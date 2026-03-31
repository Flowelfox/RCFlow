import 'dart:async';

import 'package:flutter/widgets.dart';

import 'dart:math';

import '../models/artifact_info.dart';
import '../models/linear_issue_info.dart';
import '../models/session_info.dart';
import '../models/split_tree.dart';
import '../models/task_info.dart';
import '../models/worker_config.dart';
import '../models/app_notification.dart';
import '../services/foreground_service.dart';
import '../services/hotkey_service.dart';
import '../services/notification_service.dart';
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
  late final NotificationService _notificationService;
  late final HotkeyService _hotkeyService;

  // Previous worker statuses for detecting transitions
  final Map<String, WorkerConnectionStatus> _prevWorkerStatuses = {};

  // Sidebar visibility (toggled via hotkey)
  bool _sidebarVisible = true;
  bool get sidebarVisible => _sidebarVisible;

  void toggleSidebar() {
    _sidebarVisible = !_sidebarVisible;
    notifyListeners();
  }

  // Input focus request notifier (incremented to signal focus request)
  final ValueNotifier<int> inputFocusRequest = ValueNotifier(0);

  void requestInputFocus() {
    inputFocusRequest.value++;
  }

  // --- Workers ---
  final Map<String, WorkerConnection> _workers = {};
  List<WorkerConfig> _workerConfigs = [];
  List<WorkerConfig> get workerConfigs => List.unmodifiable(_workerConfigs);
  String? _defaultWorkerId;

  @override
  String? get defaultWorkerId {
    // Return explicitly set default, or first connected worker
    if (_defaultWorkerId != null &&
        _workers[_defaultWorkerId]?.isConnected == true) {
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

  /// True if at least one worker has a Linear API key configured.
  bool get anyWorkerHasLinear => _workers.values.any((w) => w.hasLinear);

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

  // --- Appearance settings (need notifyListeners for reactive rebuild) ---

  void updateAppearance({
    String? themeMode,
    String? fontSize,
    bool? compactMode,
  }) {
    if (themeMode != null) _settings.themeMode = themeMode;
    if (fontSize != null) _settings.fontSize = fontSize;
    if (compactMode != null) _settings.compactMode = compactMode;
    notifyListeners();
  }

  /// Sessions grouped by workerId.
  Map<String, List<SessionInfo>> get sessionsByWorker {
    final map = <String, List<SessionInfo>>{};
    for (final config in _workerConfigs) {
      final worker = _workers[config.id];
      map[config.id] = worker?.sessions ?? <SessionInfo>[];
    }
    return map;
  }

  // --- Session actions (routed by session's own workerId) ---

  /// Get the [WebSocketService] for a session's owning worker.
  WebSocketService? _wsForSession(String workerId) {
    final worker = _workers[workerId];
    if (worker == null || !worker.isConnected) return null;
    return worker.ws;
  }

  Future<void> pauseSessionDirect(String sessionId, String workerId) async {
    try {
      await _wsForSession(workerId)?.pauseSession(sessionId);
      refreshSessions();
    } catch (e) {
      addSystemMessage('Failed to pause session: $e', isError: true);
    }
  }

  Future<void> resumeSessionDirect(String sessionId, String workerId) async {
    try {
      await _wsForSession(workerId)?.resumeSession(sessionId);
      refreshSessions();
    } catch (e) {
      addSystemMessage('Failed to resume session: $e', isError: true);
    }
  }

  // Track session IDs ended by the user to suppress duplicate notifications.
  final Set<String> _userEndedSessionIds = {};

  Future<void> cancelSessionDirect(String sessionId, String workerId) async {
    try {
      _userEndedSessionIds.add(sessionId);
      await _wsForSession(workerId)?.cancelSession(sessionId);
      // If any pane is viewing this session, clean it up
      for (final pane in _panes.values) {
        if (pane.sessionId == sessionId) {
          pane.finalizeStream();
          pane.goHome();
        }
      }
      _notificationService.show(
        level: NotificationLevel.info,
        title: 'Session Ended',
      );
      refreshSessions();
    } catch (e) {
      addSystemMessage('Failed to cancel session: $e', isError: true);
    }
  }

  Future<void> restoreSessionDirect(String sessionId, String workerId) async {
    try {
      await _wsForSession(workerId)?.restoreSession(sessionId);
      // Update any panes currently viewing this session
      final viewingPanes = _findPanesForSession(sessionId);
      if (viewingPanes.isNotEmpty) {
        for (final pane in viewingPanes) {
          pane.handleSessionRestored(sessionId);
        }
        // Resubscribe to session output (once, not per-pane)
        _wsForSession(workerId)?.subscribe(sessionId);
        markSubscribed(sessionId, workerId: workerId);
      }
      refreshSessions();
    } catch (e) {
      addSystemMessage('Failed to restore session: $e', isError: true);
    }
  }

  Future<void> renameSessionDirect(
    String sessionId,
    String workerId,
    String newTitle,
  ) async {
    final title = newTitle.trim().isEmpty ? null : newTitle.trim();
    try {
      await _wsForSession(workerId)?.renameSession(sessionId, title);
      refreshSessions();
    } catch (e) {
      addSystemMessage('Failed to rename session: $e', isError: true);
    }
  }

  // --- Closed pane history (for reopen-last-closed) ---
  static const _maxClosedPaneHistory = 30;
  final List<_ClosedPaneRecord> _closedPaneHistory = [];
  bool get hasClosedPaneHistory => _closedPaneHistory.isNotEmpty;

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

  PaneType getPaneType(String paneId) => _paneTypes[paneId] ?? PaneType.chat;

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

  // --- Task tracking ---
  final Map<String, TaskInfo> _tasks = {};

  /// All tasks sorted by updatedAt descending.
  List<TaskInfo> get tasks {
    final all = _tasks.values.toList();
    all.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    return all;
  }

  /// Tasks grouped by workerId (for sidebar display).
  Map<String, List<TaskInfo>> get tasksByWorker {
    final map = <String, List<TaskInfo>>{};
    for (final config in _workerConfigs) {
      map[config.id] = [];
    }
    for (final t in _tasks.values) {
      map.putIfAbsent(t.workerId, () => []).add(t);
    }
    for (final list in map.values) {
      list.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    }
    return map;
  }

  TaskInfo? getTask(String taskId) => _tasks[taskId];

  /// Check if a session is attached to any task (for sidebar indicator).
  bool isSessionAttachedToTask(String sessionId) {
    return _tasks.values.any(
      (t) => t.sessions.any((s) => s.sessionId == sessionId),
    );
  }

  /// Get all tasks that a session is attached to.
  List<TaskInfo> tasksForSession(String sessionId) {
    return _tasks.values
        .where((t) => t.sessions.any((s) => s.sessionId == sessionId))
        .toList();
  }

  void _handleTaskList(List<dynamic> list, String workerId) {
    // Remove existing tasks from this worker
    _tasks.removeWhere((_, t) => t.workerId == workerId);
    final workerName = _workerConfigs
        .firstWhere((w) => w.id == workerId, orElse: () => _workerConfigs.first)
        .name;
    for (final raw in list) {
      final t = TaskInfo.fromJson(
        raw as Map<String, dynamic>,
        workerId: workerId,
        workerName: workerName,
      );
      _tasks[t.taskId] = t;
    }
    notifyListeners();
  }

  void _handleTaskUpdate(Map<String, dynamic> msg, String workerId) {
    final taskId = msg['task_id'] as String?;
    if (taskId == null) return;
    final workerName = _workerConfigs
        .firstWhere((w) => w.id == workerId, orElse: () => _workerConfigs.first)
        .name;
    final existing = _tasks[taskId];
    final updated = TaskInfo.fromJson(
      msg,
      workerId: workerId,
      workerName: workerName,
    );
    _tasks[taskId] = updated;

    // N3: Task created (new task ID)
    if (existing == null) {
      _showToast(
        level: NotificationLevel.info,
        title: 'Task Created',
        body: updated.title,
        category: _ToastCategory.task,
        onAction: () => openTaskInPane(taskId),
      );
    }
    // N2: Task status changed
    else if (existing.status != updated.status) {
      _showToast(
        level: NotificationLevel.warning,
        title: 'Task Status Changed',
        body: '${updated.title}: ${existing.status} \u2192 ${updated.status}',
        category: _ToastCategory.task,
        onAction: () => openTaskInPane(taskId),
      );
    }

    notifyListeners();
  }

  void _handleTaskDeleted(Map<String, dynamic> msg) {
    final taskId = msg['task_id'] as String?;
    if (taskId == null) return;
    _tasks.remove(taskId);

    // Close any pane currently viewing this task
    for (final entry in _panes.entries.toList()) {
      if (_paneTypes[entry.key] == PaneType.task &&
          entry.value.taskId == taskId) {
        closeTaskView(entry.key);
      }
    }

    notifyListeners();
  }

  // --- Artifact tracking ---
  final Map<String, ArtifactInfo> _artifacts = {};

  /// All artifacts sorted by discoveredAt descending.
  List<ArtifactInfo> get artifacts {
    final all = _artifacts.values.toList();
    all.sort((a, b) {
      final aTime = a.discoveredAt ?? DateTime(1970);
      final bTime = b.discoveredAt ?? DateTime(1970);
      return bTime.compareTo(aTime);
    });
    return all;
  }

  /// Get a single artifact by ID.
  ArtifactInfo? getArtifact(String artifactId) => _artifacts[artifactId];

  /// Load artifacts from all connected workers.
  void loadArtifacts() {
    for (final worker in _workers.values) {
      if (worker.isConnected) {
        worker.ws.requestArtifacts();
      }
    }
  }

  void _handleArtifactList(List<dynamic> list, String workerId) {
    // Remove existing artifacts from this worker
    _artifacts.removeWhere((_, a) => a.workerId == workerId);
    final workerName = _workerConfigs
        .firstWhere((w) => w.id == workerId, orElse: () => _workerConfigs.first)
        .name;
    for (final raw in list) {
      final a = ArtifactInfo.fromJson(
        raw as Map<String, dynamic>,
        workerId: workerId,
        workerName: workerName,
      );
      _artifacts[a.artifactId] = a;
    }
    notifyListeners();
  }

  void _handleArtifactUpdate(Map<String, dynamic> msg, String workerId) {
    final artifactId = msg['artifact_id'] as String?;
    if (artifactId == null) return;
    final workerName = _workerConfigs
        .firstWhere((w) => w.id == workerId, orElse: () => _workerConfigs.first)
        .name;
    final updated = ArtifactInfo.fromJson(
      msg,
      workerId: workerId,
      workerName: workerName,
    );
    _artifacts[artifactId] = updated;
    notifyListeners();
  }

  void _handleArtifactDeleted(Map<String, dynamic> msg) {
    final artifactId = msg['artifact_id'] as String?;
    if (artifactId == null) return;
    _artifacts.remove(artifactId);

    // Close any pane currently viewing this artifact
    for (final entry in _panes.entries.toList()) {
      if (_paneTypes[entry.key] == PaneType.artifact &&
          entry.value.artifactId == artifactId) {
        closeArtifactView(entry.key);
      }
    }

    notifyListeners();
  }

  // --- Project panel data cache ---

  /// Cached worktree/artifact lists per `'workerId:projectPath'` key.
  /// Populated by [ProjectPanel] after each successful fetch so that reopening
  /// the panel shows the last-known data immediately while a fresh fetch runs.
  final Map<
    String,
    ({
      List<Map<String, dynamic>>? worktrees,
      List<Map<String, dynamic>>? artifacts,
    })
  >
  _projectDataCache = {};

  ({
    List<Map<String, dynamic>>? worktrees,
    List<Map<String, dynamic>>? artifacts,
  })?
  getProjectDataCache(String key) => _projectDataCache[key];

  void setProjectDataCache(
    String key, {
    List<Map<String, dynamic>>? worktrees,
    List<Map<String, dynamic>>? artifacts,
  }) {
    final existing = _projectDataCache[key];
    _projectDataCache[key] = (
      worktrees: worktrees ?? existing?.worktrees,
      artifacts: artifacts ?? existing?.artifacts,
    );
  }

  /// Show an artifact in the active pane (converting it in-place).
  void openArtifactInPane(String artifactId) {
    if (_splitRoot != null && _panes.containsKey(_activePaneId)) {
      // Push current view to nav history
      activePane.pushNavHistory(_currentNavEntry(_activePaneId));

      // Detach any existing terminal from this pane
      if (_paneTypes[_activePaneId] == PaneType.terminal) {
        for (final info in _terminalSessions.values) {
          if (info.paneId == _activePaneId) {
            info.paneId = null;
            break;
          }
        }
      }
      _paneTypes[_activePaneId] = PaneType.artifact;
      activePane.setArtifactId(artifactId);
      notifyListeners();
      return;
    }

    // No panes — create one
    final newId = 'pane_${_nextPaneId++}';
    final newPane = PaneState(paneId: newId, host: this)
      ..addListener(_onPaneChanged);
    _panes[newId] = newPane;
    _paneTypes[newId] = PaneType.artifact;
    _splitRoot = PaneLeaf(newId);
    _activePaneId = newId;
    newPane.setArtifactId(artifactId);
    notifyListeners();
  }

  /// Close artifact view for a pane (convert to session).
  void closeArtifactView(String paneId) {
    if (_paneTypes[paneId] != PaneType.artifact) return;
    _paneTypes.remove(paneId);
    _panes[paneId]?.clearArtifactId();
    notifyListeners();
  }

  // --- Linear issue tracking ---
  final Map<String, LinearIssueInfo> _linearIssues = {};

  /// All Linear issues sorted by updatedAt descending.
  List<LinearIssueInfo> get linearIssues {
    final all = _linearIssues.values.toList();
    all.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    return all;
  }

  /// Linear issues grouped by workerId (for sidebar display).
  Map<String, List<LinearIssueInfo>> get linearIssuesByWorker {
    final map = <String, List<LinearIssueInfo>>{};
    for (final config in _workerConfigs) {
      map[config.id] = [];
    }
    for (final i in _linearIssues.values) {
      map.putIfAbsent(i.workerId, () => []).add(i);
    }
    for (final list in map.values) {
      list.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    }
    return map;
  }

  LinearIssueInfo? getLinearIssue(String issueId) => _linearIssues[issueId];

  /// All Linear issues linked to the given task, sorted by updatedAt descending.
  List<LinearIssueInfo> linearIssuesForTask(String taskId) {
    final result = _linearIssues.values
        .where((i) => i.taskId == taskId)
        .toList();
    result.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    return result;
  }

  /// All Linear issues not yet linked to any task, sorted by updatedAt descending.
  List<LinearIssueInfo> get unlinkedLinearIssues {
    final result = _linearIssues.values.where((i) => i.taskId == null).toList();
    result.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    return result;
  }

  void _handleLinearIssueList(List<dynamic> list, String workerId) {
    _linearIssues.removeWhere((_, i) => i.workerId == workerId);
    final workerName = _workerConfigs
        .firstWhere((w) => w.id == workerId, orElse: () => _workerConfigs.first)
        .name;
    for (final raw in list) {
      final issue = LinearIssueInfo.fromJson(
        raw as Map<String, dynamic>,
        workerId: workerId,
        workerName: workerName,
      );
      _linearIssues[issue.id] = issue;
    }
    notifyListeners();
  }

  void _handleLinearIssueUpdate(Map<String, dynamic> msg, String workerId) {
    final issueId = msg['id'] as String?;
    if (issueId == null) return;
    final workerName = _workerConfigs
        .firstWhere((w) => w.id == workerId, orElse: () => _workerConfigs.first)
        .name;
    final updated = LinearIssueInfo.fromJson(
      msg,
      workerId: workerId,
      workerName: workerName,
    );
    _linearIssues[issueId] = updated;
    notifyListeners();
  }

  void _handleLinearIssueDeleted(Map<String, dynamic> msg) {
    final issueId = msg['id'] as String?;
    if (issueId == null) return;
    _linearIssues.remove(issueId);

    // Close any pane currently viewing this issue
    for (final entry in _panes.entries.toList()) {
      if (_paneTypes[entry.key] == PaneType.linearIssue &&
          entry.value.linearIssueId == issueId) {
        closeLinearIssueView(entry.key);
      }
    }
    notifyListeners();
  }

  /// Show a Linear issue in the active pane (converting it in-place).
  void openLinearIssueInPane(String issueId) {
    if (_splitRoot != null && _panes.containsKey(_activePaneId)) {
      activePane.pushNavHistory(_currentNavEntry(_activePaneId));

      if (_paneTypes[_activePaneId] == PaneType.terminal) {
        for (final info in _terminalSessions.values) {
          if (info.paneId == _activePaneId) {
            info.paneId = null;
            break;
          }
        }
      }
      _paneTypes[_activePaneId] = PaneType.linearIssue;
      activePane.setLinearIssueId(issueId);
      notifyListeners();
      return;
    }

    // No panes — create one
    final newId = 'pane_${_nextPaneId++}';
    final newPane = PaneState(paneId: newId, host: this)
      ..addListener(_onPaneChanged);
    _panes[newId] = newPane;
    _paneTypes[newId] = PaneType.linearIssue;
    newPane.setLinearIssueId(issueId);
    _splitRoot = PaneLeaf(newId);
    _activePaneId = newId;
    notifyListeners();
  }

  /// Close a Linear issue pane (convert to chat).
  void closeLinearIssueView(String paneId) {
    if (_paneTypes[paneId] != PaneType.linearIssue) return;
    _paneTypes.remove(paneId);
    _panes[paneId]?.clearLinearIssueId();
    notifyListeners();
  }

  /// Show the worker settings pane for [toolName] in the active pane.
  void openWorkerSettingsInPane(String toolName, {String section = 'plugins'}) {
    if (_splitRoot != null && _panes.containsKey(_activePaneId)) {
      activePane.pushNavHistory(_currentNavEntry(_activePaneId));

      if (_paneTypes[_activePaneId] == PaneType.terminal) {
        for (final info in _terminalSessions.values) {
          if (info.paneId == _activePaneId) {
            info.paneId = null;
            break;
          }
        }
      }
      _paneTypes[_activePaneId] = PaneType.workerSettings;
      activePane.setWorkerSettings(toolName, section: section);
      notifyListeners();
      return;
    }

    // No panes — create one
    final newId = 'pane_${_nextPaneId++}';
    final newPane = PaneState(paneId: newId, host: this)
      ..addListener(_onPaneChanged);
    _panes[newId] = newPane;
    _paneTypes[newId] = PaneType.workerSettings;
    newPane.setWorkerSettings(toolName, section: section);
    _splitRoot = PaneLeaf(newId);
    _activePaneId = newId;
    notifyListeners();
  }

  /// Close a worker settings pane (convert to chat).
  void closeWorkerSettingsView(String paneId) {
    if (_paneTypes[paneId] != PaneType.workerSettings) return;
    _paneTypes.remove(paneId);
    _panes[paneId]?.clearWorkerSettings();
    notifyListeners();
  }

  /// Show a task in the active pane (converting it in-place).
  void openTaskInPane(String taskId) {
    if (_splitRoot != null && _panes.containsKey(_activePaneId)) {
      // Push current view to nav history
      activePane.pushNavHistory(_currentNavEntry(_activePaneId));

      // Detach any existing terminal from this pane
      if (_paneTypes[_activePaneId] == PaneType.terminal) {
        for (final info in _terminalSessions.values) {
          if (info.paneId == _activePaneId) {
            info.paneId = null;
            break;
          }
        }
      }
      _paneTypes[_activePaneId] = PaneType.task;
      activePane.setTaskId(taskId);
      notifyListeners();
      return;
    }

    // No panes — create one
    final newId = 'pane_${_nextPaneId++}';
    final newPane = PaneState(paneId: newId, host: this)
      ..addListener(_onPaneChanged);
    _panes[newId] = newPane;
    _paneTypes[newId] = PaneType.task;
    newPane.setTaskId(taskId);
    _splitRoot = PaneLeaf(newId);
    _activePaneId = newId;
    notifyListeners();
  }

  /// Split a pane with a task via drag-and-drop.
  void splitPaneWithTask(String paneId, DropZone zone, String taskId) {
    if (_splitRoot == null || !_panes.containsKey(paneId)) return;

    final newId = 'pane_${_nextPaneId++}';
    final newPane = PaneState(paneId: newId, host: this)
      ..addListener(_onPaneChanged);
    _panes[newId] = newPane;
    _paneTypes[newId] = PaneType.task;
    newPane.setTaskId(taskId);

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

  /// Convert a task pane back to a chat pane.
  void closeTaskView(String paneId) {
    _paneTypes.remove(paneId);
    final pane = _panes[paneId];
    pane?.setTaskId(null);
    notifyListeners();
  }

  /// Start a new session from a task by opening a fresh chat pane with the
  /// task context pre-filled in the input area, allowing the user to edit
  /// before sending.
  void startSessionFromTask(String paneId, TaskInfo task) {
    final pane = _panes[paneId];
    if (pane == null) return;

    // Switch pane from task view to chat view
    closeTaskView(paneId);

    // Reset to fresh chat state
    pane.startNewChat();
    pane.setTargetWorker(task.workerId);

    // After the new session is created (ack), attach it to the task.
    pane.setNewSessionCallback((sessionId) {
      final worker = _workers[task.workerId];
      if (worker != null && worker.isConnected) {
        worker.ws.attachSessionToTask(task.taskId, sessionId);
      }
    });

    // Build context prompt from task
    final buffer = StringBuffer('Task: ${task.title}');
    if (task.description != null && task.description!.isNotEmpty) {
      buffer.write('\n\n${task.description}');
    }

    // Pre-fill input area so the user can review/edit before sending
    pane.setPendingInputText(buffer.toString());
    requestInputFocus();
  }

  PaneState get activePane {
    assert(_panes.isNotEmpty, 'No panes available');
    return _panes[_activePaneId]!;
  }

  SettingsService get settings => _settings;
  NotificationSoundService get soundService => _soundService;
  NotificationService get notificationService => _notificationService;
  HotkeyService get hotkeyService => _hotkeyService;

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
    _notificationService = NotificationService();
    _hotkeyService = HotkeyService(settings: _settings);
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
    worker.onProjectPathAttached = (sessionId, path) {
      for (final pane in _findPanesForSession(sessionId)) {
        pane.syncProjectFromSession(path);
      }
    };
    worker.onProjectNameError = (sessionId, error) {
      for (final pane in _findPanesForSession(sessionId)) {
        pane.setProjectNameError(error);
      }
    };
    worker.onProjectNameErrorCleared = (sessionId) {
      for (final pane in _findPanesForSession(sessionId)) {
        pane.clearProjectError();
      }
    };
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

    // Detect worker connection transitions for toast notifications
    if (_settings.toastEnabled && _settings.toastConnections) {
      for (final worker in _workers.values) {
        final prev = _prevWorkerStatuses[worker.config.id];
        final curr = worker.status;
        if (prev != null && prev != curr) {
          _fireWorkerConnectionNotification(worker, prev, curr);
        }
        _prevWorkerStatuses[worker.config.id] = curr;
      }
    } else {
      // Still track statuses even when disabled so we don't fire stale events
      for (final worker in _workers.values) {
        _prevWorkerStatuses[worker.config.id] = worker.status;
      }
    }

    notifyListeners();
  }

  void _fireWorkerConnectionNotification(
    WorkerConnection worker,
    WorkerConnectionStatus prev,
    WorkerConnectionStatus curr,
  ) {
    final name = worker.config.name;

    // N5: Lost connection (was connected, now disconnected or reconnecting)
    if (prev == WorkerConnectionStatus.connected &&
        (curr == WorkerConnectionStatus.disconnected ||
            curr == WorkerConnectionStatus.reconnecting)) {
      _notificationService.show(
        level: NotificationLevel.error,
        title: 'Lost Connection',
        body: 'Disconnected from $name',
      );
      return;
    }

    // N6: Reconnected (was reconnecting, now connected)
    if (prev == WorkerConnectionStatus.reconnecting &&
        curr == WorkerConnectionStatus.connected) {
      _notificationService.show(
        level: NotificationLevel.info,
        title: 'Reconnected',
        body: 'Connection to $name restored',
      );
      return;
    }

    // N7: Reconnection failed (was reconnecting, now disconnected = retries exhausted)
    if (prev == WorkerConnectionStatus.reconnecting &&
        curr == WorkerConnectionStatus.disconnected) {
      _notificationService.show(
        level: NotificationLevel.error,
        title: 'Reconnection Failed',
        body:
            'Could not reconnect to $name after ${WorkerConnection.maxRetries} attempts',
      );
      return;
    }
  }

  // --- Toast notification helpers ---

  void _showToast({
    required NotificationLevel level,
    required String title,
    String? body,
    required _ToastCategory category,
    String? actionLabel,
    VoidCallback? onAction,
  }) {
    if (!_settings.toastEnabled) return;
    switch (category) {
      case _ToastCategory.connection:
        if (!_settings.toastConnections) return;
      case _ToastCategory.task:
        if (!_settings.toastTasks) return;
      case _ToastCategory.session:
        if (!_settings.toastBackgroundSessions) return;
    }
    _notificationService.show(
      level: level,
      title: title,
      body: body,
      actionLabel: actionLabel,
      onAction: onAction,
    );
  }

  String _sessionLabel(String sessionId) {
    for (final w in _workers.values) {
      for (final s in w.sessions) {
        if (s.sessionId == sessionId) {
          return s.title ?? sessionId.substring(0, 8);
        }
      }
    }
    return sessionId.substring(0, 8);
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
            Future.microtask(
              () => activePane.switchSession(pendingId, recordHistory: false),
            );
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
            _showToast(
              level: NotificationLevel.error,
              title: 'Connection Failed',
              body: 'Failed to connect to ${config.name}: $e',
              category: _ToastCategory.connection,
            );
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
        old.host != config.host ||
        old.port != config.port ||
        old.apiKey != config.apiKey ||
        old.useSSL != config.useSSL;

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
      _showToast(
        level: NotificationLevel.error,
        title: 'Connection Failed',
        body: 'Failed to connect to ${worker.config.name}: $e',
        category: _ToastCategory.connection,
      );
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

  /// Get the [WorkerConnection] that owns the given session, if any.
  WorkerConnection? workerForSession(String sessionId) {
    for (final worker in _workers.values) {
      if (worker.sessions.any((s) => s.sessionId == sessionId)) {
        return worker;
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
  void showNotification({
    required NotificationLevel level,
    required String title,
    String? body,
  }) {
    _notificationService.show(level: level, title: title, body: body);
  }

  @override
  bool workerSupportsAttachments(String? workerId) {
    final id = workerId ?? defaultWorkerId;
    if (id == null) return true;
    return _workers[id]?.supportsAttachments ?? true;
  }

  @override
  bool workerSupportsImageAttachments(String? workerId) {
    final id = workerId ?? defaultWorkerId;
    if (id == null) return true;
    return _workers[id]?.supportsImageAttachments ?? true;
  }

  @override
  String? defaultAgentForWorker(String? workerId) {
    if (workerId == null) return null;
    try {
      final agent = _workerConfigs
          .firstWhere((c) => c.id == workerId)
          .defaultAgent;
      if (agent == null) return null;
      return kAgentMentionNames[agent] ?? agent;
    } catch (_) {
      return null;
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
  void addSystemMessageToPane(
    String paneId,
    String text, {
    bool isError = false,
    String? label,
  }) {
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
  void splitPaneWithSession(String paneId, DropZone zone, String sessionId) {
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

  void closePane(String paneId, {bool recordHistory = true}) {
    if (_splitRoot == null || !_panes.containsKey(paneId)) return;

    // Record closed pane state before destroying it
    String? closedTerminalId;
    String? closedTaskId;
    String? closedArtifactId;
    final paneType = _paneTypes[paneId] ?? PaneType.chat;

    // For terminal panes: detach but keep the session alive
    if (paneType == PaneType.terminal) {
      for (final info in _terminalSessions.values) {
        if (info.paneId == paneId) {
          closedTerminalId = info.terminalId;
          info.paneId = null; // Detach from pane, PTY stays alive
          break;
        }
      }
    }

    // For task panes: record the task ID for reopen
    if (paneType == PaneType.task) {
      closedTaskId = _panes[paneId]?.taskId;
    }

    // For artifact panes: record the artifact ID for reopen
    if (paneType == PaneType.artifact) {
      closedArtifactId = _panes[paneId]?.artifactId;
    }

    final pane = _panes[paneId];
    if (recordHistory && pane != null) {
      final record = _ClosedPaneRecord(
        sessionId: pane.sessionId,
        workerId: pane.workerId,
        paneType: paneType,
        terminalId: closedTerminalId,
        taskId: closedTaskId,
        artifactId: closedArtifactId,
        linearIssueId: pane.linearIssueId,
      );
      // Only record if the pane had meaningful state
      if (record.sessionId != null ||
          record.terminalId != null ||
          record.taskId != null ||
          record.artifactId != null ||
          record.linearIssueId != null) {
        _closedPaneHistory.add(record);
        if (_closedPaneHistory.length > _maxClosedPaneHistory) {
          _closedPaneHistory.removeAt(0);
        }
      }
    }

    _paneTypes.remove(paneId);
    _terminalPaneKeys.remove(paneId);

    final newRoot = treeClosePane(_splitRoot!, paneId);

    _panes.remove(paneId);
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

  /// Reopen the most recently closed pane by popping from the history stack.
  void reopenLastClosedPane() {
    if (_closedPaneHistory.isEmpty) return;

    final record = _closedPaneHistory.removeLast();

    if (record.paneType == PaneType.terminal && record.terminalId != null) {
      // Reopen terminal pane if the terminal session still exists
      if (_terminalSessions.containsKey(record.terminalId)) {
        if (hasNoPanes) {
          showTerminalInPane(record.terminalId!);
        } else {
          splitPane(_activePaneId, SplitAxis.horizontal);
          // The new pane is now active; convert it to show the terminal
          showTerminalInPane(record.terminalId!);
        }
        return;
      }
      // Terminal no longer exists — skip this record, try next
      reopenLastClosedPane();
      return;
    }

    // Task pane: reopen if the task still exists
    if (record.paneType == PaneType.task && record.taskId != null) {
      if (_tasks.containsKey(record.taskId)) {
        if (hasNoPanes) {
          openTaskInPane(record.taskId!);
        } else {
          splitPane(_activePaneId, SplitAxis.horizontal);
          openTaskInPane(record.taskId!);
        }
        return;
      }
      reopenLastClosedPane();
      return;
    }

    // Artifact pane: reopen if the artifact still exists
    if (record.paneType == PaneType.artifact && record.artifactId != null) {
      if (_artifacts.containsKey(record.artifactId)) {
        if (hasNoPanes) {
          openArtifactInPane(record.artifactId!);
        } else {
          splitPane(_activePaneId, SplitAxis.horizontal);
          openArtifactInPane(record.artifactId!);
        }
        return;
      }
      reopenLastClosedPane();
      return;
    }

    // Linear issue pane: reopen if the issue still exists
    if (record.paneType == PaneType.linearIssue &&
        record.linearIssueId != null) {
      if (_linearIssues.containsKey(record.linearIssueId)) {
        if (hasNoPanes) {
          openLinearIssueInPane(record.linearIssueId!);
        } else {
          splitPane(_activePaneId, SplitAxis.horizontal);
          openLinearIssueInPane(record.linearIssueId!);
        }
        return;
      }
      reopenLastClosedPane();
      return;
    }

    // Chat pane: reopen with the session if it still exists
    if (record.sessionId != null) {
      if (hasNoPanes) {
        final pane = createNewPane();
        pane.switchSession(record.sessionId!);
      } else {
        splitPane(_activePaneId, SplitAxis.horizontal);
        activePane.switchSession(record.sessionId!);
      }
      return;
    }
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

  /// Returns a non-terminal, non-task [PaneState], creating one via split if
  /// necessary. Use this when an action (e.g. "New Session", session tap)
  /// should target a chat pane but the active pane is a terminal or task view.
  PaneState ensureChatPane() {
    // No panes at all — create a fresh one (welcome screen → first pane).
    if (hasNoPanes) return createNewPane();

    final paneType = _paneTypes[_activePaneId];

    // If the active pane is already a chat pane, return it.
    if (paneType == null || paneType == PaneType.chat) {
      return activePane;
    }

    // Push current view to nav history before converting
    activePane.pushNavHistory(_currentNavEntry(_activePaneId));

    // Active pane is a terminal — convert it to a chat pane in-place.
    // Detach the terminal session (PTY stays alive server-side).
    if (paneType == PaneType.terminal) {
      for (final info in _terminalSessions.values) {
        if (info.paneId == _activePaneId) {
          info.paneId = null;
          break;
        }
      }
      _terminalPaneKeys.remove(_activePaneId);
    }

    // Active pane is a task view — convert it to a chat pane in-place.
    if (paneType == PaneType.task) {
      activePane.setTaskId(null);
    }

    // Active pane is an artifact view — convert it to a chat pane in-place.
    if (paneType == PaneType.artifact) {
      activePane.clearArtifactId();
    }

    _paneTypes.remove(_activePaneId);
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
        service.sendControl({'type': 'close', 'terminal_id': terminalId});
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
  void splitPaneWithTerminal(String paneId, DropZone zone, String terminalId) {
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
          isError: true,
        );
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

  // Message types that indicate a session is waiting for user input
  static const _awaitingInputTypes = {
    'summary',
    'session_end_ask',
    'plan_mode_ask',
    'plan_review_ask',
    'permission_request',
  };

  void _handleOutputMessage(Map<String, dynamic> msg, String workerId) {
    final type = msg['type'] as String?;
    if (type == null) return;

    // Task messages — handled here, not forwarded to panes
    if (type == 'task_list') {
      final list = msg['tasks'] as List<dynamic>? ?? [];
      _handleTaskList(list, workerId);
      return;
    }
    if (type == 'task_update') {
      _handleTaskUpdate(msg, workerId);
      return;
    }
    if (type == 'task_deleted') {
      _handleTaskDeleted(msg);
      return;
    }

    // Artifact messages — handled here, not forwarded to panes
    if (type == 'artifact_list') {
      final list = msg['artifacts'] as List<dynamic>? ?? [];
      _handleArtifactList(list, workerId);
      return;
    }
    if (type == 'artifact_update') {
      _handleArtifactUpdate(msg, workerId);
      return;
    }
    if (type == 'artifact_deleted') {
      _handleArtifactDeleted(msg);
      return;
    }

    // Linear issue messages — handled here, not forwarded to panes
    if (type == 'linear_issue_list') {
      final list = msg['issues'] as List<dynamic>? ?? [];
      _handleLinearIssueList(list, workerId);
      return;
    }
    if (type == 'linear_issue_update') {
      _handleLinearIssueUpdate(msg, workerId);
      return;
    }
    if (type == 'linear_issue_deleted') {
      _handleLinearIssueDeleted(msg);
      return;
    }

    final sessionId = msg['session_id'] as String?;
    final muted = sessionId != null && _soundMutedSessions.contains(sessionId);
    if (!muted) {
      final playForComplete =
          _settings.soundOnCompleteEnabled &&
          _completionSoundTypes.contains(type);
      final playForMessage =
          _settings.soundEnabled && _messageSoundTypes.contains(type);
      if (playForComplete || playForMessage) {
        _soundService.playNotificationSound();
      }
    }

    // Toast notifications for background sessions
    if (sessionId != null) {
      final sessionVisible = _findPanesForSession(sessionId).isNotEmpty;

      if (!sessionVisible) {
        // N4: Session awaiting input (not visible in any pane)
        if (_awaitingInputTypes.contains(type)) {
          final label = _sessionLabel(sessionId);
          _showToast(
            level: NotificationLevel.warning,
            title: 'Waiting for Input',
            body: label,
            category: _ToastCategory.session,
            onAction: () => _navigateToSession(sessionId),
          );
        }

        // N11: Error in background session
        if (type == 'error') {
          final label = _sessionLabel(sessionId);
          final content = msg['content'] as String? ?? 'Unknown error';
          _showToast(
            level: NotificationLevel.error,
            title: 'Session Error',
            body: '$label: $content',
            category: _ToastCategory.session,
            onAction: () => _navigateToSession(sessionId),
          );
        }

        // N12: Session completed (background)
        if (type == 'session_end') {
          final label = _sessionLabel(sessionId);
          _showToast(
            level: NotificationLevel.info,
            title: 'Session Completed',
            body: label,
            category: _ToastCategory.session,
            onAction: () => _navigateToSession(sessionId),
          );
        }
      }

      // N10: Token limit reached (always show, even if visible)
      if (type == 'error') {
        final code = msg['code'] as String?;
        if (code == 'TOKEN_LIMIT_REACHED') {
          final label = _sessionLabel(sessionId);
          _showToast(
            level: NotificationLevel.warning,
            title: 'Token Limit Reached',
            body: label,
            category: _ToastCategory.session,
            onAction: () => _navigateToSession(sessionId),
          );
        }
      }
    }

    // Session status change notifications (N8, N9)
    if (type == 'session_update' && sessionId != null) {
      final sessionVisible = _findPanesForSession(sessionId).isNotEmpty;
      if (!sessionVisible) {
        final status = msg['status'] as String?;
        if (status == 'failed') {
          final label = _sessionLabel(sessionId);
          _showToast(
            level: NotificationLevel.error,
            title: 'Session Failed',
            body: label,
            category: _ToastCategory.session,
            onAction: () => _navigateToSession(sessionId),
          );
        } else if (status == 'cancelled') {
          // Skip if the user just ended this session (already notified).
          if (!_userEndedSessionIds.remove(sessionId)) {
            final label = _sessionLabel(sessionId);
            _showToast(
              level: NotificationLevel.error,
              title: 'Session Cancelled',
              body: label,
              category: _ToastCategory.session,
              onAction: () => _navigateToSession(sessionId),
            );
          }
        }
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

  void _navigateToSession(String sessionId) {
    if (hasNoPanes) {
      final pane = createNewPane();
      pane.switchSession(sessionId);
    } else {
      // If a pane is already viewing it, just focus it
      for (final entry in _panes.entries) {
        if (entry.value.sessionId == sessionId) {
          setActivePane(entry.key);
          return;
        }
      }
      // Otherwise switch the active pane to it
      ensureChatPane().switchSession(sessionId);
    }
  }

  /// Navigate a pane back to its previous view by popping the nav history.
  void goBack(String paneId) {
    final pane = _panes[paneId];
    if (pane == null || !pane.canGoBack) return;

    final entry = pane.popNavHistory();
    if (entry == null) return;

    // Detach terminal if current pane is a terminal
    if (_paneTypes[paneId] == PaneType.terminal) {
      for (final info in _terminalSessions.values) {
        if (info.paneId == paneId) {
          info.paneId = null;
          break;
        }
      }
      _terminalPaneKeys.remove(paneId);
    }

    switch (entry.paneType) {
      case PaneType.chat:
        _paneTypes.remove(paneId);
        pane.clearTaskId();
        pane.clearArtifactId();
        pane.clearLinearIssueId();
        if (entry.sessionId != null) {
          pane.switchSession(entry.sessionId!, recordHistory: false);
        } else {
          pane.goHome();
        }
      case PaneType.task:
        _paneTypes[paneId] = PaneType.task;
        pane.clearArtifactId();
        pane.setTaskId(entry.taskId);
      case PaneType.artifact:
        _paneTypes[paneId] = PaneType.artifact;
        pane.clearTaskId();
        pane.clearLinearIssueId();
        pane.setArtifactId(entry.artifactId);
      case PaneType.linearIssue:
        _paneTypes[paneId] = PaneType.linearIssue;
        pane.clearTaskId();
        pane.clearArtifactId();
        pane.setLinearIssueId(entry.linearIssueId);
      case PaneType.workerSettings:
        _paneTypes[paneId] = PaneType.workerSettings;
        pane.clearTaskId();
        pane.clearArtifactId();
        pane.clearLinearIssueId();
        if (entry.workerSettingsTool != null) {
          pane.setWorkerSettings(
            entry.workerSettingsTool!,
            section: entry.workerSettingsSection ?? 'plugins',
          );
        }
      case PaneType.terminal:
        // Terminal back-navigation is not supported (terminals are detached)
        _paneTypes.remove(paneId);
        pane.goHome();
    }

    notifyListeners();
  }

  /// Capture the current view state of a pane as a [PaneNavEntry].
  PaneNavEntry _currentNavEntry(String paneId) {
    final paneType = _paneTypes[paneId] ?? PaneType.chat;
    final pane = _panes[paneId];
    return PaneNavEntry(
      paneType: paneType,
      sessionId: pane?.sessionId,
      taskId: pane?.taskId,
      artifactId: pane?.artifactId,
      linearIssueId: pane?.linearIssueId,
      workerSettingsTool: pane?.workerSettingsTool,
      workerSettingsSection: pane?.workerSettingsSection,
    );
  }

  // --- Pane navigation ---

  /// Focus the nearest pane in the given direction relative to the active pane.
  void focusAdjacentPane(AxisDirection direction) {
    if (_splitRoot == null || _panes.length <= 1) return;

    final rects = <String, Rect>{};
    _computePaneRects(_splitRoot!, const Rect.fromLTWH(0, 0, 1, 1), rects);

    final activeRect = rects[_activePaneId];
    if (activeRect == null) return;

    final activeCenter = activeRect.center;
    String? bestPaneId;
    double bestDistance = double.infinity;

    for (final entry in rects.entries) {
      if (entry.key == _activePaneId) continue;
      final candidateCenter = entry.value.center;

      final isInDirection = switch (direction) {
        AxisDirection.left => candidateCenter.dx < activeCenter.dx,
        AxisDirection.right => candidateCenter.dx > activeCenter.dx,
        AxisDirection.up => candidateCenter.dy < activeCenter.dy,
        AxisDirection.down => candidateCenter.dy > activeCenter.dy,
      };

      if (!isInDirection) continue;

      final distance = (candidateCenter - activeCenter).distance;
      if (distance < bestDistance) {
        bestDistance = distance;
        bestPaneId = entry.key;
      }
    }

    if (bestPaneId != null) {
      setActivePane(bestPaneId);
    }
  }

  void _computePaneRects(SplitNode node, Rect bounds, Map<String, Rect> out) {
    switch (node) {
      case PaneLeaf leaf:
        out[leaf.paneId] = bounds;
      case SplitBranch branch:
        if (branch.axis == SplitAxis.horizontal) {
          final splitX = bounds.left + bounds.width * branch.ratio;
          _computePaneRects(
            branch.first,
            Rect.fromLTRB(bounds.left, bounds.top, splitX, bounds.bottom),
            out,
          );
          _computePaneRects(
            branch.second,
            Rect.fromLTRB(splitX, bounds.top, bounds.right, bounds.bottom),
            out,
          );
        } else {
          final splitY = bounds.top + bounds.height * branch.ratio;
          _computePaneRects(
            branch.first,
            Rect.fromLTRB(bounds.left, bounds.top, bounds.right, splitY),
            out,
          );
          _computePaneRects(
            branch.second,
            Rect.fromLTRB(bounds.left, splitY, bounds.right, bounds.bottom),
            out,
          );
        }
    }
  }

  /// Cycle through panes in tree order.
  void cyclePaneFocus({required bool forward}) {
    if (_splitRoot == null || _panes.length <= 1) return;
    final ids = allPaneIds(_splitRoot!);
    final currentIdx = ids.indexOf(_activePaneId);
    if (currentIdx < 0) return;
    final nextIdx = forward
        ? (currentIdx + 1) % ids.length
        : (currentIdx - 1 + ids.length) % ids.length;
    setActivePane(ids[nextIdx]);
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
    _notificationService.dispose();
    inputFocusRequest.dispose();
    ForegroundServiceHelper.stop();
    super.dispose();
  }
}

// Aliases to avoid name collision with the splitPane method on AppState
SplitNode treeSplitPane(
  SplitNode node,
  String targetId,
  String newPaneId,
  SplitAxis axis,
) => splitPane(node, targetId, newPaneId, axis);

SplitNode? treeClosePane(SplitNode node, String targetId) =>
    closePane(node, targetId);

int treePaneCount(SplitNode node) => paneCount(node);

SplitNode treeSplitPaneAtPosition(
  SplitNode node,
  String targetId,
  String newPaneId,
  SplitAxis axis, {
  required bool insertFirst,
}) => splitPaneAtPosition(
  node,
  targetId,
  newPaneId,
  axis,
  insertFirst: insertFirst,
);

enum _ToastCategory { connection, task, session }

/// Snapshot of a closed pane's essential state for the reopen stack.
class _ClosedPaneRecord {
  final String? sessionId;
  final String? workerId;
  final PaneType paneType;
  final String? terminalId;
  final String? taskId;
  final String? artifactId;
  final String? linearIssueId;

  const _ClosedPaneRecord({
    this.sessionId,
    this.workerId,
    this.paneType = PaneType.chat,
    this.terminalId,
    this.taskId,
    this.artifactId,
    this.linearIssueId,
  });
}
