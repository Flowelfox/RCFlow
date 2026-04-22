/// WorkerRegistry — owns all worker connection state and lifecycle.
///
/// Extracted from [AppState] to give it a single, testable responsibility:
/// managing the set of [WorkerConnection] instances and their configs.
///
/// AppState holds a [WorkerRegistry] instance, listens to its changes, and
/// registers callbacks so the registry can trigger pane-level side effects
/// without depending on the pane layer.
library;

import 'package:flutter/foundation.dart';

import '../models/app_notification.dart';
import '../models/session_info.dart';
import '../models/worker_config.dart';
import '../services/notification_service.dart';
import '../services/settings_service.dart';
import '../services/websocket_service.dart';
import '../services/worker_connection.dart';

// ---------------------------------------------------------------------------
// WorkerRegistry
// ---------------------------------------------------------------------------

class WorkerRegistry extends ChangeNotifier {
  final SettingsService _settings;
  final NotificationService _notifications;

  final Map<String, WorkerConnection> _workers = {};
  List<WorkerConfig> _configs = [];
  String? _defaultWorkerId;

  // Previous statuses — used to detect connection transitions for toasts.
  final Map<String, WorkerConnectionStatus> _prevStatuses = {};

  // ---------------------------------------------------------------------------
  // Callbacks — wired by AppState to implement cross-cutting concerns.
  // ---------------------------------------------------------------------------

  /// Called whenever any worker's session list changes.
  /// AppState uses this to check pending auto-loads.
  VoidCallback? onSessionsChanged;

  /// Called when a session's mainProjectPath is attached or changes.
  void Function(String sessionId, String projectPath)? onProjectPathAttached;

  /// Called when the backend reports a project_name validation error.
  void Function(String sessionId, String error)? onProjectNameError;

  /// Called when a previous project_name error is cleared.
  void Function(String sessionId)? onProjectNameErrorCleared;

  /// Receives all input-channel messages from any worker.
  void Function(Map<String, dynamic> msg, String workerId)? onInputMessage;

  /// Receives all output-channel messages from any worker.
  void Function(Map<String, dynamic> msg, String workerId)? onOutputMessage;

  // ---------------------------------------------------------------------------
  // Constructor
  // ---------------------------------------------------------------------------

  WorkerRegistry({
    required SettingsService settings,
    required NotificationService notifications,
  }) : _settings = settings,
       _notifications = notifications;

  // ---------------------------------------------------------------------------
  // Public read-only state
  // ---------------------------------------------------------------------------

  List<WorkerConfig> get configs => List.unmodifiable(_configs);

  String? get defaultWorkerId {
    if (_defaultWorkerId != null &&
        _workers[_defaultWorkerId]?.isConnected == true) {
      return _defaultWorkerId;
    }
    for (final w in _workers.values) {
      if (w.isConnected) return w.config.id;
    }
    return _configs.isNotEmpty ? _configs.first.id : null;
  }

  set defaultWorkerId(String? id) {
    _defaultWorkerId = id;
    notifyListeners();
  }

  bool get connected => _workers.values.any((w) => w.isConnected);
  bool get connecting => _workers.values.any((w) => w.isConnecting);
  bool get allConnected {
    final auto = _workers.values.where((w) => w.config.autoConnect);
    return auto.isNotEmpty && auto.every((w) => w.isConnected);
  }

  int get connectedCount => _workers.values.where((w) => w.isConnected).length;
  int get totalCount => _workers.length;
  bool get anyHasLinear => _workers.values.any((w) => w.hasLinear);

  WorkerConnection? operator [](String workerId) => _workers[workerId];
  WorkerConnection? get(String workerId) => _workers[workerId];

  Iterable<WorkerConnection> get all => _workers.values;

  /// Merged session list across all workers, sorted newest-first.
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

  /// Sessions grouped by workerId (ordered by config order).
  Map<String, List<SessionInfo>> get sessionsByWorker {
    final map = <String, List<SessionInfo>>{};
    for (final cfg in _configs) {
      map[cfg.id] = _workers[cfg.id]?.sessions ?? [];
    }
    return map;
  }

  WebSocketService wsForWorker(String workerId) {
    final worker = _workers[workerId];
    if (worker == null) throw StateError('No worker with id $workerId');
    return worker.ws;
  }

  String? workerIdForSession(String sessionId) {
    for (final w in _workers.values) {
      if (w.sessions.any((s) => s.sessionId == sessionId)) return w.config.id;
    }
    return null;
  }

  WorkerConnection? workerForSession(String sessionId) {
    for (final w in _workers.values) {
      if (w.sessions.any((s) => s.sessionId == sessionId)) return w;
    }
    return null;
  }

  bool supportsAttachments(String? workerId) {
    final id = workerId ?? defaultWorkerId;
    if (id == null) return true;
    return _workers[id]?.supportsAttachments ?? true;
  }

  bool supportsImageAttachments(String? workerId) {
    final id = workerId ?? defaultWorkerId;
    if (id == null) return true;
    return _workers[id]?.supportsImageAttachments ?? true;
  }

  // ---------------------------------------------------------------------------
  // Initialisation
  // ---------------------------------------------------------------------------

  /// Populate workers from the settings list. Called once at startup.
  void init(List<WorkerConfig> configs) {
    _configs = List.of(configs);
    for (final cfg in _configs) {
      _createConnection(cfg);
    }
  }

  // ---------------------------------------------------------------------------
  // CRUD
  // ---------------------------------------------------------------------------

  void add(WorkerConfig config) {
    _configs.add(config);
    _settings.workers = _configs;
    _createConnection(config);
    notifyListeners();
  }

  void update(WorkerConfig config) {
    final idx = _configs.indexWhere((c) => c.id == config.id);
    if (idx < 0) return;

    final old = _configs[idx];
    final needsReconnect =
        old.host != config.host ||
        old.port != config.port ||
        old.apiKey != config.apiKey ||
        old.useSSL != config.useSSL;

    _configs[idx] = config;
    _settings.workers = _configs;

    final w = _workers[config.id];
    if (w != null) {
      w.config = config;
      if (needsReconnect && w.isConnected) {
        w.disconnect();
        w.connect().catchError((_) {});
      }
    }
    notifyListeners();
  }

  Future<void> remove(String id) async {
    _configs.removeWhere((c) => c.id == id);
    _settings.workers = _configs;

    final w = _workers.remove(id);
    if (w != null) {
      w.removeListener(_onWorkerChanged);
      w.disconnect();
      w.dispose();
    }

    _settings.setLastSessionId(id, null);
    _settings.setCachedSessions(id, null);

    if (_defaultWorkerId == id) _defaultWorkerId = null;
    notifyListeners();
  }

  Future<void> connect(String id) async {
    final w = _workers[id];
    if (w == null) return;
    try {
      await w.connect();
    } catch (e) {
      _notifications.show(
        level: NotificationLevel.error,
        title: 'Connection Failed',
        body: 'Failed to connect to ${w.config.name}: $e',
      );
      rethrow;
    }
  }

  void disconnect(String id) {
    _workers[id]?.disconnect();
  }

  /// Fan out [WorkerConnection.hibernate] to every worker. Workers that are
  /// already disconnected (or were never connected) are skipped by
  /// [WorkerConnection.hibernate] itself.
  void hibernateAll() {
    for (final w in _workers.values) {
      w.hibernate();
    }
  }

  /// Fan out [WorkerConnection.wake] to every worker. Workers that are not
  /// hibernating no-op in [WorkerConnection.wake], so this is safe to call
  /// unconditionally on resume.
  Future<void> wakeAll() async {
    for (final w in _workers.values) {
      await w.wake();
    }
  }

  void refreshSessions() {
    for (final w in _workers.values) {
      w.refreshSessions();
    }
  }

  void markSubscribed(String sessionId, {required String workerId}) {
    _workers[workerId]?.subscribedSessions.add(sessionId);
  }

  void unsubscribeIfUnviewed(
    String sessionId,
    String workerId, {
    required bool Function(String sessionId) isViewed,
  }) {
    if (!isViewed(sessionId)) {
      _workers[workerId]?.unsubscribe(sessionId);
    }
  }

  // ---------------------------------------------------------------------------
  // Internal — connection creation and event handling
  // ---------------------------------------------------------------------------

  WorkerConnection _createConnection(WorkerConfig config) {
    final ws = WebSocketService();
    final w = WorkerConnection(config: config, ws: ws, settings: _settings);

    w.onInputMessage = onInputMessage;
    w.onOutputMessage = onOutputMessage;
    w.onSessionsChanged = _onSessionsChanged;
    w.onProjectPathAttached = onProjectPathAttached;
    w.onProjectNameError = onProjectNameError;
    w.onProjectNameErrorCleared = onProjectNameErrorCleared;

    w.addListener(_onWorkerChanged);
    w.loadCachedSessions();
    _workers[config.id] = w;
    _prevStatuses[config.id] = w.status;
    return w;
  }

  void _onSessionsChanged() {
    onSessionsChanged?.call();
    notifyListeners();
  }

  void _onWorkerChanged() {
    _fireConnectionToasts();
    notifyListeners();
  }

  void _fireConnectionToasts() {
    if (!_settings.toastEnabled || !_settings.toastConnections) {
      // Still track statuses so we don't fire stale events later
      for (final w in _workers.values) {
        _prevStatuses[w.config.id] = w.status;
      }
      return;
    }

    for (final w in _workers.values) {
      final prev = _prevStatuses[w.config.id];
      final curr = w.status;
      if (prev != null && prev != curr) {
        _dispatchConnectionToast(w, prev, curr);
      }
      _prevStatuses[w.config.id] = curr;
    }
  }

  void _dispatchConnectionToast(
    WorkerConnection w,
    WorkerConnectionStatus prev,
    WorkerConnectionStatus curr,
  ) {
    final name = w.config.name;

    // Suppress "Lost Connection" toasts when the disconnect was driven by
    // the mobile app going to background — the hibernation tear-down is
    // intentional and reconnects automatically on resume.
    if (w.isHibernating) return;

    if (prev == WorkerConnectionStatus.connected &&
        (curr == WorkerConnectionStatus.disconnected ||
            curr == WorkerConnectionStatus.reconnecting)) {
      _notifications.show(
        level: NotificationLevel.error,
        title: 'Lost Connection',
        body: 'Disconnected from $name',
      );
      return;
    }

    if (prev == WorkerConnectionStatus.reconnecting &&
        curr == WorkerConnectionStatus.connected) {
      _notifications.show(
        level: NotificationLevel.info,
        title: 'Reconnected',
        body: 'Connection to $name restored',
      );
      return;
    }

    if (prev == WorkerConnectionStatus.reconnecting &&
        curr == WorkerConnectionStatus.disconnected) {
      _notifications.show(
        level: NotificationLevel.error,
        title: 'Connection Lost',
        body:
            'Could not reconnect to $name after '
            '${WorkerConnection.maxRetries} attempts',
      );
    }
  }

  // ---------------------------------------------------------------------------
  // Disposal
  // ---------------------------------------------------------------------------

  @override
  void dispose() {
    for (final w in _workers.values) {
      w.removeListener(_onWorkerChanged);
      w.dispose();
    }
    super.dispose();
  }
}
