import 'dart:async';
import 'dart:convert';
import 'dart:io' as io;

import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'server_url.dart';
import 'rest/rest_client.dart';

class WebSocketService {
  static const _pingInterval = Duration(seconds: 5);

  ServerUrl? _serverUrl;
  final RestClient _rest = RestClient();
  bool _allowSelfSigned = true;
  WebSocketChannel? _inputChannel;
  WebSocketChannel? _outputChannel;

  final _inputController = StreamController<Map<String, dynamic>>.broadcast();
  final _outputController = StreamController<Map<String, dynamic>>.broadcast();
  final _connectionController = StreamController<bool>.broadcast();

  Stream<Map<String, dynamic>> get inputMessages => _inputController.stream;
  Stream<Map<String, dynamic>> get outputMessages => _outputController.stream;
  Stream<bool> get connectionStatus => _connectionController.stream;

  bool get isConnected => _inputChannel != null && _outputChannel != null;

  StreamSubscription<dynamic>? _inputSub;
  StreamSubscription<dynamic>? _outputSub;

  /// Create an [io.HttpClient] that optionally trusts self-signed certificates.
  io.HttpClient _createHttpClient({required bool allowSelfSigned}) {
    final client = io.HttpClient();
    if (allowSelfSigned) {
      client.badCertificateCallback = (cert, host, port) => true;
    }
    return client;
  }

  /// Open a single [io.WebSocket] with ping keepalive and wrap it in an
  /// [IOWebSocketChannel].
  Future<IOWebSocketChannel> _connectSocket(
    Uri url, {
    required bool secure,
    required bool allowSelfSigned,
  }) async {
    io.HttpClient? client;
    if (secure) {
      client = _createHttpClient(allowSelfSigned: allowSelfSigned);
    }
    final socket = await io.WebSocket.connect(
      url.toString(),
      customClient: client,
    ).timeout(const Duration(seconds: 10));
    socket.pingInterval = _pingInterval;
    return IOWebSocketChannel(socket);
  }

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
    disconnect();

    final url = ServerUrl(rawHost: host, apiKey: apiKey, secure: secure);
    _serverUrl = url;
    _allowSelfSigned = allowSelfSigned;
    _rest.configure(url, allowSelfSigned: allowSelfSigned);

    // Connect input channel
    try {
      _inputChannel = await _connectSocket(
        url.wsInputText(),
        secure: secure,
        allowSelfSigned: allowSelfSigned,
      );
    } catch (e) {
      _inputChannel = null;
      _connectionController.add(false);
      rethrow;
    }

    _inputSub = _inputChannel!.stream.listen(
      (data) {
        try {
          final msg = jsonDecode(data as String) as Map<String, dynamic>;
          _inputController.add(msg);
        } catch (_) {}
      },
      onError: (error) {
        _connectionController.add(false);
      },
      onDone: () {
        _connectionController.add(false);
      },
    );

    // Connect output channel after input succeeds
    try {
      _outputChannel = await _connectSocket(
        url.wsOutputText(),
        secure: secure,
        allowSelfSigned: allowSelfSigned,
      );
    } catch (e) {
      _inputChannel?.sink.close();
      _inputChannel = null;
      _outputChannel = null;
      _connectionController.add(false);
      rethrow;
    }

    _outputSub = _outputChannel!.stream.listen(
      (data) {
        try {
          final msg = jsonDecode(data as String) as Map<String, dynamic>;
          _outputController.add(msg);
        } catch (_) {}
      },
      onError: (error) {
        _connectionController.add(false);
      },
      onDone: () {
        _connectionController.add(false);
      },
    );

    _connectionController.add(true);
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
    if (_inputChannel == null) return;
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
    _inputChannel!.sink.add(jsonEncode(msg));
  }

  /// Send a start_plan_session message over the input WebSocket.
  /// The server responds with an ack containing the session_id, which is handled
  /// by the standard ack routing in AppState._handleInputMessage.
  void startPlanSession(
    String taskId, {
    String? projectName,
    String? selectedWorktreePath,
  }) {
    if (_inputChannel == null) return;
    final msg = <String, dynamic>{
      'type': 'start_plan_session',
      'task_id': taskId,
      'project_name': ?projectName,
      'selected_worktree_path': ?selectedWorktreePath,
    };
    _inputChannel!.sink.add(jsonEncode(msg));
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
    if (_outputChannel == null) return;
    final msg = {'type': 'subscribe', 'session_id': sessionId};
    _outputChannel!.sink.add(jsonEncode(msg));
  }

  void unsubscribe(String sessionId) {
    if (_outputChannel == null) return;
    final msg = {'type': 'unsubscribe', 'session_id': sessionId};
    _outputChannel!.sink.add(jsonEncode(msg));
  }

  void answerQuestion(String? sessionId, Map<String, String> answers) {
    if (_inputChannel == null) return;
    final msg = {
      'type': 'question_answer',
      'session_id': sessionId,
      'answers': answers,
    };
    _inputChannel!.sink.add(jsonEncode(msg));
  }

  void sendPermissionResponse({
    required String sessionId,
    required String requestId,
    required String decision,
    required String scope,
    String? pathPrefix,
  }) {
    if (_inputChannel == null) return;
    final msg = <String, dynamic>{
      'type': 'permission_response',
      'session_id': sessionId,
      'request_id': requestId,
      'decision': decision,
      'scope': scope,
      'path_prefix': ?pathPrefix,
    };
    _inputChannel!.sink.add(jsonEncode(msg));
  }

  void sendInteractiveResponse(
    String sessionId,
    String text, {
    bool accepted = true,
  }) {
    if (_inputChannel == null) return;
    final msg = {
      'type': 'interactive_response',
      'session_id': sessionId,
      'text': text,
      'accepted': accepted,
    };
    _inputChannel!.sink.add(jsonEncode(msg));
  }

  /// Send an interrupt_subprocess message over the input WebSocket.
  /// Kills any running Claude Code / Codex subprocess without pausing the
  /// session. The session remains ACTIVE and ready for new prompts.
  void interruptSubprocess(String sessionId) {
    if (_inputChannel == null) return;
    final msg = {'type': 'interrupt_subprocess', 'session_id': sessionId};
    _inputChannel!.sink.add(jsonEncode(msg));
  }

  /// Stop a live Claude Code Monitor watch identified by its tool_use id.
  /// The server responds with an ``ack`` and emits ``monitor_end`` with
  /// ``reason="cancelled"`` for the matching block.
  void cancelMonitor(String sessionId, String monitorId) {
    if (_inputChannel == null) return;
    final msg = {
      'type': 'cancel_monitor',
      'session_id': sessionId,
      'monitor_id': monitorId,
    };
    _inputChannel!.sink.add(jsonEncode(msg));
  }

  /// Request cancellation of a queued user message that has not yet been
  /// delivered to the agent.  The server may respond with a ``cancel_ack``
  /// carrying ``ok: false`` when the message was already dequeued; the UI
  /// handles that gracefully.
  void cancelQueued(String sessionId, String queuedId) {
    if (_inputChannel == null) return;
    final msg = {
      'type': 'cancel_queued',
      'session_id': sessionId,
      'queued_id': queuedId,
    };
    _inputChannel!.sink.add(jsonEncode(msg));
  }

  /// Edit the text of a queued user message in place (text only; attachments
  /// are fixed at enqueue time).  Server responds with ``edit_ack``.
  void editQueued(
    String sessionId,
    String queuedId,
    String content, {
    String? displayContent,
  }) {
    if (_inputChannel == null) return;
    final msg = <String, dynamic>{
      'type': 'edit_queued',
      'session_id': sessionId,
      'queued_id': queuedId,
      'content': content,
    };
    if (displayContent != null) {
      msg['display_content'] = displayContent;
    }
    _inputChannel!.sink.add(jsonEncode(msg));
  }

  void listSessions({int offset = 0, int limit = 30}) {
    if (_outputChannel == null) return;
    _outputChannel!.sink.add(
      jsonEncode({'type': 'list_sessions', 'offset': offset, 'limit': limit}),
    );
  }

  void listTasks() {
    if (_outputChannel == null) return;
    _outputChannel!.sink.add(jsonEncode({'type': 'list_tasks'}));
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
    if (_outputChannel == null) return;
    _outputChannel!.sink.add(jsonEncode({'type': 'list_linear_issues'}));
  }

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
    _outputChannel!.sink.add(jsonEncode({'type': 'list_artifacts'}));
  }

  // ---------------------------------------------------------------------------
  // Worktree API
  // ---------------------------------------------------------------------------

  /// List all active worktrees for [repoPath].
  Future<Map<String, dynamic>> listWorktrees(String repoPath) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/worktrees', {'repo_path': repoPath});
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Create a new worktree with [branch] branched from [base] (default "main").
  Future<Map<String, dynamic>> createWorktree({
    required String branch,
    required String repoPath,
    String base = 'main',
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/worktrees');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.write(
        jsonEncode({'branch': branch, 'base': base, 'repo_path': repoPath}),
      );
      final response = await request.close();
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 201) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Squash-merge [name] into its base branch with [message] and clean up.
  Future<Map<String, dynamic>> mergeWorktree({
    required String name,
    required String message,
    required String repoPath,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/worktrees/$name/merge');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.write(jsonEncode({'message': message, 'repo_path': repoPath}));
      final response = await request.close();
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Remove a worktree and its branch without merging.
  Future<Map<String, dynamic>> removeWorktree({
    required String name,
    required String repoPath,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/worktrees/$name', {
      'repo_path': repoPath,
    });
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.deleteUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// List artifacts that belong to a project directory.
  ///
  /// [projectName] is the directory name as it appears under PROJECTS_DIR.
  Future<Map<String, dynamic>> listProjectArtifacts(String projectName) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/projects/$projectName/artifacts');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Set or clear the selected worktree path for a session.
  ///
  /// [path] is the absolute path of the worktree to select, or null to clear.
  /// When set, Claude Code and Codex agents will use this directory.
  Future<void> setSessionWorktree(String sessionId, String? path) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/sessions/$sessionId/worktree');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.openUrl('PATCH', url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'path': path})));
      final response = await request.close();
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200) {
        throw Exception(
          response.statusCode == 404
              ? 'Session not found'
              : 'Server returned ${response.statusCode}: $body',
        );
      }
    } finally {
      client.close();
    }
  }

  void disconnect() {
    _inputSub?.cancel();
    _outputSub?.cancel();
    _inputSub = null;
    _outputSub = null;
    _inputChannel?.sink.close();
    _outputChannel?.sink.close();
    _inputChannel = null;
    _outputChannel = null;
    _connectionController.add(false);
  }

  void dispose() {
    disconnect();
    _inputController.close();
    _outputController.close();
    _connectionController.close();
  }
}
