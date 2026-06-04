/// Per-pane state — each split pane gets its own instance.
///
/// Manages session viewing, message display, streaming, and pagination for a
/// single pane. References the shared [PaneHost] (implemented by AppState) for
/// connection, WebSocket, and session list access.
library;

import 'dart:async';

import 'package:flutter/foundation.dart';

import '../models/app_notification.dart';
import '../models/scheduled_wake.dart';
import 'pane_queue_state.dart';
import 'pane_view_target.dart';
import '../models/session_info.dart';
import '../models/split_tree.dart';
import '../models/subprocess_info.dart';
import '../models/todo_item.dart';
import '../models/worker_config.dart';
import '../models/ws_messages.dart';
import '../services/websocket_service.dart';
import 'output_handlers.dart';

part 'pane_message_store.dart';

/// Snapshot of a pane's view state, used for back-navigation history.
class PaneNavEntry {
  final PaneType paneType;
  final String? sessionId;
  final String? taskId;
  final String? artifactId;
  final String? linearIssueId;
  final String? githubPrId;

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
    this.githubPrId,
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
  void addSystemMessageToPane(
    String paneId,
    String text, {
    bool isError = false,
    String? label,
  });
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

  /// Returns the default agent tool name configured for [workerId], or null if
  /// no default is set or the worker is not found.
  String? defaultAgentForWorker(String? workerId);

  /// Returns the last project name the user worked on for [workerId], or null.
  String? getLastProjectForWorker(String workerId);

  /// Read the cached draft for [key] from local storage.
  /// Key is a session ID or `"new_{workerId}"` for the new-session pane.
  ({String content, DateTime? cachedAt}) getDraft(String key);

  /// Persist [content] as the draft for [key] in local storage.
  void saveDraft(String key, String content);

  /// Remove the draft for [key] from local storage.
  void clearDraft(String key);

  /// Read persisted pluck selections (agent, project, worktree) for [key].
  /// Returns null when nothing has been saved for that key.
  Map<String, dynamic>? getDraftPlucks(String key);

  /// Persist pluck selections for [key].
  void saveDraftPlucks(String key, Map<String, dynamic> plucks);

  /// Remove the pluck selections for [key] from local storage.
  void clearDraftPlucks(String key);

  /// Validates that [projectName] exists on [workerId] and returns its full
  /// absolute path, or null if the project cannot be found or the worker is
  /// not connected.
  Future<String?> resolveProjectOnWorker(String workerId, String projectName);

  /// Returns true if the given worker's active tool has caveman mode enabled.
  /// Used by [PaneState.isCavemanActive] for new-chat panes.
  bool isWorkerCavemanActive(String? workerId);

  /// Returns the [SessionInfo] for [sessionId], or null if not found.
  SessionInfo? sessionById(String sessionId);
}

class PaneState extends ChangeNotifier {
  final String paneId;
  final PaneHost _host;

  /// Owns the chat content (messages, streaming, todos, queue, pagination).
  /// PaneState forwards the message/stream surface to this store.
  late final PaneMessageStore _msg = PaneMessageStore(this);

  bool _disposed = false;

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

  // True while _applyWorkerDefaults() is resolving the cached project for the
  // newly selected worker.  Used by the UI to show a subtle loading state on
  // the project chip instead of a blank gap.
  bool _loadingWorkerDefaults = false;
  bool get loadingWorkerDefaults => _loadingWorkerDefaults;

  // Tool selected via the #ToolName chip above the input field.
  // When set, the tool mention is prepended to the prompt text on send so
  // the backend receives a normal #ToolName mention.  Null = no tool selected.
  String? _selectedToolMention;
  String? get selectedToolMention => _selectedToolMention;

  // What non-chat content this pane shows + pending first-prompt selections.
  // Storage lives on the composed [PaneViewTarget]; PaneState keeps the notify
  // responsibility and re-exposes the historical accessors.
  final PaneViewTarget _view = PaneViewTarget();

  String? get pendingWorktreePath => _view.pendingWorktreePath;

  void setPendingWorktreePath(String? path) {
    _view.pendingWorktreePath = path;
    notifyListeners();
  }

  String? get pendingTaskId => _view.pendingTaskId;

  void setPendingTaskId(String? taskId) {
    _view.pendingTaskId = taskId;
    notifyListeners();
  }

  void _clearPendingTaskId() {
    _view.pendingTaskId = null;
  }

  String? get taskId => _view.taskId;

  void setTaskId(String? taskId) {
    _view.taskId = taskId;
    notifyListeners();
  }

  void clearTaskId() {
    _view.taskId = null;
    notifyListeners();
  }

  String? get artifactId => _view.artifactId;

  void setArtifactId(String? artifactId) {
    _view.artifactId = artifactId;
    notifyListeners();
  }

  void clearArtifactId() {
    _view.artifactId = null;
    notifyListeners();
  }

  String? get linearIssueId => _view.linearIssueId;

  void setLinearIssueId(String? linearIssueId) {
    _view.linearIssueId = linearIssueId;
    notifyListeners();
  }

  void clearLinearIssueId() {
    _view.linearIssueId = null;
    notifyListeners();
  }

  String? get githubPrId => _view.githubPrId;

  void setGithubPrId(String? githubPrId) {
    _view.githubPrId = githubPrId;
    notifyListeners();
  }

  void clearGithubPrId() {
    _view.githubPrId = null;
    notifyListeners();
  }

  /// The managed tool whose settings are displayed (``"claude_code"``, ``"codex"``, or ``"opencode"``).
  String? get workerSettingsTool => _view.workerSettingsTool;

  /// The settings sub-section currently shown (e.g. ``"plugins"``).
  String? get workerSettingsSection => _view.workerSettingsSection;

  void setWorkerSettings(String toolName, {String section = 'plugins'}) {
    _view.workerSettingsTool = toolName;
    _view.workerSettingsSection = section;
    notifyListeners();
  }

  void clearWorkerSettings() {
    _view.workerSettingsTool = null;
    _view.workerSettingsSection = null;
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

  /// Live Claude Code ``Monitor`` watches keyed by ``monitor_id``.  Lookups by
  /// id keep events routing to the correct block even when interleaved with
  /// other tool calls.  Entries are removed when ``monitor_end`` arrives or
  /// the session is switched.
  final Map<String, DisplayMessage> _activeMonitors = {};
  Map<String, DisplayMessage> get activeMonitors => _activeMonitors;

  /// All currently-live monitor blocks for this pane, in start order.
  List<DisplayMessage> get liveMonitors =>
      _activeMonitors.values.where((m) => !m.finished).toList(growable: false);

  /// Hard cap on monitor events retained per block.  Older entries are
  /// dropped while ``monitorTotalEvents`` keeps the true count.
  static const int monitorEventCap = 200;

  Future<void> cancelMonitor(String monitorId) async {
    final sid = _sessionId;
    if (sid == null) return;
    _ws?.cancelMonitor(sid, monitorId);
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

  // --- Draft management ---
  //
  // _draftProvider: callback registered by InputArea so PaneState can read the
  // live controller text synchronously at switch/goHome time without owning it.
  //
  // _lastLoadedDraft: the text that was in the input when the draft was last
  // *loaded* (not typed). Used to detect whether the user actually modified the
  // draft so we avoid clobbering a sibling pane's live draft with an unchanged
  // snapshot (multi-pane same-session guard).

  String Function()? _draftProvider;
  String _lastLoadedDraft = '';

  /// Register the callback that reads the current input controller text.
  /// Called by InputArea in initState.
  void registerDraftProvider(String Function() provider) {
    _draftProvider = provider;
  }

  /// Unregister the draft provider. Called by InputArea in dispose.
  void unregisterDraftProvider() {
    _draftProvider = null;
  }

  /// Called by InputArea's debounce timer after the user pauses typing.
  void triggerDraftSave() {
    _saveDraftIfChanged();
  }

  /// Snapshot the current input text and persist it if it differs from what
  /// was loaded. Synchronous entry point; async writes are fire-and-forget.
  void _saveDraftIfChanged() {
    final text = _draftProvider?.call() ?? '';
    // Multi-pane guard: skip if this pane never changed the draft.
    if (text == _lastLoadedDraft) return;

    // Key: real session → session ID; new-session pane → "new_{workerId}".
    final key = _sessionId ?? (_workerId != null ? 'new_$_workerId' : null);
    if (key == null) return;

    _host.saveDraft(key, text);

    // Persist current pluck chip state alongside the text draft so that the
    // full input-area configuration (agent, project, worktree) round-trips.
    final plucks = <String, dynamic>{
      if (_selectedToolMention != null) 'agent': _selectedToolMention,
      if (_selectedProjectName != null) 'project': _selectedProjectName,
      if (_view.pendingWorktreePath != null)
        'worktree': _view.pendingWorktreePath,
    };
    if (plucks.isNotEmpty) {
      _host.saveDraftPlucks(key, plucks);
    }

    // Write backend only for real sessions (new-session pane has no ID yet).
    if (_sessionId != null) {
      final ws = _ws;
      if (ws != null) {
        // ignore: discarded_futures
        ws.saveSessionDraft(_sessionId!, text);
      }
    }
  }

  /// Two-phase draft load for a real session:
  ///   Phase 1 — local cache (fast path, no network, immediately populates input)
  ///   Phase 2 — backend fetch (authoritative; overwrites local if newer)
  ///
  /// Always emits a [pendingInputText] value (even empty string) so that
  /// InputArea resets its controller when switching to a session with no draft,
  /// preventing the previous session's text from bleeding through.
  Future<void> _loadDraftAsync(String sessionId) async {
    final local = _host.getDraft(sessionId);
    // Always reset the input — empty string clears the field when no draft exists.
    _lastLoadedDraft = local.content;
    setPendingInputText(local.content);

    // If the session didn't carry an agent type (e.g. archived sessions where
    // the backend returns agentType=null), fall back to the agent saved in the
    // session's draft pluck.  This keeps the chip populated after navigation.
    if (_selectedToolMention == null) {
      final savedAgent = _host.getDraftPlucks(sessionId)?['agent'] as String?;
      if (savedAgent != null) {
        _selectedToolMention = kAgentMentionNames[savedAgent] ?? savedAgent;
        notifyListeners();
      }
    }

    final ws = _ws;
    if (ws == null) return;
    try {
      final remote = await ws.getSessionDraft(sessionId);
      // Backend wins if it has content and its timestamp is newer than the
      // local cache (or the local cache has no timestamp, meaning it predates
      // this feature or was never written by this client).
      final useRemote =
          remote.content.isNotEmpty &&
          (local.cachedAt == null || remote.updatedAt.isAfter(local.cachedAt!));
      if (useRemote && remote.content != local.content) {
        _lastLoadedDraft = remote.content;
        _host.saveDraft(sessionId, remote.content);
        setPendingInputText(remote.content);
      }
    } catch (_) {
      // Network failure — local cache is sufficient.
    }
  }

  /// Load the new-session pane draft from local storage (local-only; no
  /// backend fetch since the new-session pane has no session ID yet).
  ///
  /// Only sets [pendingInputText] when a non-empty draft exists; leaves it null
  /// if there is nothing to restore.
  ///
  /// Also restores pluck chip selections (agent, project, worktree) saved
  /// alongside the text.  Draft-pluck values take precedence over the async
  /// worker-defaults applied by [_applyWorkerDefaults], since they represent
  /// the explicit state the user left the pane in.
  Future<void> _loadNewSessionDraftAsync(String workerId) async {
    final local = _host.getDraft('new_$workerId');
    _lastLoadedDraft = local.content;
    if (local.content.isNotEmpty) setPendingInputText(local.content);

    final plucks = _host.getDraftPlucks('new_$workerId');
    if (plucks != null) {
      var changed = false;
      final agent = plucks['agent'] as String?;
      if (agent != null) {
        _selectedToolMention = kAgentMentionNames[agent] ?? agent;
        changed = true;
      }
      final project = plucks['project'] as String?;
      if (project != null) {
        _selectedProjectName = project;
        // _selectedProjectPath is intentionally not restored: it requires
        // server-side validation that happens via _applyWorkerDefaults.
        changed = true;
      }
      final worktree = plucks['worktree'] as String?;
      if (worktree != null) {
        _view.pendingWorktreePath = worktree;
        changed = true;
      }
      if (changed) notifyListeners();
    }
  }

  /// Apply a draft update pushed from the backend (cross-client sync).
  ///
  /// Only overwrites the input if the user has not typed anything since the
  /// last load (i.e. the current controller text still matches
  /// [_lastLoadedDraft]). This prevents clobbering an actively-typed draft.
  void applyRemoteDraft(String sessionId, String content) {
    if (_sessionId != sessionId) return;
    // Multi-pane / active-edit guard: skip if the user has modified the input.
    final currentText = _draftProvider?.call() ?? '';
    if (currentText != _lastLoadedDraft) return;
    _lastLoadedDraft = content;
    _host.saveDraft(sessionId, content);
    setPendingInputText(content);
  }

  // Message display, streaming, todos, queue and pagination all live in the
  // owned [PaneMessageStore]; these getters forward to it.
  List<DisplayMessage> get messages => _msg.messages;
  bool get loadingMore => _msg.loadingMore;
  bool get hasMoreMessages => _msg.hasMoreMessages;
  int get totalMessageCount => _msg.totalMessageCount;
  List<QueuedMessage> get queuedMessages => _msg.queuedMessages;
  List<TodoItem> get todos => _msg.todos;

  // Right panel state — which panel is open (null = closed).
  // Recognised keys: "todo", "project", "statistics".
  String? _activeRightPanel;
  String? get activeRightPanel => _activeRightPanel;
  double _rightPanelWidth = 260;
  double get rightPanelWidth => _rightPanelWidth;
  static const double rightPanelMinWidth = 180;

  /// Absolute fallback cap. The actual max is typically constrained to a
  /// fraction of the available row width by the caller (see session_pane).
  static const double rightPanelMaxWidth = 2000;

  /// Maximum fraction of the available row width the right panel may occupy.
  static const double rightPanelMaxFraction = 0.75;

  // Kept for backwards compat — derived from _activeRightPanel.
  bool get todoPanelVisible => _activeRightPanel == 'todo';
  double get todoPanelWidth => _rightPanelWidth;
  static const double todoPanelMinWidth = rightPanelMinWidth;
  static const double todoPanelMaxWidth = rightPanelMaxWidth;

  /// The [WorktreeInfo] for the session currently shown in this pane, or null.
  WorktreeInfo? get currentWorktreeInfo {
    if (_sessionId == null) return null;
    return _host.sessions
        .cast<SessionInfo?>()
        .firstWhere((s) => s?.sessionId == _sessionId, orElse: () => null)
        ?.worktreeInfo;
  }

  /// Pending ``ScheduleWakeup`` calls for the session currently shown in this
  /// pane, fire-time order.  Empty when none are queued or the pane has no
  /// real session yet.  Drives the live countdown entries in the activity
  /// strip above the input field.
  List<ScheduledWake> get currentScheduledWakes {
    if (_sessionId == null) return const [];
    return _host.sessions
            .cast<SessionInfo?>()
            .firstWhere((s) => s?.sessionId == _sessionId, orElse: () => null)
            ?.scheduledWakes ??
        const [];
  }

  /// The selected worktree path for the session currently shown in this pane,
  /// or null when no worktree is explicitly selected.
  String? get currentSelectedWorktreePath {
    if (_sessionId == null) return null;
    return _host.sessions
        .cast<SessionInfo?>()
        .firstWhere((s) => s?.sessionId == _sessionId, orElse: () => null)
        ?.selectedWorktreePath;
  }

  /// The main project path confirmed by the server for the current session,
  /// or null when no project is attached.
  String? get currentMainProjectPath {
    if (_sessionId == null) return null;
    return _host.sessions
        .cast<SessionInfo?>()
        .firstWhere((s) => s?.sessionId == _sessionId, orElse: () => null)
        ?.mainProjectPath;
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

  // Terminal statuses for sessions
  static const _terminalStatuses = {'completed', 'failed', 'cancelled'};

  /// Whether the current session is driven by an agent that supports plugin slash commands.
  bool get isClaudeCodeSession =>
      _msg.agentToolName == 'claude_code' || _msg.agentToolName == 'opencode';

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

  /// Loads the per-worker cached project and agent for [workerId] and applies
  /// them as pre-session defaults.  Validates the cached project against the
  /// worker's live project list so a project that no longer exists on that
  /// worker is silently skipped rather than causing a backend error.
  ///
  /// Safe to call from async contexts — guards against disposal mid-flight.
  Future<void> _applyWorkerDefaults(String workerId) async {
    _loadingWorkerDefaults = true;
    notifyListeners();
    try {
      // Agent: honour the configured per-worker default exactly. When the
      // worker is set to "No preference" (null) the new session starts with
      // no agent badge — last-used auto-resurrection was deliberately removed
      // because it produced surprising "I never picked Claude Code, why is it
      // here" behaviour.
      _selectedToolMention = _host.defaultAgentForWorker(workerId);

      // Project: validate existence on the worker before applying.
      final lastProject = _host.getLastProjectForWorker(workerId);
      if (lastProject != null) {
        final resolvedPath = await _host.resolveProjectOnWorker(
          workerId,
          lastProject,
        );
        if (_disposed) return;
        // Only apply the worker's cached project default if the user has not
        // already had a project restored via draft pluck hydration (which runs
        // synchronously before this async continuation resumes).
        if (resolvedPath != null && _selectedProjectName == null) {
          _selectedProjectName = lastProject;
          _selectedProjectPath = resolvedPath;
        }
        // If resolvedPath is null the project doesn't exist on this worker;
        // leave _selectedProjectName null so no stale value is shown.
      }
    } finally {
      if (!_disposed) {
        _loadingWorkerDefaults = false;
        notifyListeners();
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Caveman mode — indicates the active tool has caveman mode enabled.
  // The preview badge is dismissed per-pane until the next page load.
  // ---------------------------------------------------------------------------

  bool _cavemanDismissed = false;

  /// True when the active session's tool has caveman mode enabled AND the user
  /// has not dismissed the preview badge for this pane.
  bool get isCavemanActive {
    if (_cavemanDismissed) return false;
    // Infer from the current session's badge list if available.
    final sid = sessionId;
    if (sid == null) {
      // New-chat pane: check the default worker's caveman setting via host.
      return _host.isWorkerCavemanActive(workerId ?? _host.defaultWorkerId);
    }
    final session = _host.sessionById(sid);
    return session?.badges.any((b) => b.type == 'caveman') ?? false;
  }

  /// Dismiss the caveman preview badge for this pane session.
  void setCavemanDisabled(bool dismissed) {
    _cavemanDismissed = dismissed;
    notifyListeners();
  }

  /// Reset caveman dismiss state (e.g. when a new session starts).
  void _resetCavemanDismiss() {
    _cavemanDismissed = false;
  }

  /// Set the target worker for new chats.  Clears any project/tool state that
  /// belongs to the previous worker and asynchronously loads defaults for the
  /// new worker.
  void setTargetWorker(String? workerId) {
    _workerId = workerId;
    _selectedProjectName = null;
    _selectedProjectPath = null;
    _projectNameError = null;
    _selectedToolMention = null;
    _view.pendingWorktreePath = null;
    if (workerId != null) {
      // Apply sync fallback immediately so the chip isn't blank during the
      // async validation phase.
      _selectedToolMention = _host.defaultAgentForWorker(workerId);
      _applyWorkerDefaults(workerId);
    }
    notifyListeners();
  }

  // --- Session operations ---

  void sendPrompt(String text, {List<Map<String, dynamic>>? attachments}) {
    if (!_host.connected || text.trim().isEmpty) return;
    if (_sessionId == null && !_readyForNewChat) {
      _readyForNewChat = true;
    }

    _msg.finalizeStream();
    _msg.exitAgentMode();
    // Prepend #ToolName to the prompt so the backend sees a normal tool mention.
    final toolMention = _selectedToolMention;
    final effectiveText = toolMention != null ? '#$toolMention $text' : text;

    // When using the chip the text field is already clean (no #mention).
    // When the user typed a #mention directly, strip it from the displayed
    // content so routing markers never appear in chat history.
    final displayContent = toolMention != null
        ? text
        : text
              .replaceAllMapped(RegExp(r'(^|\s)#\S+'), (m) => m.group(1) ?? '')
              .trim();

    _msg.addLocalUserMessage(
      DisplayMessage(
        type: DisplayMessageType.user,
        content: displayContent,
        sessionId: _sessionId,
        pendingLocalEcho: true,
        attachments: attachments,
      ),
    );
    pendingAck = _sessionId == null; // new chat — expect ack
    notifyListeners();

    // Pass the pre-selected worktree path only for new sessions (no session yet).
    // After the session exists the user adjusts the worktree via the Project panel.
    final worktreeToSend = _sessionId == null
        ? _view.pendingWorktreePath
        : null;
    // Pass the pending task ID only for new sessions so the backend can inject
    // plan context. Cleared immediately after sending.
    final taskIdToSend = _sessionId == null ? _view.pendingTaskId : null;
    _ws?.sendPrompt(
      effectiveText,
      _sessionId,
      attachments: attachments,
      projectName: _selectedProjectName,
      selectedWorktreePath: worktreeToSend,
      taskId: taskIdToSend,
      // When chip used: text is already clean; send it as displayText so the
      // server echo matches the local echo for deduplication.
      // When typed directly: send stripped content if it differs, so the
      // server echoes back the same clean string for content-based dedup.
      displayText: toolMention != null
          ? text
          : (displayContent != text ? displayContent : null),
    );
    _clearPendingTaskId();

    // Clear the draft now that the message has been sent.
    // New-session panes: handleAck clears the new_{workerId} draft on ack.
    // Existing sessions: clear immediately so stale draft doesn't persist.
    if (_sessionId != null) {
      _host.clearDraft(_sessionId!);
      _host.clearDraftPlucks(_sessionId!);
      final ws = _ws;
      if (ws != null) {
        // ignore: discarded_futures
        ws.saveSessionDraft(_sessionId!, '');
      }
    }
    // Reset guard so subsequent typing compares against empty string.
    _lastLoadedDraft = '';
  }

  void switchSession(String sessionId, {bool recordHistory = true}) {
    if (sessionId == _sessionId) return;

    // Snapshot and persist the current draft before clearing state.
    // _sessionId still holds the OLD session ID here — that's intentional.
    _saveDraftIfChanged();

    // Push current session to nav history for back-navigation
    if (recordHistory && _sessionId != null) {
      pushNavHistory(
        PaneNavEntry(paneType: PaneType.chat, sessionId: _sessionId),
      );
    }

    final oldSessionId = _sessionId;
    final oldWorkerId = _workerId;

    _msg.resetForSwitch();
    _activeRightPanel = null;
    _sessionEnded = false;
    _sessionPaused = false;
    _pausedReason = null;
    _runningSubprocess = null;
    _activeMonitors.clear();
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
      _msg.loadSessionMessages(sessionId);
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

    // Restore the agent chip from the session's known agent type so that
    // switching to an existing session (e.g. after reconnect) shows which
    // agent drove that session without requiring the user to re-select it.
    // Store the raw internal name (e.g. "claude_code") — display-name mapping
    // is handled by the input area and badge composer independently.
    _selectedToolMention = session?.agentType;

    notifyListeners();

    // Kick off two-phase draft load for the new session.
    // ignore: discarded_futures
    _loadDraftAsync(sessionId);
  }

  /// Resubscribes the pane to its current session on the output WebSocket.
  ///
  /// Called after an auto-reconnect where the pane already holds a session ID
  /// but the underlying WS connection was replaced. [switchSession] would
  /// return early in that case (same ID → no-op), leaving the pane without
  /// a live subscription and causing the UI to hang indefinitely.
  void resubscribeSession() {
    if (_sessionId == null) return;

    _msg.resetForSwitch();
    _runningSubprocess = null;
    _activeMonitors.clear();

    final session = _host.sessions.cast<SessionInfo?>().firstWhere(
      (s) => s!.sessionId == _sessionId,
      orElse: () => null,
    );

    _sessionPaused = session?.status == 'paused';
    _pausedReason = _sessionPaused ? session?.pausedReason : null;

    _host.muteSessionSound(_sessionId!);
    _ws?.subscribe(_sessionId!);
    if (_workerId != null) {
      _host.markSubscribed(_sessionId!, workerId: _workerId!);
    }
    notifyListeners();
  }

  void goHome() {
    // Snapshot and persist the current session's draft before clearing state.
    _saveDraftIfChanged();

    final oldSessionId = _sessionId;
    final oldWorkerId = _workerId;

    _msg.resetForSwitch();
    _sessionId = null;
    _readyForNewChat = false;
    _sessionEnded = false;
    _sessionPaused = false;
    _pausedReason = null;
    _runningSubprocess = null;
    _activeMonitors.clear();
    _activeRightPanel = null;
    _selectedProjectName = null;
    _selectedProjectPath = null;
    _projectNameError = null;
    _selectedToolMention = null;
    _view.pendingWorktreePath = null;

    if (oldSessionId != null && oldWorkerId != null) {
      _host.requestUnsubscribe(oldSessionId, oldWorkerId);
    }

    notifyListeners();

    // Load the new-session pane draft for the current worker (local-only).
    if (_workerId != null) {
      // ignore: discarded_futures
      _loadNewSessionDraftAsync(_workerId!);
    }
  }

  void startNewChat() {
    // Snapshot and persist the current session's draft before clearing state.
    _saveDraftIfChanged();

    final oldSessionId = _sessionId;
    final oldWorkerId = _workerId;

    _msg.resetForSwitch();
    _sessionId = null;
    _readyForNewChat = true;
    _sessionEnded = false;
    _sessionPaused = false;
    _pausedReason = null;
    _runningSubprocess = null;
    _activeMonitors.clear();
    _pendingInputText = null;
    _selectedToolMention = null;
    _selectedProjectName = null;
    _selectedProjectPath = null;
    _projectNameError = null;
    _activeRightPanel = null;
    _view.pendingWorktreePath = null;
    _view.pendingTaskId = null;
    // Apply per-worker cached defaults (project + agent) for the new chat.
    // The sync fallback ensures the tool chip isn't blank during async work.
    final targetWorker = _workerId ?? _host.defaultWorkerId;
    if (targetWorker != null) {
      _selectedToolMention = _host.defaultAgentForWorker(targetWorker);
      _applyWorkerDefaults(targetWorker);
    }

    if (oldSessionId != null && oldWorkerId != null) {
      _host.requestUnsubscribe(oldSessionId, oldWorkerId);
    }

    notifyListeners();

    // Load the new-session pane draft for the target worker (local-only).
    if (targetWorker != null) {
      // ignore: discarded_futures
      _loadNewSessionDraftAsync(targetWorker);
    }
  }

  void refresh() => notifyListeners();

  /// Clear the displayed message list for this pane (client-side only).
  /// Does not affect the server-side session or database history.
  void clearMessages() => _msg.clearMessages();

  // --- Ack handling ---

  void handleAck(
    String sessionId, {
    String? workerId,
    bool queued = false,
    String? queuedId,
  }) {
    // Promote the optimistic pendingLocalEcho DisplayMessage to a
    // [QueuedMessage] when the server signalled the prompt was queued.
    // Queued prompts do not start a new stream, so skip `finalizeStream()`
    // to avoid closing the still-running prior turn's assistant message.
    if (queued && queuedId != null) {
      // Take the newest pending-local-echo user message as the submission
      // being acknowledged; its content is what we show in the pinned queue.
      for (int i = messages.length - 1; i >= 0; i--) {
        final m = messages[i];
        if (m.type == DisplayMessageType.user && m.pendingLocalEcho) {
          _msg.promoteLocalEchoToQueued(queuedId: queuedId, content: m.content);
          break;
        }
      }
      pendingAck = false;
      _sessionId = sessionId;
      if (workerId != null) _workerId = workerId;
      notifyListeners();
      return;
    }
    _msg.finalizeStream();
    pendingAck = false;
    if (sessionId != _sessionId) {
      _msg.addSystemMessage('[ACK] Session: $sessionId');
    }
    // Record whether this ack created a brand-new session (vs. re-acking an
    // existing one) so we can clear the new-session draft below.
    final wasNewSession = _sessionId == null;
    _sessionId = sessionId;
    if (workerId != null) _workerId = workerId;
    _readyForNewChat = false;
    if (wasNewSession) _resetCavemanDismiss();

    // Fire the new-session callback (e.g. to attach task after creation).
    if (_onNewSessionAck != null) {
      _onNewSessionAck!(sessionId);
      _onNewSessionAck = null;
    }

    // The user sent the new-session draft as a prompt — clear the local cache
    // so the new-session pane starts blank next time.
    if (wasNewSession && _workerId != null) {
      _host.clearDraft('new_$_workerId');
      _host.clearDraftPlucks('new_$_workerId');
      _lastLoadedDraft = '';
    }

    // Persist the agent mention to the session's draft plucks.  This lets
    // switchSession restore the chip for archived sessions where the backend
    // returns agentType=null.
    if (_selectedToolMention != null) {
      _host.saveDraftPlucks(sessionId, {'agent': _selectedToolMention!});
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
        _msg.finalizeStream();
        _sessionId = null;
        _readyForNewChat = true;
        _sessionEnded = false;
        _msg.messages.clear();

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
      _msg.addSystemMessage('Failed to cancel session: $e', isError: true);
    }
  }

  Future<void> endSession(String sessionId) async {
    try {
      await _ws?.endSession(sessionId);
      _msg.finalizeStream();
      if (_sessionId == sessionId) {
        _sessionEnded = true;
      }
      _host.refreshSessions();
      notifyListeners();
    } catch (e) {
      _msg.addSystemMessage('Failed to end session: $e', isError: true);
    }
  }

  Future<void> pauseSession(String sessionId) async {
    try {
      await _ws?.pauseSession(sessionId);
      _host.refreshSessions();
    } catch (e) {
      _msg.addSystemMessage('Failed to pause session: $e', isError: true);
    }
  }

  Future<void> resumeSession(String sessionId) async {
    try {
      await _ws?.resumeSession(sessionId);
      _host.refreshSessions();
    } catch (e) {
      _msg.addSystemMessage('Failed to resume session: $e', isError: true);
    }
  }

  Future<void> interruptSubprocess() async {
    final sid = _sessionId;
    if (sid == null) return;
    try {
      _ws?.interruptSubprocess(sid);
    } catch (e) {
      _msg.addSystemMessage(
        'Failed to interrupt subprocess: $e',
        isError: true,
      );
    }
  }

  void handleSessionPaused(String? sessionId, {String? reason}) {
    if (_sessionId == sessionId) {
      _msg.finalizeStream();
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
      _msg.addSystemMessage('Failed to restore session: $e', isError: true);
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
      _msg.addSystemMessage('Failed to rename session: $e', isError: true);
    }
  }

  void answerQuestion(DisplayMessage msg, Map<String, String> answers) {
    if (!_host.connected) return;
    msg.selectedAnswers = answers;
    msg.finished = true;
    msg.expanded = true; // show the answers in the collapsible body by default
    _ws?.answerQuestion(msg.sessionId ?? _sessionId, answers);
    notifyListeners();
  }

  /// Send a mid-turn interactive response to Claude Code (plan mode, etc.)
  /// without opening a new agent group or adding a user message.
  void sendInteractiveResponse(
    DisplayMessage msg,
    String text, {
    bool accepted = true,
  }) {
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
    _msg.loadSessionMessages(sessionId);
    _host.refreshSessions();
  }

  void handleSessionEnded(String? sessionId) {
    if (_sessionId == sessionId) {
      _msg.finalizeStream();
      _sessionEnded = true;
      _msg.finishPendingQuestions();
    }
    _host.refreshSessions();
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

  /// Replace the todo list (forwards to the message store).
  void updateTodos(List<TodoItem> todos) => _msg.updateTodos(todos);

  /// Clear the todo list (forwards to the message store).
  void clearTodos() => _msg.clearTodos();

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
        _activeRightPanel == 'project') {
      return;
    }
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

  // ---------------------------------------------------------------------------
  // Message / streaming surface — forwarded to the owned [PaneMessageStore].
  // The streaming output handlers and chat widgets call these on the pane.
  // ---------------------------------------------------------------------------

  /// The message currently being streamed into (assistant text or tool block).
  DisplayMessage? get lastStreamMessage => _msg.lastStreamMessage;

  /// Enter agent mode — subsequent tool calls are auto-grouped.
  void startAgentGroup(
    String name,
    Map<String, dynamic>? input, {
    String? displayName,
  }) => _msg.startAgentGroup(name, input, displayName: displayName);

  /// Exit agent mode — close any open tool sub-group.
  void endAgentGroup() => _msg.endAgentGroup();

  /// Append streamed assistant text.
  void appendAssistantChunk(String text) => _msg.appendAssistantChunk(text);

  /// Open a new streaming tool block.
  void startToolBlock(
    String name,
    Map<String, dynamic>? input, {
    String? displayName,
  }) => _msg.startToolBlock(name, input, displayName: displayName);

  /// Append output text to the current tool block.
  void appendToolOutput(String text, {bool isError = false}) =>
      _msg.appendToolOutput(text, isError: isError);

  /// Attach a unified diff to the most recent tool block.
  void applyDiffToLastToolBlock(String diff) =>
      _msg.applyDiffToLastToolBlock(diff);

  /// Finish the current streaming message.
  void finalizeStream() => _msg.finalizeStream();

  /// True when a local user echo matches [echoContent] (skip the server echo).
  bool consumeLocalUserMessage(String echoContent) =>
      _msg.consumeLocalUserMessage(echoContent);

  /// Promote an optimistic local echo into a pinned queued message.
  void promoteLocalEchoToQueued({
    required String queuedId,
    required String content,
  }) => _msg.promoteLocalEchoToQueued(queuedId: queuedId, content: content);

  /// Apply a ``message_queued`` event.
  void applyMessageQueued(Map<String, dynamic> msg) =>
      _msg.applyMessageQueued(msg);

  /// Apply a ``message_dequeued`` event.
  void applyMessageDequeued(String queuedId) =>
      _msg.applyMessageDequeued(queuedId);

  /// Apply a ``message_queued_updated`` event.
  void applyMessageQueuedUpdated(Map<String, dynamic> msg) =>
      _msg.applyMessageQueuedUpdated(msg);

  /// Replace the local queue with the authoritative server snapshot.
  void applyQueueSnapshot(List<Map<String, dynamic>> snapshot) =>
      _msg.applyQueueSnapshot(snapshot);

  /// Handle a ``cancel_ack`` (visible state driven by the dequeue stream).
  void applyCancelAck(Map<String, dynamic> msg) => _msg.applyCancelAck(msg);

  /// Handle an ``edit_ack`` (visible update arrives via queued_updated).
  void applyEditAck(Map<String, dynamic> msg) => _msg.applyEditAck(msg);

  /// Request cancellation of a queued message.
  void cancelQueuedMessage(String queuedId) =>
      _msg.cancelQueuedMessage(queuedId);

  /// Update the text of a queued message.
  void editQueuedMessage(String queuedId, String content) =>
      _msg.editQueuedMessage(queuedId, content);

  /// Append a finished display message at the top level.
  void addDisplayMessage(DisplayMessage msg) => _msg.addDisplayMessage(msg);

  /// Add a display message respecting agent group nesting.
  void addDisplayMessageInStream(DisplayMessage msg) =>
      _msg.addDisplayMessageInStream(msg);

  /// Append a system/error notice to the message list.
  void addSystemMessage(String text, {bool isError = false}) =>
      _msg.addSystemMessage(text, isError: isError);

  /// Load the next older page of history and prepend it to the message list.
  Future<void> loadOlderMessages() => _msg.loadOlderMessages();

  @override
  void dispose() {
    _disposed = true;
    _msg.disposeTimers();
    super.dispose();
  }
}
