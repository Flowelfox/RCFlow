import 'server_url.dart';
import 'rest/rest_client.dart';
import 'web_socket_transport.dart';

/// Worker connection facade: composes the raw [WebSocketTransport] and the
/// HTTP [RestClient], exposing the higher-level command + REST surface the
/// rest of the app (and the test mocks) use.
class WebSocketService {
  final RestClient _rest = RestClient();
  final WebSocketTransport _transport = WebSocketTransport();

  Stream<Map<String, dynamic>> get inputMessages => _transport.inputMessages;
  Stream<Map<String, dynamic>> get outputMessages => _transport.outputMessages;
  Stream<bool> get connectionStatus => _transport.connectionStatus;

  bool get isConnected => _transport.isConnected;

  /// Connect both input and output WebSocket channels.
  /// [host] is a raw host string (e.g. "192.168.1.100:8765" or "example.com").
  /// When [secure] is true, uses wss://. When [allowSelfSigned] is true,
  /// self-signed TLS certificates are accepted.
  Future<void> connect(
    String host,
    String apiKey, {
    bool secure = false,
    bool allowSelfSigned = true,
  }) async {
    final url = ServerUrl(rawHost: host, apiKey: apiKey, secure: secure);
    _rest.configure(url, allowSelfSigned: allowSelfSigned);
    await _transport.connect(
      url,
      secure: secure,
      allowSelfSigned: allowSelfSigned,
    );
  }

  void sendPrompt(
    String text,
    String? sessionId, {
    List<Map<String, dynamic>>? attachments,
    String? projectName,
    String? selectedWorktreePath,
    String? taskId,
    String? displayText,
  }) {
    final msg = <String, dynamic>{
      'type': 'prompt',
      'text': text,
      'session_id': sessionId,
      if (attachments != null && attachments.isNotEmpty)
        'attachments': attachments,
      'project_name': ?projectName,
      'selected_worktree_path': ?selectedWorktreePath,
      'task_id': ?taskId,
      'display_text': ?displayText,
    };
    _transport.sendInput(msg);
  }

  /// Send a start_plan_session message over the input WebSocket.
  /// The server responds with an ack containing the session_id, which is handled
  /// by the standard ack routing in AppState._handleInputMessage.
  void startPlanSession(
    String taskId, {
    String? projectName,
    String? selectedWorktreePath,
  }) {
    final msg = <String, dynamic>{
      'type': 'start_plan_session',
      'task_id': taskId,
      'project_name': ?projectName,
      'selected_worktree_path': ?selectedWorktreePath,
    };
    _transport.sendInput(msg);
  }

  /// Start a PR-assist session over the input WebSocket. For the read-only
  /// kinds (``summary`` / ``explain``) only the PR id, kind and optional file
  /// path are sent. For ``fix`` the comment body plus the worktree/project the
  /// full-perms agent should edit are also supplied. The server responds with
  /// an ack containing the session_id, handled by the standard ack routing in
  /// AppState._handleInputMessage.
  void startPrAssist(
    String prId,
    String kind, {
    String? filePath,
    String? commentBody,
    int? line,
    String? projectName,
    String? projectPath,
    String? selectedWorktreePath,
    String? agent,
  }) {
    final msg = <String, dynamic>{
      'type': 'start_pr_assist',
      'pr_id': prId,
      'kind': kind,
      'file_path': ?filePath,
      'comment_body': ?commentBody,
      'line': ?line,
      'project_name': ?projectName,
      'project_path': ?projectPath,
      'selected_worktree_path': ?selectedWorktreePath,
      'agent': ?agent,
    };
    _transport.sendInput(msg);
  }

  Future<Map<String, dynamic>> uploadAttachment({
    required List<int> bytes,
    required String fileName,
    required String mimeType,
  }) => _rest.uploadAttachment(
    bytes: bytes,
    fileName: fileName,
    mimeType: mimeType,
  );

  void subscribe(String sessionId) {
    final msg = {'type': 'subscribe', 'session_id': sessionId};
    _transport.sendOutput(msg);
  }

  void unsubscribe(String sessionId) {
    final msg = {'type': 'unsubscribe', 'session_id': sessionId};
    _transport.sendOutput(msg);
  }

  void answerQuestion(String? sessionId, Map<String, String> answers) {
    final msg = {
      'type': 'question_answer',
      'session_id': sessionId,
      'answers': answers,
    };
    _transport.sendInput(msg);
  }

  void sendPermissionResponse({
    required String sessionId,
    required String requestId,
    required String decision,
    required String scope,
    String? pathPrefix,
  }) {
    final msg = <String, dynamic>{
      'type': 'permission_response',
      'session_id': sessionId,
      'request_id': requestId,
      'decision': decision,
      'scope': scope,
      'path_prefix': ?pathPrefix,
    };
    _transport.sendInput(msg);
  }

  void sendInteractiveResponse(
    String sessionId,
    String text, {
    bool accepted = true,
  }) {
    final msg = {
      'type': 'interactive_response',
      'session_id': sessionId,
      'text': text,
      'accepted': accepted,
    };
    _transport.sendInput(msg);
  }

  /// Send an interrupt_subprocess message over the input WebSocket.
  /// Kills any running Claude Code / Codex subprocess without pausing the
  /// session. The session remains ACTIVE and ready for new prompts.
  void interruptSubprocess(String sessionId) {
    final msg = {'type': 'interrupt_subprocess', 'session_id': sessionId};
    _transport.sendInput(msg);
  }

  /// Stop a live Claude Code Monitor watch identified by its tool_use id.
  /// The server responds with an ``ack`` and emits ``monitor_end`` with
  /// ``reason="cancelled"`` for the matching block.
  void cancelMonitor(String sessionId, String monitorId) {
    final msg = {
      'type': 'cancel_monitor',
      'session_id': sessionId,
      'monitor_id': monitorId,
    };
    _transport.sendInput(msg);
  }

  /// Request cancellation of a queued user message that has not yet been
  /// delivered to the agent.  The server may respond with a ``cancel_ack``
  /// carrying ``ok: false`` when the message was already dequeued; the UI
  /// handles that gracefully.
  void cancelQueued(String sessionId, String queuedId) {
    final msg = {
      'type': 'cancel_queued',
      'session_id': sessionId,
      'queued_id': queuedId,
    };
    _transport.sendInput(msg);
  }

  /// Edit the text of a queued user message in place (text only; attachments
  /// are fixed at enqueue time).  Server responds with ``edit_ack``.
  void editQueued(
    String sessionId,
    String queuedId,
    String content, {
    String? displayContent,
  }) {
    final msg = <String, dynamic>{
      'type': 'edit_queued',
      'session_id': sessionId,
      'queued_id': queuedId,
      'content': content,
    };
    if (displayContent != null) {
      msg['display_content'] = displayContent;
    }
    _transport.sendInput(msg);
  }

  void listSessions({int offset = 0, int limit = 30}) {
    _transport.sendOutput({
      'type': 'list_sessions',
      'offset': offset,
      'limit': limit,
    });
  }

  void listTasks() {
    _transport.sendOutput({'type': 'list_tasks'});
  }

  Future<Map<String, dynamic>> fetchSessionMessages(
    String sessionId, {
    int? before,
    int? limit,
  }) => _rest.fetchSessionMessages(sessionId, before: before, limit: limit);

  Future<({String content, DateTime updatedAt})> getSessionDraft(
    String sessionId,
  ) => _rest.getSessionDraft(sessionId);

  Future<void> saveSessionDraft(String sessionId, String content) =>
      _rest.saveSessionDraft(sessionId, content);

  Future<void> endSession(String sessionId) => _rest.endSession(sessionId);

  Future<void> cancelSession(String sessionId) =>
      _rest.cancelSession(sessionId);

  Future<void> pauseSession(String sessionId) => _rest.pauseSession(sessionId);

  Future<void> resumeSession(String sessionId) =>
      _rest.resumeSession(sessionId);

  Future<void> restoreSession(String sessionId) =>
      _rest.restoreSession(sessionId);

  Future<Map<String, dynamic>> fetchServerInfo() => _rest.fetchServerInfo();

  Future<List<Map<String, String>>> fetchProjects({String? query}) =>
      _rest.fetchProjects(query: query);

  Future<List<Map<String, String>>> fetchTools({String? query}) =>
      _rest.fetchTools(query: query);

  Future<List<Map<String, String>>> fetchArtifactSuggestions({String? query}) =>
      _rest.fetchArtifactSuggestions(query: query);

  Future<List<Map<String, String>>> fetchSlashCommands({String? query}) =>
      _rest.fetchSlashCommands(query: query);

  // ---------------------------------------------------------------------------
  // RCFlow-managed plugin management
  // ---------------------------------------------------------------------------

  Future<List<Map<String, dynamic>>> fetchRCFlowPlugins() =>
      _rest.fetchRCFlowPlugins();

  Future<Map<String, dynamic>> installRCFlowPlugin(
    String source, {
    String? name,
  }) => _rest.installRCFlowPlugin(source, name: name);

  Future<void> uninstallRCFlowPlugin(String name) =>
      _rest.uninstallRCFlowPlugin(name);

  // ---------------------------------------------------------------------------
  // Tool-scoped plugin management (canonical v2 endpoints)
  // ---------------------------------------------------------------------------

  Future<List<Map<String, dynamic>>> fetchToolPlugins(String toolName) =>
      _rest.fetchToolPlugins(toolName);

  Future<Map<String, dynamic>> installToolPlugin(
    String toolName,
    String source, {
    String? name,
  }) => _rest.installToolPlugin(toolName, source, name: name);

  Future<void> uninstallToolPlugin(String toolName, String name) =>
      _rest.uninstallToolPlugin(toolName, name);

  Future<void> setToolPluginEnabled(
    String toolName,
    String name,
    bool enabled,
  ) => _rest.setToolPluginEnabled(toolName, name, enabled);

  Future<void> renameSession(String sessionId, String? title) =>
      _rest.renameSession(sessionId, title);

  Future<void> reorderSession(String sessionId, {String? afterSessionId}) =>
      _rest.reorderSession(sessionId, afterSessionId: afterSessionId);

  Future<List<Map<String, dynamic>>> fetchConfig() => _rest.fetchConfig();

  Future<Map<String, dynamic>> fetchModels({
    required String provider,
    required String scope,
    bool refresh = false,
  }) => _rest.fetchModels(provider: provider, scope: scope, refresh: refresh);

  Future<List<Map<String, dynamic>>> updateConfig(
    Map<String, dynamic> updates,
  ) => _rest.updateConfig(updates);

  Future<Map<String, dynamic>> fetchTimeSeries({
    required String zoom,
    required DateTime start,
    required DateTime end,
    String? sessionId,
  }) => _rest.fetchTimeSeries(
    zoom: zoom,
    start: start,
    end: end,
    sessionId: sessionId,
  );

  Future<Map<String, dynamic>> fetchWorkerTelemetry() =>
      _rest.fetchWorkerTelemetry();

  Future<Map<String, dynamic>?> fetchSessionTelemetry(String sessionId) =>
      _rest.fetchSessionTelemetry(sessionId);

  Future<Map<String, dynamic>> fetchToolStatus() => _rest.fetchToolStatus();

  Future<Map<String, dynamic>> fetchCodingAgentAuthPreflight() =>
      _rest.fetchCodingAgentAuthPreflight();

  Future<Map<String, dynamic>> triggerToolUpdate() => _rest.triggerToolUpdate();

  Future<Map<String, dynamic>> triggerSingleToolUpdate(
    String toolName, {
    void Function(Map<String, dynamic> event)? onProgress,
  }) => _rest.triggerSingleToolUpdate(toolName, onProgress: onProgress);

  Future<Map<String, dynamic>> installManagedTool(
    String toolName, {
    void Function(Map<String, dynamic> event)? onProgress,
  }) => _rest.installManagedTool(toolName, onProgress: onProgress);

  Future<void> codexLogin({
    bool deviceCode = false,
    void Function(Map<String, dynamic> event)? onProgress,
  }) => _rest.codexLogin(deviceCode: deviceCode, onProgress: onProgress);

  Future<Map<String, dynamic>> codexLoginStatus() => _rest.codexLoginStatus();

  Future<Map<String, dynamic>> claudeCodeLogin() => _rest.claudeCodeLogin();

  Future<Map<String, dynamic>> claudeCodeLoginCode(String code) =>
      _rest.claudeCodeLoginCode(code);

  Future<Map<String, dynamic>> claudeCodeLoginStatus() =>
      _rest.claudeCodeLoginStatus();

  Future<void> claudeCodeLogout() => _rest.claudeCodeLogout();

  Future<Map<String, dynamic>> uninstallManagedTool(String toolName) =>
      _rest.uninstallManagedTool(toolName);

  Future<Map<String, dynamic>> fetchToolSettings(String toolName) =>
      _rest.fetchToolSettings(toolName);

  Future<Map<String, dynamic>> updateToolSettings(
    String toolName,
    Map<String, dynamic> updates,
  ) => _rest.updateToolSettings(toolName, updates);

  // ---------------------------------------------------------------------------
  // Task CRUD
  // ---------------------------------------------------------------------------

  Future<Map<String, dynamic>> createTask({
    required String title,
    String? description,
    String source = 'user',
    String? sessionId,
  }) => _rest.createTask(
    title: title,
    description: description,
    source: source,
    sessionId: sessionId,
  );

  Future<Map<String, dynamic>> updateTask(
    String taskId, {
    String? title,
    String? description,
    String? status,
  }) => _rest.updateTask(
    taskId,
    title: title,
    description: description,
    status: status,
  );

  Future<void> deleteTask(String taskId) => _rest.deleteTask(taskId);

  Future<Map<String, dynamic>> attachSessionToTask(
    String taskId,
    String sessionId,
  ) => _rest.attachSessionToTask(taskId, sessionId);

  Future<Map<String, dynamic>> detachSessionFromTask(
    String taskId,
    String sessionId,
  ) => _rest.detachSessionFromTask(taskId, sessionId);

  // ---------------------------------------------------------------------------
  // Linear integration
  // ---------------------------------------------------------------------------

  Future<Map<String, dynamic>> testLinearConnection(String apiKey) =>
      _rest.testLinearConnection(apiKey);

  Future<Map<String, dynamic>> fetchLinearTeams() => _rest.fetchLinearTeams();

  void listLinearIssues() {
    _transport.sendOutput({'type': 'list_linear_issues'});
  }

  void listGithubPrs() {
    _transport.sendOutput({'type': 'list_github_prs'});
  }

  /// Trigger a server-side sync of open pull requests from GitHub. The backend
  /// broadcasts `github_pr_update` for each synced PR. Returns `{"synced": int}`.
  Future<Map<String, dynamic>> syncGithubPrs({
    String? role,
    String? state,
    bool force = false,
  }) => _rest.syncGithubPrs(role: role, state: state, force: force);

  Future<Map<String, dynamic>> fetchGithubStatus() => _rest.fetchGithubStatus();

  Future<Map<String, dynamic>> checkGithubToken(String token) =>
      _rest.checkGithubToken(token);

  Future<Map<String, dynamic>> getGithubRepoDefaults() =>
      _rest.getGithubRepoDefaults();

  Future<Map<String, dynamic>> setGithubRepoDefault(
    String owner,
    String repo,
    bool isDefault,
  ) => _rest.setGithubRepoDefault(owner, repo, isDefault);

  Future<Map<String, dynamic>> getGithubPrFiles(String prId) =>
      _rest.getGithubPrFiles(prId);

  Future<Map<String, dynamic>> getGithubPrProject(String prId) =>
      _rest.getGithubPrProject(prId);

  Future<Map<String, dynamic>> getGithubPrConflicts(String prId) =>
      _rest.getGithubPrConflicts(prId);

  Future<Map<String, dynamic>> getGithubPrConversation(String prId) =>
      _rest.getGithubPrConversation(prId);

  Future<Map<String, dynamic>> postGithubPrConversation(
    String prId,
    String body,
  ) => _rest.postGithubPrConversation(prId, body);

  Future<Map<String, dynamic>> getGithubPrFile(
    String prId,
    String path, {
    String side = 'head',
  }) => _rest.getGithubPrFile(prId, path, side: side);

  Future<Map<String, dynamic>> getGithubPrThreads(String prId) =>
      _rest.getGithubPrThreads(prId);

  Future<Map<String, dynamic>> getGithubPrDraft(String prId) =>
      _rest.getGithubPrDraft(prId);

  Future<Map<String, dynamic>> patchGithubPrDraft(
    String prId, {
    String? event,
    String? body,
  }) => _rest.patchGithubPrDraft(prId, event: event, body: body);

  Future<Map<String, dynamic>> addGithubPrDraftComment(
    String prId, {
    required String path,
    required int line,
    required String side,
    required String body,
    int? startLine,
    String? startSide,
  }) => _rest.addGithubPrDraftComment(
    prId,
    path: path,
    line: line,
    side: side,
    body: body,
    startLine: startLine,
    startSide: startSide,
  );

  Future<Map<String, dynamic>> deleteGithubPrDraftComment(
    String prId,
    int index,
  ) => _rest.deleteGithubPrDraftComment(prId, index);

  Future<Map<String, dynamic>> submitGithubPrReview(
    String prId, {
    required String event,
    String? body,
  }) => _rest.submitGithubPrReview(prId, event: event, body: body);

  Future<Map<String, dynamic>> replyGithubPrComment(
    String prId,
    int commentId,
    String body,
  ) => _rest.replyGithubPrComment(prId, commentId, body);

  Future<Map<String, dynamic>> deleteGithubPrComment(
    String prId,
    int commentId,
  ) => _rest.deleteGithubPrComment(prId, commentId);

  Future<Map<String, dynamic>> resolveGithubPrThread(
    String prId,
    String threadId,
    bool resolved,
  ) => _rest.resolveGithubPrThread(prId, threadId, resolved);

  Future<Map<String, dynamic>> mergeGithubPr(
    String prId, {
    required String method,
    String? commitTitle,
    String? commitMessage,
  }) => _rest.mergeGithubPr(
    prId,
    method: method,
    commitTitle: commitTitle,
    commitMessage: commitMessage,
  );

  /// Open a pull request from a local worktree. Returns `{pr, url}`.
  Future<Map<String, dynamic>> openGithubPr({
    String? selectedWorktreePath,
    String? projectName,
    required String title,
    String body = '',
    String base = 'main',
    String? headBranch,
    String? commitMessage,
    bool draft = false,
  }) => _rest.openGithubPr(
    selectedWorktreePath: selectedWorktreePath,
    projectName: projectName,
    title: title,
    body: body,
    base: base,
    headBranch: headBranch,
    commitMessage: commitMessage,
    draft: draft,
  );

  Future<Map<String, dynamic>> syncLinearIssues() => _rest.syncLinearIssues();

  Future<Map<String, dynamic>> createLinearIssue({
    required String title,
    String? description,
    int priority = 0,
  }) => _rest.createLinearIssue(
    title: title,
    description: description,
    priority: priority,
  );

  Future<Map<String, dynamic>> updateLinearIssue(
    String issueId, {
    String? title,
    String? description,
    String? stateId,
    int? priority,
  }) => _rest.updateLinearIssue(
    issueId,
    title: title,
    description: description,
    stateId: stateId,
    priority: priority,
  );

  Future<Map<String, dynamic>> linkLinearIssueToTask(
    String issueId,
    String taskId,
  ) => _rest.linkLinearIssueToTask(issueId, taskId);

  Future<Map<String, dynamic>> unlinkLinearIssueFromTask(String issueId) =>
      _rest.unlinkLinearIssueFromTask(issueId);

  Future<Map<String, dynamic>> createTaskFromLinearIssue(String issueId) =>
      _rest.createTaskFromLinearIssue(issueId);

  // ---------------------------------------------------------------------------
  // Artifact CRUD
  // ---------------------------------------------------------------------------

  Future<Map<String, dynamic>> getArtifacts({
    String? search,
    int limit = 100,
    int offset = 0,
  }) => _rest.getArtifacts(search: search, limit: limit, offset: offset);

  Future<Map<String, dynamic>> getArtifact(String artifactId) =>
      _rest.getArtifact(artifactId);

  Future<String> getArtifactContent(String artifactId) =>
      _rest.getArtifactContent(artifactId);

  Future<void> deleteArtifact(String artifactId) =>
      _rest.deleteArtifact(artifactId);

  Future<void> recheckArtifacts() => _rest.recheckArtifacts();

  Future<Map<String, dynamic>> getArtifactSettings() =>
      _rest.getArtifactSettings();

  Future<Map<String, dynamic>> updateArtifactSettings({
    String? includePattern,
    String? excludePattern,
  }) => _rest.updateArtifactSettings(
    includePattern: includePattern,
    excludePattern: excludePattern,
  );

  /// Send a WebSocket message to request artifacts list
  void requestArtifacts() {
    _transport.sendOutput({'type': 'list_artifacts'});
  }

  // ---------------------------------------------------------------------------
  // Worktree API
  // ---------------------------------------------------------------------------

  Future<Map<String, dynamic>> listWorktrees(String repoPath) =>
      _rest.listWorktrees(repoPath);

  Future<Map<String, dynamic>> createWorktree({
    required String branch,
    required String repoPath,
    String base = 'main',
  }) => _rest.createWorktree(branch: branch, repoPath: repoPath, base: base);

  Future<Map<String, dynamic>> mergeWorktree({
    required String name,
    required String message,
    required String repoPath,
  }) => _rest.mergeWorktree(name: name, message: message, repoPath: repoPath);

  Future<Map<String, dynamic>> removeWorktree({
    required String name,
    required String repoPath,
  }) => _rest.removeWorktree(name: name, repoPath: repoPath);

  Future<Map<String, dynamic>> listProjectArtifacts(String projectName) =>
      _rest.listProjectArtifacts(projectName);

  Future<void> setSessionWorktree(String sessionId, String? path) =>
      _rest.setSessionWorktree(sessionId, path);

  Future<void> setSessionModel(String sessionId, String? model) =>
      _rest.setSessionModel(sessionId, model);

  void disconnect() => _transport.disconnect();

  void dispose() => _transport.dispose();
}
