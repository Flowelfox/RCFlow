import 'dart:async';

import 'package:flutter/widgets.dart';

import 'dart:math';

import '../models/session_info.dart';
import '../models/split_tree.dart';
import '../models/worker_config.dart';
import '../services/foreground_service.dart';
import '../services/notification_sound_service.dart';
import '../services/settings_service.dart';
import '../services/websocket_service.dart';
import '../services/worker_connection.dart';
import '../ui/widgets/terminal_pane.dart';
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

  // --- Hide terminal sessions ---

  bool get hideTerminalSessions => _settings.hideTerminalSessions;

  void toggleHideTerminalSessions() {
    _settings.hideTerminalSessions = !_settings.hideTerminalSessions;
    notifyListeners();
  }

  // --- Appearance settings (need notifyListeners for reactive rebuild) ---

  void updateAppearance({String? themeMode, String? fontSize, bool? compactMode}) {
    if (themeMode != null) _settings.themeMode = themeMode;
    if (fontSize != null) _settings.fontSize = fontSize;
    if (compactMode != null) _settings.compactMode = compactMode;
    notifyListeners();
  }

  static const _terminalStatuses = {'completed', 'failed', 'cancelled'};

  /// Sessions grouped by workerId.
  Map<String, List<SessionInfo>> get sessionsByWorker {
    final hide = hideTerminalSessions;
    final map = <String, List<SessionInfo>>{};
    for (final config in _workerConfigs) {
      final worker = _workers[config.id];
      var sessions = worker?.sessions ?? <SessionInfo>[];
      if (hide) {
        sessions = sessions.where((s) {
          if (!_terminalStatuses.contains(s.status)) return true;
          return isSessionViewed(s.sessionId);
        }).toList();
      }
      map[config.id] = sessions;
    }
    return map;
  }

  // --- Split pane state ---
  final Map<String, PaneState> _panes = {};
  Map<String, PaneState> get panes => _panes;
  SplitNode? _splitRoot;
  SplitNode? get splitRoot => _splitRoot;
  String _activePaneId;
  String get activePaneId => _activePaneId;
  int _nextPaneId = 1;
  bool get hasNoPanes => _panes.isEmpty;

  // --- Terminal session tracking ---
  /// All terminal sessions keyed by terminalId. Survives pane close/reopen.
  final Map<String, TerminalSessionInfo> _terminalSessions = {};
  Map<String, TerminalSessionInfo> get terminalSessions =>
      Map.unmodifiable(_terminalSessions);

  final Map<String, PaneType> _paneTypes = {};
  final Map<String, GlobalKey> _terminalPaneKeys = {};

  /// Returns a stable [GlobalKey] for the [TerminalPane] widget in a given pane.
  GlobalKey terminalPaneKey(String paneId) =>
      _terminalPaneKeys.putIfAbsent(paneId, () => GlobalKey());

  PaneType getPaneType(String paneId) =>
      _paneTypes[paneId] ?? PaneType.chat;

  /// Find the terminal session info attached to a given pane.
  TerminalSessionInfo? getTerminalPaneInfo(String paneId) {
    for (final info in _terminalSessions.values) {
      if (info.paneId == paneId) return info;
    }
    return null;
  }

  /// Terminal sessions grouped by workerId (for sidebar display).
  Map<String, List<TerminalSessionInfo>> get terminalsByWorker {
    final map = <String, List<TerminalSessionInfo>>{};
    for (final info in _terminalSessions.values) {
      map.putIfAbsent(info.workerId, () => []).add(info);
    }
    for (final list in map.values) {
      list.sort((a, b) => b.createdAt.compareTo(a.createdAt));
    }
    return map;
  }

  PaneState get activePane {
    assert(_panes.isNotEmpty, 'No panes available');
    return _panes[_activePaneId]!;
  }

  SettingsService get settings => _settings;
  NotificationSoundService get soundService => _soundService;

  // Backward-compat getters delegating to active pane
  String? get currentSessionId => hasNoPanes ? null : activePane.sessionId;
  List<dynamic> get messages => hasNoPanes ? [] : activePane.messages;
  bool get readyForNewChat => hasNoPanes ? false : activePane.readyForNewChat;
  bool get currentSessionEnded => hasNoPanes ? false : activePane.sessionEnded;
  bool get canSendMessage => hasNoPanes ? false : activePane.canSendMessage;
  bool get loadingMore => hasNoPanes ? false : activePane.loadingMore;
  bool get hasMoreMessages => hasNoPanes ? false : activePane.hasMoreMessages;
  int get totalMessageCount => hasNoPanes ? 0 : activePane.totalMessageCount;

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
    if (!hasNoPanes) {
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
            if (!hasNoPanes) {
              activePane.addSystemMessage('Connected to ${config.name}');
            }
          } catch (e) {
            if (!hasNoPanes) {
              activePane.addSystemMessage(
                  'Failed to connect to ${config.name}: $e',
                  isError: true);
            }
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
      if (!hasNoPanes) {
        activePane.addSystemMessage('Connected to ${worker.config.name}');
      }
    } catch (e) {
      if (!hasNoPanes) {
        activePane.addSystemMessage(
            'Failed to connect to ${worker.config.name}: $e',
            isError: true);
      }
    }
  }

  void disconnectWorker(String id) {
    final worker = _workers[id];
    if (worker == null) return;
    worker.disconnect();
    if (!hasNoPanes) {
      activePane.addSystemMessage('Disconnected from ${worker.config.name}');
    }
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
    if (hasNoPanes) return;
    activePane.addSystemMessage(
      label != null ? '[$label] $text' : text,
      isError: isError,
    );
  }

  // --- Split operations ---

  void splitPane(String paneId, SplitAxis axis) {
    if (_splitRoot == null || !_panes.containsKey(paneId)) return;
    final newId = 'pane_${_nextPaneId++}';
    final newPane = PaneState(paneId: newId, host: this)
      ..addListener(_onPaneChanged);
    _panes[newId] = newPane;
    _splitRoot = treeSplitPane(_splitRoot!, paneId, newId, axis);
    _activePaneId = newId;
    notifyListeners();
  }

  /// Split [paneId] using a [DropZone] and immediately switch the new pane
  /// to [sessionId].
  void splitPaneWithSession(
      String paneId, DropZone zone, String sessionId) {
    if (_splitRoot == null || !_panes.containsKey(paneId)) return;
    final newId = 'pane_${_nextPaneId++}';
    final newPane = PaneState(paneId: newId, host: this)
      ..addListener(_onPaneChanged);
    _panes[newId] = newPane;
    _splitRoot = treeSplitPaneAtPosition(
      _splitRoot!,
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
    if (_splitRoot == null || !_panes.containsKey(paneId)) return;

    // For terminal panes: detach but keep the session alive
    if (_paneTypes[paneId] == PaneType.terminal) {
      for (final info in _terminalSessions.values) {
        if (info.paneId == paneId) {
          info.paneId = null; // Detach from pane, PTY stays alive
          break;
        }
      }
    }

    _paneTypes.remove(paneId);
    _terminalPaneKeys.remove(paneId);

    final newRoot = treeClosePane(_splitRoot!, paneId);

    final pane = _panes.remove(paneId);
    final closedSessionId = pane?.sessionId;
    final closedWorkerId = pane?.workerId;
    pane?.removeListener(_onPaneChanged);
    pane?.dispose();
    _splitRoot = newRoot; // null when last pane is closed

    if (newRoot != null && _activePaneId == paneId) {
      _activePaneId = allPaneIds(newRoot).first;
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
    if (_paneTypes[paneId] != PaneType.terminal && !hasNoPanes) {
      final sid = activePane.sessionId;
      final wid = activePane.workerId;
      if (sid != null && wid != null) {
        _settings.setLastSessionId(wid, sid);
      }
    }
    notifyListeners();
  }

  void updateSplitRatio(SplitBranch branch, double ratio) {
    branch.ratio = ratio.clamp(0.15, 0.85);
    notifyListeners();
  }

  int get paneCount => _splitRoot != null ? treePaneCount(_splitRoot!) : 0;

  /// Returns a non-terminal [PaneState], creating one via split if necessary.
  /// Use this when an action (e.g. "New Session", session tap) should target a
  /// chat pane but the active pane is a terminal.
  PaneState ensureChatPane() {
    // No panes at all — create a fresh one (welcome screen → first pane).
    if (hasNoPanes) return createNewPane();

    // If the active pane is already a chat pane, return it.
    if (_paneTypes[_activePaneId] != PaneType.terminal) {
      return activePane;
    }

    // Active pane is a terminal — convert it to a chat pane in-place.
    // Detach the terminal session (PTY stays alive server-side).
    for (final info in _terminalSessions.values) {
      if (info.paneId == _activePaneId) {
        info.paneId = null;
        break;
      }
    }
    _paneTypes.remove(_activePaneId);
    _terminalPaneKeys.remove(_activePaneId);
    notifyListeners();
    return activePane;
  }

  /// Create a brand-new pane from empty state (e.g. from the welcome screen).
  PaneState createNewPane() {
    final newId = 'pane_${_nextPaneId++}';
    final newPane = PaneState(paneId: newId, host: this)
      ..addListener(_onPaneChanged);
    _panes[newId] = newPane;
    _splitRoot = PaneLeaf(newId);
    _activePaneId = newId;
    notifyListeners();
    return newPane;
  }

  /// Open a new terminal in the active pane (converting it in-place),
  /// or create a fresh pane if none exist.
  void openTerminal(String workerId) {
    final terminalId = _generateUuid();

    // If there's an active pane, convert it in-place
    if (_splitRoot != null && _panes.containsKey(_activePaneId)) {
      // Detach any existing terminal from this pane
      if (_paneTypes[_activePaneId] == PaneType.terminal) {
        for (final other in _terminalSessions.values) {
          if (other.paneId == _activePaneId) {
            other.paneId = null;
            break;
          }
        }
      }

      final info = TerminalSessionInfo(
        terminalId: terminalId,
        workerId: workerId,
        title: 'Terminal',
        maxLines: _settings.terminalScrollback,
        paneId: _activePaneId,
      );
      _terminalSessions[terminalId] = info;
      _paneTypes[_activePaneId] = PaneType.terminal;
      _terminalPaneKeys[_activePaneId] = GlobalKey();
      notifyListeners();
      return;
    }

    // No panes at all — create a fresh one
    final newId = 'pane_${_nextPaneId++}';
    final info = TerminalSessionInfo(
      terminalId: terminalId,
      workerId: workerId,
      title: 'Terminal',
      maxLines: _settings.terminalScrollback,
      paneId: newId,
    );
    _terminalSessions[terminalId] = info;

    final newPane = PaneState(paneId: newId, host: this)
      ..addListener(_onPaneChanged);
    _panes[newId] = newPane;
    _paneTypes[newId] = PaneType.terminal;

    _splitRoot = PaneLeaf(newId);
    _activePaneId = newId;
    notifyListeners();
  }

  /// Show an existing terminal session in a pane.
  /// If already visible, switch active pane to it. Otherwise convert the
  /// active pane in-place (or create a new pane if none exist).
  void showTerminalInPane(String terminalId) {
    final info = _terminalSessions[terminalId];
    if (info == null) return;

    // If already shown in a pane, just focus it
    if (info.paneId != null && _panes.containsKey(info.paneId)) {
      _activePaneId = info.paneId!;
      notifyListeners();
      return;
    }

    // Convert the active pane in-place to show this terminal
    if (_splitRoot != null && _panes.containsKey(_activePaneId)) {
      // If the active pane is currently showing a different terminal, detach it
      if (_paneTypes[_activePaneId] == PaneType.terminal) {
        for (final other in _terminalSessions.values) {
          if (other.paneId == _activePaneId) {
            other.paneId = null;
            break;
          }
        }
      }

      info.paneId = _activePaneId;
      _paneTypes[_activePaneId] = PaneType.terminal;
      _terminalPaneKeys[_activePaneId] = GlobalKey();
      notifyListeners();
      return;
    }

    // No panes at all — create a fresh one
    final newId = 'pane_${_nextPaneId++}';
    info.paneId = newId;

    final newPane = PaneState(paneId: newId, host: this)
      ..addListener(_onPaneChanged);
    _panes[newId] = newPane;
    _paneTypes[newId] = PaneType.terminal;

    _splitRoot = PaneLeaf(newId);
    _activePaneId = newId;
    notifyListeners();
  }

  /// Actually kill a terminal session (sends close command to server).
  void closeTerminalSession(String terminalId) {
    final info = _terminalSessions.remove(terminalId);
    if (info == null) return;

    // Send close command to server if still connected
    if (info.connected && !info.ended) {
      final worker = _workers[info.workerId];
      if (worker != null) {
        final service = worker.terminalService;
        service.sendControl({
          'type': 'close',
          'terminal_id': terminalId,
        });
      }
    }

    // Clean up subscriptions
    info.outputSub?.cancel();
    info.controlSub?.cancel();

    // Close the pane if it's currently visible
    if (info.paneId != null && _panes.containsKey(info.paneId)) {
      closePane(info.paneId!);
    }

    final service = _workers[info.workerId]?.terminalService;
    service?.unregisterTerminal(terminalId);

    notifyListeners();
  }

  /// Rename a terminal session.
  void renameTerminal(String terminalId, String newTitle) {
    final info = _terminalSessions[terminalId];
    if (info == null) return;
    info.title = newTitle.isEmpty ? 'Terminal' : newTitle;
    notifyListeners();
  }

  /// Split a pane with a terminal via drag-and-drop.
  void splitPaneWithTerminal(
      String paneId, DropZone zone, String terminalId) {
    if (_splitRoot == null || !_panes.containsKey(paneId)) return;
    final info = _terminalSessions[terminalId];
    if (info == null) return;

    // If terminal is already in a pane, close that pane first
    if (info.paneId != null && _panes.containsKey(info.paneId)) {
      closePane(info.paneId!);
    }

    final newId = 'pane_${_nextPaneId++}';
    info.paneId = newId;

    final newPane = PaneState(paneId: newId, host: this)
      ..addListener(_onPaneChanged);
    _panes[newId] = newPane;
    _paneTypes[newId] = PaneType.terminal;

    _splitRoot = treeSplitPaneAtPosition(
      _splitRoot!,
      paneId,
      newId,
      dropZoneAxis(zone),
      insertFirst: dropZoneIsFirst(zone),
    );
    _activePaneId = newId;
    notifyListeners();
  }

  static String _generateUuid() {
    final rng = Random.secure();
    final bytes = List.generate(16, (_) => rng.nextInt(256));
    bytes[6] = (bytes[6] & 0x0F) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3F) | 0x80; // variant 1
    final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
    return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
        '${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
  }

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
    if (hasNoPanes) return;
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

    if (!hasNoPanes) handler(msg, activePane);
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
