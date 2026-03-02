import 'dart:async';

import 'package:flutter/foundation.dart';

import '../models/session_info.dart';
import '../models/split_tree.dart';
import '../models/worker_config.dart';
import '../services/foreground_service.dart';
import '../services/notification_sound_service.dart';
import '../services/settings_service.dart';
import '../services/websocket_service.dart';
import '../services/worker_connection.dart';
import 'output_handlers.dart';
import 'pane_state.dart';

class AppState extends ChangeNotifier implements PaneHost {
  final SettingsService _settings;
  late final NotificationSoundService _soundService;

  // --- Workers ---
  final Map<String, WorkerConnection> _workers = {};
  List<WorkerConfig> _workerConfigs = [];
  List<WorkerConfig> get workerConfigs => List.unmodifiable(_workerConfigs);
  String? _defaultWorkerId;

  @override
  String? get defaultWorkerId {
    // Return explicitly set default, or first connected worker
    if (_defaultWorkerId != null && _workers[_defaultWorkerId]?.isConnected == true) {
      return _defaultWorkerId;
    }
    for (final w in _workers.values) {
      if (w.isConnected) return w.config.id;
    }
    return _workerConfigs.isNotEmpty ? _workerConfigs.first.id : null;
  }

  set defaultWorkerId(String? id) {
    _defaultWorkerId = id;
    notifyListeners();
  }

  // Connection state (aggregated)
  @override
  bool get connected => _workers.values.any((w) => w.isConnected);
  bool get connecting => _workers.values.any((w) => w.isConnecting);
  bool get allConnected {
    final autoWorkers = _workers.values.where((w) => w.config.autoConnect);
    return autoWorkers.isNotEmpty && autoWorkers.every((w) => w.isConnected);
  }

  int get connectedWorkerCount =>
      _workers.values.where((w) => w.isConnected).length;
  int get totalWorkerCount => _workers.length;

  WorkerConnection? getWorker(String workerId) => _workers[workerId];

  // Sessions with temporarily muted sound (during history replay after switch)
  final Set<String> _soundMutedSessions = {};

  // Merged session list (all workers)
  @override
  List<SessionInfo> get sessions {
    final all = <SessionInfo>[];
    for (final w in _workers.values) {
      all.addAll(w.sessions);
    }
    all.sort((a, b) {
      final aTime = a.createdAt ?? DateTime(2000);
      final bTime = b.createdAt ?? DateTime(2000);
      return bTime.compareTo(aTime);
    });
    return all;
  }

  /// Sessions grouped by workerId.
  Map<String, List<SessionInfo>> get sessionsByWorker {
    final map = <String, List<SessionInfo>>{};
    for (final config in _workerConfigs) {
      final worker = _workers[config.id];
      map[config.id] = worker?.sessions ?? [];
    }
    return map;
  }

  // --- Split pane state ---
  final Map<String, PaneState> _panes = {};
  Map<String, PaneState> get panes => _panes;
  SplitNode _splitRoot;
  SplitNode get splitRoot => _splitRoot;
  String _activePaneId;
  String get activePaneId => _activePaneId;
  int _nextPaneId = 1;

  PaneState get activePane => _panes[_activePaneId]!;

  SettingsService get settings => _settings;
  NotificationSoundService get soundService => _soundService;

  // Backward-compat getters delegating to active pane
  String? get currentSessionId => activePane.sessionId;
  List<dynamic> get messages => activePane.messages;
  bool get readyForNewChat => activePane.readyForNewChat;
  bool get currentSessionEnded => activePane.sessionEnded;
  bool get canSendMessage => activePane.canSendMessage;
  bool get loadingMore => activePane.loadingMore;
  bool get hasMoreMessages => activePane.hasMoreMessages;
  int get totalMessageCount => activePane.totalMessageCount;

  AppState({required SettingsService settings})
      : _settings = settings,
        _splitRoot = const PaneLeaf('pane_0'),
        _activePaneId = 'pane_0' {
    _soundService = NotificationSoundService(settings: _settings);
    _panes['pane_0'] = PaneState(paneId: 'pane_0', host: this)
      ..addListener(_onPaneChanged);

    _workerConfigs = _settings.workers;
    _initWorkers();
    _connectAutoConnectWorkers();
  }

  void _onPaneChanged() => notifyListeners();

  // --- Worker lifecycle ---

  void _initWorkers() {
    for (final config in _workerConfigs) {
      _createWorkerConnection(config);
    }
  }

  WorkerConnection _createWorkerConnection(WorkerConfig config) {
    final ws = WebSocketService();
    final worker = WorkerConnection(
      config: config,
      ws: ws,
      settings: _settings,
    );
    worker.onInputMessage = _handleInputMessage;
    worker.onOutputMessage = _handleOutputMessage;
    worker.onSessionsChanged = _onWorkerSessionsChanged;
    worker.addListener(_onWorkerChanged);
    worker.loadCachedSessions();
    _workers[config.id] = worker;
    return worker;
  }

  void _onWorkerChanged() {
    // Update foreground service: start when first connects, stop when last disconnects
    if (connected) {
      ForegroundServiceHelper.start();
    } else if (!connecting) {
      ForegroundServiceHelper.stop();
    }
    notifyListeners();
  }

  void _onWorkerSessionsChanged() {
    // Check pending auto-loads for all workers
    for (final worker in _workers.values) {
      final pendingId = worker.pendingAutoLoadSessionId;
      if (pendingId != null) {
        worker.clearPendingAutoLoad();
        final exists = worker.sessions.any((s) => s.sessionId == pendingId);
        if (exists) {
          Future.microtask(() => activePane.switchSession(pendingId));
        }
      }
    }
    notifyListeners();
  }

  Future<void> _connectAutoConnectWorkers() async {
    for (final config in _workerConfigs) {
      if (config.autoConnect && config.apiKey.isNotEmpty) {
        final worker = _workers[config.id];
        if (worker != null && !worker.isConnected && !worker.isConnecting) {
          try {
            await worker.connect();
            activePane.addSystemMessage('Connected to ${config.name}');
          } catch (e) {
            activePane.addSystemMessage(
                'Failed to connect to ${config.name}: $e',
                isError: true);
          }
        }
      }
    }
  }

  // --- Worker CRUD ---

  void addWorker(WorkerConfig config) {
    _workerConfigs.add(config);
    _settings.workers = _workerConfigs;
    _createWorkerConnection(config);
    notifyListeners();
  }

  void updateWorker(WorkerConfig config) {
    final idx = _workerConfigs.indexWhere((w) => w.id == config.id);
    if (idx < 0) return;

    final old = _workerConfigs[idx];
    final needsReconnect =
        old.host != config.host || old.apiKey != config.apiKey || old.useSSL != config.useSSL;

    _workerConfigs[idx] = config;
    _settings.workers = _workerConfigs;

    final worker = _workers[config.id];
    if (worker != null) {
      worker.config = config;
      if (needsReconnect && worker.isConnected) {
        worker.disconnect();
        worker.connect().catchError((_) {});
      }
    }
    notifyListeners();
  }

  Future<void> removeWorker(String id) async {
    _workerConfigs.removeWhere((w) => w.id == id);
    _settings.workers = _workerConfigs;

    final worker = _workers.remove(id);
    if (worker != null) {
      worker.removeListener(_onWorkerChanged);
      worker.disconnect();
      worker.dispose();
    }

    // Clear per-worker settings
    _settings.setLastSessionId(id, null);
    _settings.setCachedSessions(id, null);

    if (_defaultWorkerId == id) _defaultWorkerId = null;
    notifyListeners();
  }

  Future<void> connectWorker(String id) async {
    final worker = _workers[id];
    if (worker == null) return;
    try {
      await worker.connect();
      activePane.addSystemMessage('Connected to ${worker.config.name}');
    } catch (e) {
      activePane.addSystemMessage(
          'Failed to connect to ${worker.config.name}: $e',
          isError: true);
    }
  }

  void disconnectWorker(String id) {
    final worker = _workers[id];
    if (worker == null) return;
    worker.disconnect();
    activePane.addSystemMessage('Disconnected from ${worker.config.name}');
  }

  // --- PaneHost interface ---

  @override
  WebSocketService wsForWorker(String workerId) {
    final worker = _workers[workerId];
    if (worker == null) {
      throw StateError('No worker with id $workerId');
    }
    return worker.ws;
  }

  @override
  String? workerIdForSession(String sessionId) {
    for (final worker in _workers.values) {
      if (worker.sessions.any((s) => s.sessionId == sessionId)) {
        return worker.config.id;
      }
    }
    return null;
  }

  @override
  void refreshSessions() {
    for (final worker in _workers.values) {
      worker.refreshSessions();
    }
  }

  @override
  void markSubscribed(String sessionId, {required String workerId}) {
    final worker = _workers[workerId];
    worker?.subscribedSessions.add(sessionId);
  }

  @override
  void requestUnsubscribe(String sessionId, String workerId) {
    // Only unsubscribe from the server if no other pane still views this session
    final stillViewed = _panes.values.any((p) => p.sessionId == sessionId);
    if (!stillViewed) {
      _workers[workerId]?.unsubscribe(sessionId);
    }
  }

  @override
  void muteSessionSound(String sessionId) {
    _soundMutedSessions.add(sessionId);
    Future.delayed(const Duration(seconds: 3), () {
      _soundMutedSessions.remove(sessionId);
    });
  }

  @override
  void addSystemMessageToPane(String paneId, String text,
      {bool isError = false, String? label}) {
    final pane = _panes[paneId];
    if (pane == null) return;
    pane.addSystemMessage(
      label != null ? '[$label] $text' : text,
      isError: isError,
    );
  }

  void addSystemMessage(String text, {bool isError = false, String? label}) {
    activePane.addSystemMessage(
      label != null ? '[$label] $text' : text,
      isError: isError,
    );
  }

  // --- Split operations ---

  void splitPane(String paneId, SplitAxis axis) {
    if (!_panes.containsKey(paneId)) return;
    final newId = 'pane_${_nextPaneId++}';
    final newPane = PaneState(paneId: newId, host: this)
      ..addListener(_onPaneChanged);
    _panes[newId] = newPane;
    _splitRoot = treeSplitPane(_splitRoot, paneId, newId, axis);
    _activePaneId = newId;
    notifyListeners();
  }

  /// Split [paneId] using a [DropZone] and immediately switch the new pane
  /// to [sessionId].
  void splitPaneWithSession(
      String paneId, DropZone zone, String sessionId) {
    if (!_panes.containsKey(paneId)) return;
    final newId = 'pane_${_nextPaneId++}';
    final newPane = PaneState(paneId: newId, host: this)
      ..addListener(_onPaneChanged);
    _panes[newId] = newPane;
    _splitRoot = treeSplitPaneAtPosition(
      _splitRoot,
      paneId,
      newId,
      dropZoneAxis(zone),
      insertFirst: dropZoneIsFirst(zone),
    );
    _activePaneId = newId;
    newPane.switchSession(sessionId);
    notifyListeners();
  }

  void closePane(String paneId) {
    if (_panes.length <= 1) {
      _panes[paneId]!.goHome();
      return;
    }
    final newRoot = treeClosePane(_splitRoot, paneId);
    if (newRoot == null) return;

    final pane = _panes.remove(paneId);
    final closedSessionId = pane?.sessionId;
    final closedWorkerId = pane?.workerId;
    pane?.removeListener(_onPaneChanged);
    pane?.dispose();
    _splitRoot = newRoot;

    if (_activePaneId == paneId) {
      _activePaneId = allPaneIds(_splitRoot).first;
    }

    // Unsubscribe from the closed pane's session if no other pane views it
    if (closedSessionId != null && closedWorkerId != null) {
      requestUnsubscribe(closedSessionId, closedWorkerId);
    }

    notifyListeners();
  }

  void setActivePane(String paneId) {
    if (_activePaneId == paneId || !_panes.containsKey(paneId)) return;
    _activePaneId = paneId;
    final sid = activePane.sessionId;
    final wid = activePane.workerId;
    if (sid != null && wid != null) {
      _settings.setLastSessionId(wid, sid);
    }
    notifyListeners();
  }

  void updateSplitRatio(SplitBranch branch, double ratio) {
    branch.ratio = ratio.clamp(0.15, 0.85);
    notifyListeners();
  }

  int get paneCount => treePaneCount(_splitRoot);

  // --- Pane routing helpers ---

  List<PaneState> _findPanesForSession(String sessionId) {
    return _panes.values.where((p) => p.sessionId == sessionId).toList();
  }

  PaneState? _findPaneWithPendingAck() {
    for (final pane in _panes.values) {
      if (pane.pendingAck) return pane;
    }
    return null;
  }

  bool isSessionViewed(String sessionId) {
    return _panes.values.any((p) => p.sessionId == sessionId);
  }

  // --- Input channel message handling ---

  void _handleInputMessage(Map<String, dynamic> msg, String workerId) {
    final type = msg['type'] as String?;
    switch (type) {
      case 'ack':
        final sessionId = msg['session_id'] as String;
        final pane = _findPaneWithPendingAck() ?? activePane;
        pane.handleAck(sessionId, workerId: workerId);
        final worker = _workers[workerId];
        if (worker != null && !worker.subscribedSessions.contains(sessionId)) {
          worker.subscribe(sessionId);
        }
        _settings.setLastSessionId(workerId, sessionId);
        _workers[workerId]?.refreshSessions();
        break;
      case 'error':
        activePane.finalizeStream();
        activePane.addSystemMessage(
            msg['content'] as String? ?? 'Unknown error',
            isError: true);
        break;
    }
  }

  // --- Output channel message handling ---

  static const _completionSoundTypes = {
    'summary',
    'session_end_ask',
    'plan_mode_ask',
    'plan_review_ask',
  };

  static const _messageSoundTypes = {
    'summary',
    'session_end_ask',
    'error',
    'plan_mode_ask',
    'plan_review_ask',
  };

  void _handleOutputMessage(Map<String, dynamic> msg, String workerId) {
    final type = msg['type'] as String?;
    if (type == null) return;

    final sessionId = msg['session_id'] as String?;
    final muted = sessionId != null && _soundMutedSessions.contains(sessionId);
    if (!muted) {
      final playForComplete = _settings.soundOnCompleteEnabled &&
          _completionSoundTypes.contains(type);
      final playForMessage =
          _settings.soundEnabled && _messageSoundTypes.contains(type);
      if (playForComplete || playForMessage) {
        _soundService.playNotificationSound();
      }
    }

    final handler = outputHandlerRegistry[type];
    if (handler == null) {
      activePane.addSystemMessage(msg.toString());
      return;
    }

    if (sessionId != null) {
      final targetPanes = _findPanesForSession(sessionId);
      for (final pane in targetPanes) {
        handler(msg, pane);
      }
      return;
    }

    handler(msg, activePane);
  }

  @override
  void dispose() {
    for (final worker in _workers.values) {
      worker.removeListener(_onWorkerChanged);
      worker.dispose();
    }
    _workers.clear();
    for (final pane in _panes.values) {
      pane.removeListener(_onPaneChanged);
      pane.dispose();
    }
    _panes.clear();
    _soundService.dispose();
    ForegroundServiceHelper.stop();
    super.dispose();
  }
}

// Aliases to avoid name collision with the splitPane method on AppState
SplitNode treeSplitPane(
        SplitNode node, String targetId, String newPaneId, SplitAxis axis) =>
    splitPane(node, targetId, newPaneId, axis);

SplitNode? treeClosePane(SplitNode node, String targetId) =>
    closePane(node, targetId);

int treePaneCount(SplitNode node) => paneCount(node);

SplitNode treeSplitPaneAtPosition(SplitNode node, String targetId,
        String newPaneId, SplitAxis axis, {required bool insertFirst}) =>
    splitPaneAtPosition(node, targetId, newPaneId, axis,
        insertFirst: insertFirst);
