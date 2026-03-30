/// Per-pane state — each split pane gets its own instance.
///
/// Manages session viewing, message display, streaming, and pagination for a
/// single pane. References the shared [PaneHost] (implemented by AppState) for
/// connection, WebSocket, and session list access.
library;

import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/foundation.dart';

import '../models/app_notification.dart';
import '../models/session_info.dart';
import '../models/split_tree.dart';
import '../models/subprocess_info.dart';
import '../models/todo_item.dart';
import '../models/ws_messages.dart';
import '../services/websocket_service.dart';
import 'output_handlers.dart';

/// Snapshot of a pane's view state, used for back-navigation history.
class PaneNavEntry {
  final PaneType paneType;
  final String? sessionId;
  final String? taskId;
  final String? artifactId;
  final String? linearIssueId;
  /// Tool name when [paneType] is [PaneType.workerSettings].
  /// One of ``"claude_code"``, ``"codex"``, or ``"opencode"``.
  final String? workerSettingsTool;
  /// Sub-section to show within the worker settings pane.
  /// Defaults to ``"plugins"``.
  final String? workerSettingsSection;

  const PaneNavEntry({
    required this.paneType,
    this.sessionId,
    this.taskId,
    this.artifactId,
    this.linearIssueId,
    this.workerSettingsTool,
    this.workerSettingsSection,
  });
}

/// Interface exposing shared state that PaneState needs from AppState.
abstract class PaneHost {
  bool get connected;
  List<SessionInfo> get sessions;
  WebSocketService wsForWorker(String workerId);
  String? workerIdForSession(String sessionId);
  String? get defaultWorkerId;
  void refreshSessions();
  void addSystemMessageToPane(String paneId, String text,
      {bool isError = false, String? label});
  void muteSessionSound(String sessionId);
  void markSubscribed(String sessionId, {required String workerId});
  void requestUnsubscribe(String sessionId, String workerId);
  void showNotification({
    required NotificationLevel level,
    required String title,
    String? body,
  });

  /// Whether the worker for [workerId] (or the default worker) supports
  /// any file attachments (text/code files are always supported).
  bool workerSupportsAttachments(String? workerId);

  /// Whether the worker for [workerId] (or the default worker) supports
  /// image attachments (JPEG, PNG, GIF, WEBP).
  bool workerSupportsImageAttachments(String? workerId);
}

class PaneState extends ChangeNotifier {
  final String paneId;
  final PaneHost _host;

  // Worker this pane is currently targeting
  String? _workerId;
  String? get workerId => _workerId;

  // Project selected via the picker chip above the input field.
  // Sent as project_name in every sendPrompt call. Null = no project selected.
  String? _selectedProjectName;
  String? get selectedProjectName => _selectedProjectName;

  // Full absolute path of the locally-selected project, resolved at pick time
  // from the server's project list. Set by setSelectedProject and cleared on
  // session switch / goHome. Used by effectiveProjectPath to populate the
  // Project panel before the first prompt is sent (pre-session state).
  String? _selectedProjectPath;
  String? get selectedProjectPath => _selectedProjectPath;

  // Non-null when the backend rejected the last project_name we sent.
  // Cleared when the backend accepts a project or when the user clears the chip.
  String? _projectNameError;
  String? get projectNameError => _projectNameError;

  // Tool selected via the #ToolName chip above the input field.
  // When set, the tool mention is prepended to the prompt text on send so
  // the backend receives a normal #ToolName mention.  Null = no tool selected.
  String? _selectedToolMention;
  String? get selectedToolMention => _selectedToolMention;

  // Absolute path of the worktree the user pre-selected before the first message
  // is sent.  Passed as selected_worktree_path in the first sendPrompt call and
  // then cleared (the session's worktree selection lives on the backend afterward).
  // Also cleared when the user starts a new chat or switches sessions.
  String? _pendingWorktreePath;
  String? get pendingWorktreePath => _pendingWorktreePath;

  void setPendingWorktreePath(String? path) {
    _pendingWorktreePath = path;
    notifyListeners();
  }

  // Task pane state (when pane shows a task detail view)
  String? _taskId;
  String? get taskId => _taskId;

  void setTaskId(String? taskId) {
    _taskId = taskId;
    notifyListeners();
  }

  void clearTaskId() {
    _taskId = null;
    notifyListeners();
  }

  // Artifact pane state (when pane shows an artifact detail view)
  String? _artifactId;
  String? get artifactId => _artifactId;

  void setArtifactId(String? artifactId) {
    _artifactId = artifactId;
    notifyListeners();
  }

  void clearArtifactId() {
    _artifactId = null;
    notifyListeners();
  }

  // Linear issue pane state (when pane shows a Linear issue detail view)
  String? _linearIssueId;
  String? get linearIssueId => _linearIssueId;

  void setLinearIssueId(String? linearIssueId) {
    _linearIssueId = linearIssueId;
    notifyListeners();
  }

  void clearLinearIssueId() {
    _linearIssueId = null;
    notifyListeners();
  }

  // Worker settings pane state (when pane shows managed tool plugin settings)
  String? _workerSettingsTool;
  String? _workerSettingsSection;

  /// The managed tool whose settings are displayed (``"claude_code"``, ``"codex"``, or ``"opencode"``).
  String? get workerSettingsTool => _workerSettingsTool;

  /// The settings sub-section currently shown (e.g. ``"plugins"``).
  String? get workerSettingsSection => _workerSettingsSection;

  void setWorkerSettings(String toolName, {String section = 'plugins'}) {
    _workerSettingsTool = toolName;
    _workerSettingsSection = section;
    notifyListeners();
  }

  void clearWorkerSettings() {
    _workerSettingsTool = null;
    _workerSettingsSection = null;
    notifyListeners();
  }

  // Session state
  String? _sessionId;
  String? get sessionId => _sessionId;
  bool _readyForNewChat = false;
  bool get readyForNewChat => _readyForNewChat;
  bool _sessionEnded = false;
  bool get sessionEnded => _sessionEnded;
  bool _sessionPaused = false;
  bool get sessionPaused => _sessionPaused;
  /// Reason why the session is paused, or null for a manual pause.
  /// "max_turns" means Claude Code hit its configured turn limit.
  String? _pausedReason;
  String? get pausedReason => _pausedReason;
  bool pendingAck = false;

  // Running subprocess state (ephemeral — cleared on session switch / session end).
  SubprocessInfo? _runningSubprocess;
  SubprocessInfo? get runningSubprocess => _runningSubprocess;

  void setRunningSubprocess(SubprocessInfo? info) {
    _runningSubprocess = info;
    notifyListeners();
  }

  // Callback invoked once when a new session is created (ack received).
  void Function(String sessionId)? _onNewSessionAck;

  void setNewSessionCallback(void Function(String sessionId)? callback) {
    _onNewSessionAck = callback;
  }

  // Pre-fill text for the input area (e.g. from "Start Session from Task").
  // InputArea listens to this and populates its controller when non-null.
  String? _pendingInputText;
  String? get pendingInputText => _pendingInputText;

  /// Set text to pre-fill in the input area. InputArea will consume and clear
  /// this after applying it.
  void setPendingInputText(String? text) {
    _pendingInputText = text;
    notifyListeners();
  }

  /// Called by InputArea after it has applied the pending text.
  void consumePendingInputText() {
    _pendingInputText = null;
  }

  // Message display
  final List<DisplayMessage> _messages = [];
  List<DisplayMessage> get messages => _messages;

  // Pagination state (archived session message loading)
  int? _nextCursor;
  int _totalMessageCount = 0;
  bool _loadingMore = false;
  bool get loadingMore => _loadingMore;
  bool get hasMoreMessages => _nextCursor != null;
  int get totalMessageCount => _totalMessageCount;

  // Counter for locally-added user messages not yet echoed by server.
  // Prevents duplicate display when the server replays user messages.
  int _pendingLocalUserMessages = 0;

  // Todo list state (from TodoWrite tool calls)
  List<TodoItem> _todos = [];
  List<TodoItem> get todos => _todos;

  // Right panel state — which panel is open (null = closed).
  // Recognised keys: "todo", "project", "statistics".
  String? _activeRightPanel;
  String? get activeRightPanel => _activeRightPanel;
  double _rightPanelWidth = 260;
  double get rightPanelWidth => _rightPanelWidth;
  static const double rightPanelMinWidth = 180;
  static const double rightPanelMaxWidth = 500;

  // Kept for backwards compat — derived from _activeRightPanel.
  bool get todoPanelVisible => _activeRightPanel == 'todo';
  double get todoPanelWidth => _rightPanelWidth;
  static const double todoPanelMinWidth = rightPanelMinWidth;
  static const double todoPanelMaxWidth = rightPanelMaxWidth;

  /// The [WorktreeInfo] for the session currently shown in this pane, or null.
  WorktreeInfo? get currentWorktreeInfo {
    if (_sessionId == null) return null;
    return _host.sessions.cast<SessionInfo?>().firstWhere(
          (s) => s?.sessionId == _sessionId,
          orElse: () => null,
        )?.worktreeInfo;
  }

  /// The selected worktree path for the session currently shown in this pane,
  /// or null when no worktree is explicitly selected.
  String? get currentSelectedWorktreePath {
    if (_sessionId == null) return null;
    return _host.sessions.cast<SessionInfo?>().firstWhere(
          (s) => s?.sessionId == _sessionId,
          orElse: () => null,
        )?.selectedWorktreePath;
  }

  /// The main project path confirmed by the server for the current session,
  /// or null when no project is attached.
  String? get currentMainProjectPath {
    if (_sessionId == null) return null;
    return _host.sessions.cast<SessionInfo?>().firstWhere(
          (s) => s?.sessionId == _sessionId,
          orElse: () => null,
        )?.mainProjectPath;
  }

  /// The effective project path for the Project panel to display.
  ///
  /// Prefers the server-confirmed path from the active session. Falls back to
  /// [_selectedProjectPath] — the path resolved at pick time from the server's
  /// project list — so the panel can show worktrees and artifacts immediately
  /// after the user tags @ProjectName, even before the first prompt is sent.
  String? get effectiveProjectPath =>
      currentMainProjectPath ?? _selectedProjectPath;

  // --- Back-navigation history ---
  static const int _maxNavHistory = 30;
  final List<PaneNavEntry> _navHistory = [];
  bool get canGoBack => _navHistory.isNotEmpty;

  /// Push the current view state onto the nav history stack.
  /// Called by AppState before switching pane content.
  void pushNavHistory(PaneNavEntry entry) {
    _navHistory.add(entry);
    if (_navHistory.length > _maxNavHistory) {
      _navHistory.removeAt(0);
    }
    notifyListeners();
  }

  /// Pop and return the most recent nav history entry, or null if empty.
  PaneNavEntry? popNavHistory() {
    if (_navHistory.isEmpty) return null;
    final entry = _navHistory.removeLast();
    notifyListeners();
    return entry;
  }

  // Agent group tracking (Claude Code / Codex / OpenCode collapsible blocks)
  // True between agent_group_start and agent_group_end.
  bool _inAgentMode = false;
  // The tool name for the current agent group (e.g. 'claude_code', 'codex', 'opencode').
  String _agentToolName = 'claude_code';
  // Human-readable display name for the current agent group.
  String? _agentDisplayName;
  // Index in _messages of the current tool sub-group being built.
  // Null when no tool group is open (e.g. between text and next tool batch).
  int? _agentToolGroupIndex;

  /// Monotonically increasing revision counter, bumped on every notify.
  /// Used by OutputDisplay to detect changes that aren't visible from
  /// top-level message count or last-message content alone (e.g. tool
  /// output streaming inside an agentGroup's children).
  int _revision = 0;
  int get revision => _revision;

  @override
  void notifyListeners() {
    _revision++;
    super.notifyListeners();
  }

  // Dynamic streaming
  final List<String> _charQueue = [];
  Timer? _streamingTimer;
  String? _activeToolName;
  static const _tickMs = 16;
  static const _accelThreshold = 4;
  static const _speedScale = 1.5;
  static const _maxCharsPerTick = 80;
  static const _pageSize = 50;

  // Terminal statuses for sessions
  static const _terminalStatuses = {'completed', 'failed', 'cancelled'};

  /// Whether the current session is driven by an agent that supports plugin slash commands.
  bool get isClaudeCodeSession => _agentToolName == 'claude_code' || _agentToolName == 'opencode';

  /// Whether the input area should allow sending messages.
  /// Sending while paused is allowed — the server auto-resumes the session.
  /// Also allowed on the home screen — auto-creates a new session on send.
  bool get canSendMessage => _host.connected && !_sessionEnded;

  /// Whether the current session is actively processing (not idle).
  bool get isSessionProcessing {
    if (_sessionId == null) return false;
    final session = _host.sessions.cast<SessionInfo?>().firstWhere(
          (s) => s!.sessionId == _sessionId,
          orElse: () => null,
        );
    return session != null && session.isProcessing;
  }

  PaneState({required this.paneId, required PaneHost host}) : _host = host;

  /// Resolve the WebSocketService for the current worker context.
  /// For new chats (no session yet), uses the explicitly set _workerId or
  /// falls back to the host's default worker.
  WebSocketService? get _ws {
    final wid = _workerId ?? _host.defaultWorkerId;
    if (wid == null) return null;
    return _host.wsForWorker(wid);
  }

  /// Set the target worker for new chats.
  void setTargetWorker(String? workerId) {
    _workerId = workerId;
    notifyListeners();
  }

  // --- Session operations ---

  void sendPrompt(
    String text, {
    List<Map<String, dynamic>>? attachments,
  }) {
    if (!_host.connected || text.trim().isEmpty) return;
    if (_sessionId == null && !_readyForNewChat) {
      _readyForNewChat = true;
    }

    finalizeStream();
    _closeAgentToolGroup();
    _inAgentMode = false;
    _dismissSessionEndAsk();

    // Prepend #ToolName to the prompt so the backend sees a normal tool mention.
    final toolMention = _selectedToolMention;
    final effectiveText = toolMention != null ? '#$toolMention $text' : text;

    _pendingLocalUserMessages++;
    _messages.add(DisplayMessage(
      type: DisplayMessageType.user,
      content: text,
      sessionId: _sessionId,
      pendingLocalEcho: true,
      attachments: attachments,
    ));
    pendingAck = _sessionId == null; // new chat — expect ack
    notifyListeners();

    // Pass the pre-selected worktree path only for new sessions (no session yet).
    // After the session exists the user adjusts the worktree via the Project panel.
    final worktreeToSend = _sessionId == null ? _pendingWorktreePath : null;
    _ws?.sendPrompt(
      effectiveText,
      _sessionId,
      attachments: attachments,
      projectName: _selectedProjectName,
      selectedWorktreePath: worktreeToSend,
    );
  }

  void switchSession(String sessionId, {bool recordHistory = true}) {
    if (sessionId == _sessionId) return;

    // Push current session to nav history for back-navigation
    if (recordHistory && _sessionId != null) {
      pushNavHistory(PaneNavEntry(
        paneType: PaneType.chat,
        sessionId: _sessionId,
      ));
    }

    final oldSessionId = _sessionId;
    final oldWorkerId = _workerId;

    finalizeStream();
    _inAgentMode = false;
    _agentToolGroupIndex = null;
    _messages.clear();
    _todos = [];
    _activeRightPanel = null;
    _resetPagination();
    _pendingLocalUserMessages = 0;
    _sessionEnded = false;
    _sessionPaused = false;
    _pausedReason = null;
    _runningSubprocess = null;
    _selectedToolMention = null;
    _sessionId = sessionId;

    final session = _host.sessions.cast<SessionInfo?>().firstWhere(
          (s) => s!.sessionId == sessionId,
          orElse: () => null,
        );

    // Set workerId from session
    if (session != null) {
      _workerId = session.workerId;
    } else {
      _workerId = _host.workerIdForSession(sessionId);
    }

    final isTerminal =
        session != null && _terminalStatuses.contains(session.status);

    if (isTerminal) {
      _sessionEnded = true;
      _loadSessionMessages(sessionId);
    } else {
      if (session != null && session.status == 'paused') {
        _sessionPaused = true;
        _pausedReason = session.pausedReason;
      }
      _host.muteSessionSound(sessionId);
      _ws?.subscribe(sessionId);
      if (_workerId != null) {
        _host.markSubscribed(sessionId, workerId: _workerId!);
      }
    }

    _host.refreshSessions();

    // Unsubscribe from the old session if no other pane still views it
    if (oldSessionId != null && oldWorkerId != null) {
      _host.requestUnsubscribe(oldSessionId, oldWorkerId);
    }

    // Sync the project chip with the session's confirmed project.
    // Also auto-opens the project panel when switching to a session that
    // already has a project attached (handles back-navigation and restore).
    final mainPath = session?.mainProjectPath;
    if (mainPath != null) {
      _selectedProjectName = mainPath.split('/').last;
      _selectedProjectPath = mainPath;
      _activeRightPanel = 'project';
    } else {
      _selectedProjectName = null;
      _selectedProjectPath = null;
    }
    _projectNameError = null;

    notifyListeners();
  }

  void goHome() {
    final oldSessionId = _sessionId;
    final oldWorkerId = _workerId;

    finalizeStream();
    _inAgentMode = false;
    _agentToolGroupIndex = null;
    _sessionId = null;
    _readyForNewChat = false;
    _sessionEnded = false;
    _sessionPaused = false;
    _pausedReason = null;
    _runningSubprocess = null;
    _pendingLocalUserMessages = 0;
    _messages.clear();
    _todos = [];
    _activeRightPanel = null;
    _selectedProjectName = null;
    _selectedProjectPath = null;
    _projectNameError = null;
    _selectedToolMention = null;
    _pendingWorktreePath = null;
    _resetPagination();

    if (oldSessionId != null && oldWorkerId != null) {
      _host.requestUnsubscribe(oldSessionId, oldWorkerId);
    }

    notifyListeners();
  }

  void startNewChat() {
    final oldSessionId = _sessionId;
    final oldWorkerId = _workerId;

    finalizeStream();
    _inAgentMode = false;
    _agentToolGroupIndex = null;
    _sessionId = null;
    _readyForNewChat = true;
    _sessionEnded = false;
    _sessionPaused = false;
    _pausedReason = null;
    _runningSubprocess = null;
    _pendingLocalUserMessages = 0;
    _pendingInputText = null;
    _selectedToolMention = null;
    _pendingWorktreePath = null;
    _messages.clear();
    _todos = [];
    _activeRightPanel = null;
    _resetPagination();

    if (oldSessionId != null && oldWorkerId != null) {
      _host.requestUnsubscribe(oldSessionId, oldWorkerId);
    }

    notifyListeners();
  }

  void refresh() => notifyListeners();

  /// Clear the displayed message list for this pane (client-side only).
  /// Does not affect the server-side session or database history.
  void clearMessages() {
    finalizeStream();
    _messages.clear();
    _todos = [];
    if (_activeRightPanel == 'todo') _activeRightPanel = null;
    _pendingLocalUserMessages = 0;
    notifyListeners();
  }

  // --- Ack handling ---

  void handleAck(String sessionId, {String? workerId}) {
    finalizeStream();
    pendingAck = false;
    if (sessionId != _sessionId) {
      _addSystemMessage('[ACK] Session: $sessionId');
    }
    _sessionId = sessionId;
    if (workerId != null) _workerId = workerId;
    _readyForNewChat = false;

    // Fire the new-session callback (e.g. to attach task after creation).
    if (_onNewSessionAck != null) {
      _onNewSessionAck!(sessionId);
      _onNewSessionAck = null;
    }

    _host.refreshSessions();
    notifyListeners();
  }

  // --- Session lifecycle ---

  Future<void> cancelSession(String sessionId) async {
    try {
      final oldWorkerId = _workerId;
      await _ws?.cancelSession(sessionId);
      if (_sessionId == sessionId) {
        finalizeStream();
        _sessionId = null;
        _readyForNewChat = true;
        _sessionEnded = false;
        _messages.clear();

        if (oldWorkerId != null) {
          _host.requestUnsubscribe(sessionId, oldWorkerId);
        }
      }
      _host.showNotification(
        level: NotificationLevel.info,
        title: 'Session Ended',
      );
      _host.refreshSessions();
    } catch (e) {
      _addSystemMessage('Failed to cancel session: $e', isError: true);
    }
  }

  Future<void> endSession(String sessionId) async {
    try {
      for (final m in _messages) {
        if (m.type == DisplayMessageType.sessionEndAsk && m.accepted == null) {
          m.accepted = true;
        }
      }
      await _ws?.endSession(sessionId);
      finalizeStream();
      if (_sessionId == sessionId) {
        _sessionEnded = true;
      }
      _host.refreshSessions();
      notifyListeners();
    } catch (e) {
      _addSystemMessage('Failed to end session: $e', isError: true);
    }
  }

  Future<void> pauseSession(String sessionId) async {
    try {
      await _ws?.pauseSession(sessionId);
      _host.refreshSessions();
    } catch (e) {
      _addSystemMessage('Failed to pause session: $e', isError: true);
    }
  }

  Future<void> resumeSession(String sessionId) async {
    try {
      await _ws?.resumeSession(sessionId);
      _host.refreshSessions();
    } catch (e) {
      _addSystemMessage('Failed to resume session: $e', isError: true);
    }
  }

  Future<void> interruptSubprocess() async {
    final sid = _sessionId;
    if (sid == null) return;
    try {
      _ws?.interruptSubprocess(sid);
    } catch (e) {
      _addSystemMessage('Failed to interrupt subprocess: $e', isError: true);
    }
  }

  void handleSessionPaused(String? sessionId, {String? reason}) {
    if (_sessionId == sessionId) {
      finalizeStream();
      _sessionPaused = true;
      _pausedReason = reason;
    }
    _host.refreshSessions();
    notifyListeners();
  }

  void handleSessionResumed(String? sessionId) {
    if (_sessionId == sessionId) {
      _sessionPaused = false;
      _pausedReason = null;
    }
    _host.refreshSessions();
    notifyListeners();
  }

  Future<void> restoreSession(String sessionId) async {
    try {
      await _ws?.restoreSession(sessionId);
      if (_sessionId == sessionId) {
        _sessionEnded = false;
        _ws?.subscribe(sessionId);
        if (_workerId != null) {
          _host.markSubscribed(sessionId, workerId: _workerId!);
        }
      }
      _host.refreshSessions();
      notifyListeners();
    } catch (e) {
      _addSystemMessage('Failed to restore session: $e', isError: true);
    }
  }

  void handleSessionRestored(String? sessionId) {
    if (_sessionId == sessionId) {
      _sessionEnded = false;
    }
    _host.refreshSessions();
    notifyListeners();
  }

  Future<void> renameSession(String sessionId, String newTitle) async {
    final title = newTitle.trim().isEmpty ? null : newTitle.trim();
    try {
      await _ws?.renameSession(sessionId, title);
      _host.refreshSessions();
    } catch (e) {
      _addSystemMessage('Failed to rename session: $e', isError: true);
    }
  }

  void answerQuestion(DisplayMessage msg, Map<String, String> answers) {
    if (!_host.connected) return;
    msg.selectedAnswers = answers;
    msg.finished = true;
    _ws?.answerQuestion(msg.sessionId ?? _sessionId, answers);
    notifyListeners();
  }

  /// Send a mid-turn interactive response to Claude Code (plan mode, etc.)
  /// without opening a new agent group or adding a user message.
  void sendInteractiveResponse(DisplayMessage msg, String text,
      {bool accepted = true}) {
    if (!_host.connected) return;
    msg.accepted = accepted;
    final sid = msg.sessionId ?? _sessionId;
    if (sid != null) {
      _ws?.sendInteractiveResponse(sid, text, accepted: accepted);
    }
    notifyListeners();
  }

  void sendPermissionResponse({
    required String sessionId,
    required String requestId,
    required String decision,
    required String scope,
    String? pathPrefix,
  }) {
    if (!_host.connected) return;
    _ws?.sendPermissionResponse(
      sessionId: sessionId,
      requestId: requestId,
      decision: decision,
      scope: scope,
      pathPrefix: pathPrefix,
    );
    notifyListeners();
  }

  void handleSessionNotFound(String sessionId) {
    if (_sessionId != sessionId) return;
    _loadSessionMessages(sessionId);
    _host.refreshSessions();
  }

  void handleSessionEnded(String? sessionId) {
    if (_sessionId == sessionId) {
      finalizeStream();
      _sessionEnded = true;
      _finishPendingQuestions();
    }
    _host.refreshSessions();
    notifyListeners();
  }

  /// Mark any unanswered question blocks as finished (e.g. on session end).
  void _finishPendingQuestions() {
    for (final m in _messages) {
      if (m.isQuestion && !m.finished) m.finished = true;
      if (m.type == DisplayMessageType.agentGroup) {
        for (final child in m.children ?? <DisplayMessage>[]) {
          if (child.isQuestion && !child.finished) child.finished = true;
        }
      }
    }
  }

  // --- Agent group helpers ---

  /// The list that streaming content should be appended to: either the active
  /// tool sub-group's children, or the top-level message list.
  List<DisplayMessage> get _streamTargetList {
    if (_agentToolGroupIndex != null &&
        _agentToolGroupIndex! < _messages.length &&
        _messages[_agentToolGroupIndex!].type ==
            DisplayMessageType.agentGroup) {
      return _messages[_agentToolGroupIndex!].children!;
    }
    return _messages;
  }

  /// The message that the streaming character queue is currently writing into.
  DisplayMessage? get _streamTarget {
    final list = _streamTargetList;
    return list.isEmpty ? null : list.last;
  }

  /// The last message in the current stream target list (public, for handlers).
  DisplayMessage? get lastStreamMessage => _streamTarget;

  /// Enter agent mode — subsequent tool calls will be auto-grouped.
  void startAgentGroup(String name, Map<String, dynamic>? input, {String? displayName}) {
    finalizeStream();
    _inAgentMode = true;
    _agentToolName = name;
    _agentDisplayName = displayName;
    // Don't create a message yet — wait for the first tool_start.
    notifyListeners();
  }

  /// Exit agent mode — close any open tool sub-group.
  void endAgentGroup() {
    finalizeStream();
    _closeAgentToolGroup();
    _inAgentMode = false;
    notifyListeners();
  }

  // -- Right panel management --

  /// Open [panelKey] ("todo", "project", or "statistics"), or toggle it off if already active.
  void toggleRightPanel(String panelKey) {
    _activeRightPanel = _activeRightPanel == panelKey ? null : panelKey;
    notifyListeners();
  }

  void setRightPanelWidth(double width) {
    _rightPanelWidth = width.clamp(rightPanelMinWidth, rightPanelMaxWidth);
    notifyListeners();
  }

  // -- Todo list management --

  void updateTodos(List<TodoItem> todos) {
    _todos = todos;
    if (todos.isNotEmpty && _activeRightPanel == null) {
      _activeRightPanel = 'todo';
    }
    notifyListeners();
  }

  void clearTodos() {
    if (_todos.isEmpty) return;
    _todos = [];
    if (_activeRightPanel == 'todo') _activeRightPanel = null;
    notifyListeners();
  }

  void toggleTodoPanel() => toggleRightPanel('todo');

  void setTodoPanelWidth(double width) => setRightPanelWidth(width);

  /// Open the Project panel if it is not already the active panel.
  void openProjectPanel() {
    if (_activeRightPanel != 'project') {
      _activeRightPanel = 'project';
      notifyListeners();
    }
  }

  /// Called when the user picks a project in the picker chip.
  ///
  /// [name] is the project folder name sent to the server as `project_name`.
  /// [path] is the full absolute path resolved from the server's project list
  /// at pick time; when provided it is used as the pre-session [effectiveProjectPath]
  /// so the Project panel can show worktrees and artifacts immediately.
  ///
  /// Opens the Project panel immediately and clears any previous error.
  void setSelectedProject(String? name, {String? path}) {
    _selectedProjectName = name;
    _selectedProjectPath = path;
    _projectNameError = null;
    if (name != null) openProjectPanel();
    notifyListeners();
  }

  /// Sync chip state from a confirmed server-side project path.
  /// Called when a session_update transitions main_project_path from null→non-null
  /// (i.e. the server has accepted and echoed back our project_name selection).
  void syncProjectFromSession(String path) {
    final name = path.split('/').last;
    if (_selectedProjectName == name &&
        _selectedProjectPath == path &&
        _activeRightPanel == 'project') return;
    _selectedProjectName = name;
    _selectedProjectPath = path;
    _activeRightPanel = 'project';
    notifyListeners();
  }

  /// Clear the project name error flag when the backend accepts a project.
  void clearProjectError() {
    if (_projectNameError != null) {
      _projectNameError = null;
      notifyListeners();
    }
  }

  /// Set a project name error received from the backend session_update.
  void setProjectNameError(String error) {
    _projectNameError = error;
    notifyListeners();
  }

  /// Set or clear the tool mention chip above the input field.
  void setSelectedTool(String? name) {
    _selectedToolMention = name;
    notifyListeners();
  }

  /// Reconstruct todo state from a history message's metadata.
  void _reconstructTodosFromHistory(Map<String, dynamic> msg) {
    final metadata = msg['metadata'] as Map<String, dynamic>? ?? {};
    final rawTodos = metadata['todos'] as List<dynamic>? ?? [];
    if (rawTodos.isNotEmpty) {
      _todos = rawTodos
          .whereType<Map<String, dynamic>>()
          .map((t) => TodoItem.fromJson(t))
          .toList();
    }
  }

  /// Ensure a tool sub-group exists (create one if needed).
  void _ensureAgentToolGroup() {
    if (_agentToolGroupIndex != null) return;
    _messages.add(DisplayMessage(
      type: DisplayMessageType.agentGroup,
      sessionId: _sessionId,
      toolName: _agentToolName,
      displayName: _agentDisplayName,
      children: [],
      expanded: true,
    ));
    _agentToolGroupIndex = _messages.length - 1;
  }

  /// Close the current tool sub-group (mark finished, collapse).
  void _closeAgentToolGroup() {
    if (_agentToolGroupIndex == null) return;
    if (_agentToolGroupIndex! < _messages.length &&
        _messages[_agentToolGroupIndex!].type ==
            DisplayMessageType.agentGroup) {
      final group = _messages[_agentToolGroupIndex!];
      group.finished = true;
      group.expanded = false;
      for (final child in group.children ?? <DisplayMessage>[]) {
        if (child.type == DisplayMessageType.toolBlock &&
            !child.finished &&
            !child.isQuestion) {
          child.finished = true;
        }
      }
    }
    _agentToolGroupIndex = null;
  }

  // --- Streaming helpers ---

  void appendAssistantChunk(String text) {
    // Assistant text breaks a tool group — close it and add text at top level.
    if (_inAgentMode && _agentToolGroupIndex != null) {
      finalizeStream();
      _closeAgentToolGroup();
    }
    if (_messages.isEmpty ||
        _messages.last.type != DisplayMessageType.assistant) {
      finalizeStream();
      _messages.add(DisplayMessage(
        type: DisplayMessageType.assistant,
        sessionId: _sessionId,
      ));
      notifyListeners();
    }
    _enqueueText(text);
  }

  void startToolBlock(String name, Map<String, dynamic>? input,
      {String? displayName}) {
    finalizeStream();
    _activeToolName = name;
    if (_inAgentMode) {
      _ensureAgentToolGroup();
    }
    _streamTargetList.add(DisplayMessage(
      type: DisplayMessageType.toolBlock,
      sessionId: _sessionId,
      toolName: name,
      displayName: displayName,
      toolInput: input,
    ));
    notifyListeners();
  }

  void appendToolOutput(String text, {bool isError = false}) {
    final target = _streamTargetList;
    if (target.isEmpty ||
        target.last.type != DisplayMessageType.toolBlock) {
      target.add(DisplayMessage(
        type: DisplayMessageType.toolBlock,
        sessionId: _sessionId,
        toolName: 'output',
      ));
      notifyListeners();
    }
    if (isError && target.isNotEmpty) {
      target.last.isError = true;
      target.last.expanded = true;
    }
    _enqueueText(text);
  }

  void _enqueueText(String text) {
    for (int i = 0; i < text.length; i++) {
      _charQueue.add(text[i]);
    }
    _streamingTimer ??= Timer.periodic(
      const Duration(milliseconds: _tickMs),
      (_) => _renderChars(),
    );
  }

  int get _charsPerTick {
    final len = _charQueue.length;
    if (len <= _accelThreshold) return 1;
    final excess = (len - _accelThreshold).toDouble();
    return (math.sqrt(excess) * _speedScale).ceil().clamp(1, _maxCharsPerTick);
  }

  void _renderChars() {
    if (_charQueue.isEmpty) {
      _streamingTimer?.cancel();
      _streamingTimer = null;
      return;
    }
    final target = _streamTarget;
    if (target == null) return;

    final count = _charsPerTick.clamp(1, _charQueue.length);
    final batch = _charQueue.sublist(0, count).join();
    _charQueue.removeRange(0, count);
    target.content += batch;
    notifyListeners();
  }

  void finalizeStream() {
    final target = _streamTarget;
    if (_charQueue.isNotEmpty && target != null) {
      target.content += _charQueue.join();
      _charQueue.clear();
    }
    _streamingTimer?.cancel();
    _streamingTimer = null;

    if (_activeToolName != null && target != null) {
      if (target.type == DisplayMessageType.toolBlock && !target.isQuestion) {
        target.finished = true;
      }
    }

    _activeToolName = null;
    notifyListeners();
  }

  void stripSessionEndAskTag() {
    for (int i = _messages.length - 1; i >= 0; i--) {
      if (_messages[i].type == DisplayMessageType.assistant) {
        _messages[i].content =
            _messages[i].content.replaceAll('[SessionEndAsk]', '').trimRight();
        break;
      }
    }
  }

  void _dismissSessionEndAsk() {
    for (final m in _messages) {
      if (m.type == DisplayMessageType.sessionEndAsk && m.accepted == null) {
        m.accepted = false;
      }
    }
  }

  void dismissSessionEndAsk() {
    _dismissSessionEndAsk();
    if (_sessionId != null && _host.connected) {
      _ws?.dismissSessionEndAsk(_sessionId!);
    }
    notifyListeners();
  }

  /// Returns true if a locally-added user message matches [echoContent] —
  /// meaning the server echo should be skipped.
  ///
  /// Uses content-based matching against pending local messages, with the
  /// counter as a fallback. This is robust against timing issues where the
  /// counter might be reset (e.g. by an intervening [switchSession]).
  bool consumeLocalUserMessage(String echoContent) {
    // Primary: content-based match against pending local messages.
    for (int i = _messages.length - 1; i >= 0; i--) {
      final m = _messages[i];
      if (m.type == DisplayMessageType.user &&
          m.pendingLocalEcho &&
          m.content == echoContent) {
        m.pendingLocalEcho = false;
        if (_pendingLocalUserMessages > 0) _pendingLocalUserMessages--;
        return true;
      }
    }
    // Fallback: counter only (e.g. if content was mutated).
    if (_pendingLocalUserMessages > 0) {
      _pendingLocalUserMessages--;
      return true;
    }
    return false;
  }

  void addDisplayMessage(DisplayMessage msg) {
    _messages.add(msg);
    notifyListeners();
  }

  /// Add a display message respecting agent group nesting.
  void addDisplayMessageInStream(DisplayMessage msg) {
    if (_inAgentMode) {
      _ensureAgentToolGroup();
    }
    _streamTargetList.add(msg);
    notifyListeners();
  }

  void addSystemMessage(String text, {bool isError = false}) {
    _addSystemMessage(text, isError: isError);
  }

  void _addSystemMessage(String text, {bool isError = false}) {
    _messages.add(DisplayMessage(
      type: isError ? DisplayMessageType.error : DisplayMessageType.system,
      content: text,
    ));
    notifyListeners();
  }

  // --- Pagination ---

  Future<void> _loadSessionMessages(String sessionId) async {
    _addSystemMessage('Loading session history...');
    try {
      final ws = _ws;
      if (ws == null) {
        _addSystemMessage('Not connected to worker', isError: true);
        return;
      }
      final response =
          await ws.fetchSessionMessages(sessionId, limit: _pageSize);
      if (_sessionId != sessionId) return;

      if (_messages.isNotEmpty &&
          _messages.last.type == DisplayMessageType.system) {
        _messages.removeLast();
      }

      final rawMessages = (response['messages'] as List<dynamic>? ?? [])
          .cast<Map<String, dynamic>>();
      final pagination =
          response['pagination'] as Map<String, dynamic>? ?? {};
      _totalMessageCount = pagination['total_count'] as int? ?? 0;
      final hasMore = pagination['has_more'] as bool? ?? false;
      _nextCursor = hasMore ? pagination['next_cursor'] as int? : null;

      if (rawMessages.isEmpty) {
        _addSystemMessage('No message history available');
      } else {
        _buildDisplayFromHistoryInto(rawMessages, sessionId, _messages);
      }
    } catch (e) {
      _addSystemMessage('Failed to load session history: $e', isError: true);
    }
  }

  Future<void> loadOlderMessages() async {
    if (_loadingMore || _nextCursor == null || _sessionId == null) return;
    final sessionId = _sessionId!;
    final ws = _ws;
    if (ws == null) return;

    _loadingMore = true;
    notifyListeners();

    try {
      final response = await ws.fetchSessionMessages(
        sessionId,
        before: _nextCursor,
        limit: _pageSize,
      );
      if (_sessionId != sessionId) return;

      final rawMessages = (response['messages'] as List<dynamic>? ?? [])
          .cast<Map<String, dynamic>>();
      final pagination =
          response['pagination'] as Map<String, dynamic>? ?? {};
      final hasMore = pagination['has_more'] as bool? ?? false;
      _nextCursor = hasMore ? pagination['next_cursor'] as int? : null;

      if (rawMessages.isNotEmpty) {
        final olderMessages = <DisplayMessage>[];
        _buildDisplayFromHistoryInto(rawMessages, sessionId, olderMessages);
        _messages.insertAll(0, olderMessages);
      }
    } catch (e) {
      _addSystemMessage('Failed to load older messages: $e', isError: true);
    } finally {
      _loadingMore = false;
      notifyListeners();
    }
  }

  void _buildDisplayFromHistoryInto(
    List<Map<String, dynamic>> rawMessages,
    String sessionId,
    List<DisplayMessage> target,
  ) {
    bool inAgent = false;
    int? toolGroupIdx;
    String? agentDisplayName;
    String agentToolName = 'claude_code';

    void closeToolGroup() {
      if (toolGroupIdx != null && toolGroupIdx! < target.length) {
        final group = target[toolGroupIdx!];
        group.finished = true;
        group.expanded = false;
        for (final child in group.children ?? <DisplayMessage>[]) {
          if (child.type == DisplayMessageType.toolBlock && !child.finished) {
            child.finished = true;
          }
        }
      }
      toolGroupIdx = null;
    }

    void ensureToolGroup() {
      if (toolGroupIdx != null) return;
      target.add(DisplayMessage(
        type: DisplayMessageType.agentGroup,
        sessionId: sessionId,
        toolName: agentToolName,
        displayName: agentDisplayName,
        children: [],
        finished: false,
      ));
      toolGroupIdx = target.length - 1;
    }

    for (final msg in rawMessages) {
      final type = msg['type'] as String? ?? '';

      if (type == 'agent_group_start') {
        inAgent = true;
        final meta = msg['metadata'] as Map<String, dynamic>?;
        agentDisplayName = meta?['display_name'] as String?;
        agentToolName = meta?['tool_name'] as String? ?? 'claude_code';
        continue;
      }

      if (type == 'agent_group_end') {
        closeToolGroup();
        inAgent = false;
        continue;
      }

      if (inAgent) {
        if (type == 'todo_update') {
          // Todo updates go into the agent group but also update pane state
          ensureToolGroup();
          final builder = historyBuilderRegistry[type];
          if (builder != null) {
            builder(msg, sessionId, target[toolGroupIdx!].children!);
          }
          _reconstructTodosFromHistory(msg);
        } else if (type == 'tool_start' || type == 'tool_output') {
          ensureToolGroup();
          final builder = historyBuilderRegistry[type];
          if (builder != null) {
            builder(msg, sessionId, target[toolGroupIdx!].children!);
          }
        } else if (type == 'text_chunk') {
          final metadata = msg['metadata'] as Map<String, dynamic>? ?? {};
          if (metadata['role'] == 'user') {
            final builder = historyBuilderRegistry[type];
            if (builder != null) builder(msg, sessionId, target);
          } else {
            // Assistant text closes the current tool group
            closeToolGroup();
            final builder = historyBuilderRegistry[type];
            if (builder != null) builder(msg, sessionId, target);
          }
        } else {
          // Other types (error, summary, etc.) go to top level
          closeToolGroup();
          final builder = historyBuilderRegistry[type];
          if (builder != null) builder(msg, sessionId, target);
        }
      } else {
        final builder = historyBuilderRegistry[type];
        if (builder != null) builder(msg, sessionId, target);
        if (type == 'todo_update') {
          _reconstructTodosFromHistory(msg);
        }
      }
    }

    // Finalize any remaining open groups
    closeToolGroup();

    for (final m in target) {
      if (m.type == DisplayMessageType.toolBlock && !m.finished) {
        m.finished = true;
      }
      if (m.type == DisplayMessageType.agentGroup) {
        for (final child in m.children ?? <DisplayMessage>[]) {
          if (child.type == DisplayMessageType.toolBlock && !child.finished) {
            child.finished = true;
          }
        }
      }
    }

    notifyListeners();
  }

  void _resetPagination() {
    _nextCursor = null;
    _totalMessageCount = 0;
    _loadingMore = false;
  }

  @override
  void dispose() {
    _streamingTimer?.cancel();
    super.dispose();
  }
}
