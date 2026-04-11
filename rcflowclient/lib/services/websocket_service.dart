import 'dart:async';
import 'dart:convert';
import 'dart:io' as io;

import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'server_url.dart';

class WebSocketService {
  static const _pingInterval = Duration(seconds: 5);

  ServerUrl? _serverUrl;
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

  /// Upload a file to the server and return the attachment metadata,
  /// including the ``attachment_id`` to include in a subsequent prompt.
  ///
  /// Throws if the upload fails or the server returns a non-2xx status.
  Future<Map<String, dynamic>> uploadAttachment({
    required List<int> bytes,
    required String fileName,
    required String mimeType,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/uploads');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      // Build a minimal multipart/form-data body manually.
      const boundary = '----RCFlowBoundary7MA4YWxkTrZu0gW';
      final header =
          '--$boundary\r\nContent-Disposition: form-data; name="file"; filename="${_escapeFilename(fileName)}"\r\nContent-Type: $mimeType\r\n\r\n';
      final footer = '\r\n--$boundary--\r\n';
      final headerBytes = utf8.encode(header);
      final footerBytes = utf8.encode(footer);
      final body = [...headerBytes, ...bytes, ...footerBytes];

      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.set(
        'Content-Type',
        'multipart/form-data; boundary=$boundary',
      );
      request.headers.contentLength = body.length;
      request.add(body);

      final response = await request.close();
      final responseBody = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw Exception(
          'Upload failed (${response.statusCode}): $responseBody',
        );
      }
      return jsonDecode(responseBody) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  static String _escapeFilename(String name) =>
      name.replaceAll('"', '\\"').replaceAll('\n', '').replaceAll('\r', '');

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

  void dismissSessionEndAsk(String sessionId) {
    if (_inputChannel == null) return;
    final msg = {'type': 'dismiss_session_end_ask', 'session_id': sessionId};
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

  void listSessions() {
    if (_outputChannel == null) return;
    _outputChannel!.sink.add(jsonEncode({'type': 'list_sessions'}));
  }

  void listTasks() {
    if (_outputChannel == null) return;
    _outputChannel!.sink.add(jsonEncode({'type': 'list_tasks'}));
  }

  Future<Map<String, dynamic>> fetchSessionMessages(
    String sessionId, {
    int? before,
    int? limit,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final queryParams = <String, String>{};
    if (before != null) queryParams['before'] = before.toString();
    if (limit != null) queryParams['limit'] = limit.toString();
    final url = _serverUrl!.http(
      '/api/sessions/$sessionId/messages',
      queryParams.isNotEmpty ? queryParams : null,
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Fetch the unsent message draft for [sessionId] from the backend.
  ///
  /// Returns `(content: '', updatedAt: DateTime.now())` on any error so the
  /// caller can always treat the result as a plain string without try/catch.
  Future<({String content, DateTime updatedAt})> getSessionDraft(
    String sessionId,
  ) async {
    if (_serverUrl == null) {
      return (content: '', updatedAt: DateTime.now());
    }
    final url = _serverUrl!.http('/api/sessions/$sessionId/draft');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        return (content: '', updatedAt: DateTime.now());
      }
      final map = jsonDecode(body) as Map<String, dynamic>;
      return (
        content: map['content'] as String? ?? '',
        updatedAt: DateTime.parse(map['updated_at'] as String),
      );
    } catch (_) {
      return (content: '', updatedAt: DateTime.now());
    } finally {
      client.close();
    }
  }

  /// Save [content] as the unsent message draft for [sessionId].
  ///
  /// Best-effort: errors are swallowed silently so a network blip never
  /// disrupts the UX. The local SharedPreferences cache is always written
  /// before this is called, so the draft is durable even if this fails.
  Future<void> saveSessionDraft(String sessionId, String content) async {
    if (_serverUrl == null) return;
    final url = _serverUrl!.http('/api/sessions/$sessionId/draft');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.putUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.write(jsonEncode({'content': content}));
      final response = await request.close();
      await response.drain<void>();
    } catch (_) {
      // best-effort; local cache already written
    } finally {
      client.close();
    }
  }

  /// Ends a session.  Returns `true` if the session was ended (or was already
  /// ended), `false` should never happen (throws on real errors).
  Future<void> endSession(String sessionId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/sessions/$sessionId/end');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      // Treat 409 (already ended) as success — the session IS ended on the
      // server, so the client should update its state accordingly.
      if (response.statusCode == 409) return;
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

  Future<void> cancelSession(String sessionId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/sessions/$sessionId/cancel');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception(
          response.statusCode == 404
              ? 'Session not found'
              : response.statusCode == 409
              ? 'Session already ended'
              : 'Server returned ${response.statusCode}: $body',
        );
      }
    } finally {
      client.close();
    }
  }

  Future<void> pauseSession(String sessionId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/sessions/$sessionId/pause');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception(
          response.statusCode == 404
              ? 'Session not found'
              : response.statusCode == 409
              ? 'Session cannot be paused'
              : 'Server returned ${response.statusCode}: $body',
        );
      }
    } finally {
      client.close();
    }
  }

  Future<void> resumeSession(String sessionId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/sessions/$sessionId/resume');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception(
          response.statusCode == 404
              ? 'Session not found'
              : response.statusCode == 409
              ? 'Session is not paused'
              : 'Server returned ${response.statusCode}: $body',
        );
      }
    } finally {
      client.close();
    }
  }

  Future<void> restoreSession(String sessionId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/sessions/$sessionId/restore');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception(
          response.statusCode == 404
              ? 'Session not found'
              : response.statusCode == 409
              ? 'Session cannot be restored'
              : 'Server returned ${response.statusCode}: $body',
        );
      }
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> fetchServerInfo() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/info');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Fetches project entries from the server.
  ///
  /// Returns a list of maps with ``name`` and ``path`` keys, e.g.
  /// `[{"name": "RCFlow", "path": "/home/user/Projects/RCFlow"}]`.
  Future<List<Map<String, String>>> fetchProjects({String? query}) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final queryParams = <String, String>{};
    if (query != null && query.isNotEmpty) queryParams['q'] = query;
    final url = _serverUrl!.http(
      '/api/projects',
      queryParams.isNotEmpty ? queryParams : null,
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      final data = jsonDecode(body) as Map<String, dynamic>;
      final projects = data['projects'] as List<dynamic>;
      return projects
          .cast<Map<String, dynamic>>()
          .map(
            (e) => {'name': e['name'] as String, 'path': e['path'] as String},
          )
          .toList();
    } finally {
      client.close();
    }
  }

  Future<List<Map<String, String>>> fetchTools({String? query}) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final queryParams = <String, String>{};
    if (query != null && query.isNotEmpty) queryParams['q'] = query;
    final url = _serverUrl!.http(
      '/api/tools',
      queryParams.isNotEmpty ? queryParams : null,
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      final data = jsonDecode(body) as Map<String, dynamic>;
      final tools = data['tools'] as List<dynamic>;
      return tools.map((t) {
        final m = t as Map<String, dynamic>;
        final mentionName =
            (m['mention_name'] as String?) ?? m['name'] as String;
        return {
          'name': m['name'] as String,
          'mention_name': mentionName,
          'display_name': (m['display_name'] as String?) ?? m['name'] as String,
          'description': m['description'] as String,
        };
      }).toList();
    } finally {
      client.close();
    }
  }

  Future<List<Map<String, String>>> fetchArtifactSuggestions({
    String? query,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final queryParams = <String, String>{};
    if (query != null && query.isNotEmpty) queryParams['q'] = query;
    final url = _serverUrl!.http(
      '/api/artifacts/search',
      queryParams.isNotEmpty ? queryParams : null,
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      final data = jsonDecode(body) as Map<String, dynamic>;
      final artifacts = data['artifacts'] as List<dynamic>;
      return artifacts.map((a) {
        final m = a as Map<String, dynamic>;
        return {
          'artifact_id': m['artifact_id'] as String,
          'file_name': m['file_name'] as String,
          'file_path': m['file_path'] as String,
          'file_extension': m['file_extension'] as String,
          'is_text': (m['is_text'] as bool? ?? false).toString(),
        };
      }).toList();
    } finally {
      client.close();
    }
  }

  Future<List<Map<String, String>>> fetchSlashCommands({String? query}) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final queryParams = <String, String>{};
    if (query != null && query.isNotEmpty) queryParams['q'] = query;
    final url = _serverUrl!.http(
      '/api/slash-commands',
      queryParams.isNotEmpty ? queryParams : null,
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      final data = jsonDecode(body) as Map<String, dynamic>;
      final commands = data['commands'] as List<dynamic>;
      return commands.map((c) {
        final m = c as Map<String, dynamic>;
        return {
          'name': m['name'] as String,
          'description': m['description'] as String,
          'source': m['source'] as String,
        };
      }).toList();
    } finally {
      client.close();
    }
  }

  // ---------------------------------------------------------------------------
  // RCFlow-managed plugin management
  // ---------------------------------------------------------------------------

  Future<List<Map<String, dynamic>>> fetchRCFlowPlugins() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/rcflow-plugins');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      final data = jsonDecode(body) as Map<String, dynamic>;
      return (data['plugins'] as List<dynamic>).cast<Map<String, dynamic>>();
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> installRCFlowPlugin(
    String source, {
    String? name,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/rcflow-plugins');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      final body = <String, dynamic>{'source': source};
      if (name != null) body['name'] = name;
      request.add(utf8.encode(jsonEncode(body)));
      final response = await request.close();
      final responseBody = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 201) {
        final detail = _extractDetail(responseBody);
        throw Exception(detail);
      }
      final data = jsonDecode(responseBody) as Map<String, dynamic>;
      return data['plugin'] as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<void> uninstallRCFlowPlugin(String name) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/rcflow-plugins/${Uri.encodeComponent(name)}',
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.deleteUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        final detail = _extractDetail(body);
        throw Exception(detail);
      }
    } finally {
      client.close();
    }
  }

  // ---------------------------------------------------------------------------
  // Tool-scoped plugin management (canonical v2 endpoints)
  // ---------------------------------------------------------------------------

  Future<List<Map<String, dynamic>>> fetchToolPlugins(String toolName) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/tools/${Uri.encodeComponent(toolName)}/plugins',
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        final detail = _extractDetail(body);
        throw Exception(detail);
      }
      final data = jsonDecode(body) as Map<String, dynamic>;
      return (data['plugins'] as List<dynamic>).cast<Map<String, dynamic>>();
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> installToolPlugin(
    String toolName,
    String source, {
    String? name,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/tools/${Uri.encodeComponent(toolName)}/plugins',
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      final bodyMap = <String, dynamic>{'source': source};
      if (name != null) bodyMap['name'] = name;
      request.add(utf8.encode(jsonEncode(bodyMap)));
      final response = await request.close();
      final responseBody = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 201) {
        final detail = _extractDetail(responseBody);
        throw Exception(detail);
      }
      final data = jsonDecode(responseBody) as Map<String, dynamic>;
      return data['plugin'] as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<void> uninstallToolPlugin(String toolName, String name) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/tools/${Uri.encodeComponent(toolName)}/plugins/${Uri.encodeComponent(name)}',
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.deleteUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        final detail = _extractDetail(body);
        throw Exception(detail);
      }
    } finally {
      client.close();
    }
  }

  Future<void> setToolPluginEnabled(
    String toolName,
    String name,
    bool enabled,
  ) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/tools/${Uri.encodeComponent(toolName)}/plugins/${Uri.encodeComponent(name)}',
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.openUrl('PATCH', url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'enabled': enabled})));
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        final detail = _extractDetail(body);
        throw Exception(detail);
      }
    } finally {
      client.close();
    }
  }

  /// Extracts the FastAPI ``detail`` field from a JSON error response body,
  /// falling back to the raw body if parsing fails.
  static String _extractDetail(String body) {
    try {
      final data = jsonDecode(body) as Map<String, dynamic>;
      return data['detail'] as String? ?? body;
    } catch (_) {
      return body;
    }
  }

  Future<void> renameSession(String sessionId, String? title) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/sessions/$sessionId/title');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.openUrl('PATCH', url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'title': title})));
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
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

  /// Reorder a session by placing it after another session (or at the top).
  Future<void> reorderSession(
    String sessionId, {
    String? afterSessionId,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/sessions/$sessionId/reorder');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.openUrl('PATCH', url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(
        utf8.encode(jsonEncode({'after_session_id': afterSessionId})),
      );
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
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

  Future<List<Map<String, dynamic>>> fetchConfig() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/config');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      final data = jsonDecode(body) as Map<String, dynamic>;
      final options = data['options'] as List<dynamic>;
      return options.cast<Map<String, dynamic>>();
    } finally {
      client.close();
    }
  }

  Future<List<Map<String, dynamic>>> updateConfig(
    Map<String, dynamic> updates,
  ) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/config');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.openUrl('PATCH', url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'updates': updates})));
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      final data = jsonDecode(body) as Map<String, dynamic>;
      final options = data['options'] as List<dynamic>;
      return options.cast<Map<String, dynamic>>();
    } finally {
      client.close();
    }
  }

  /// Fetch time-series telemetry buckets.
  Future<Map<String, dynamic>> fetchTimeSeries({
    required String zoom,
    required DateTime start,
    required DateTime end,
    String? sessionId,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final params = <String, String>{
      'zoom': zoom,
      'start': start.toUtc().toIso8601String(),
      'end': end.toUtc().toIso8601String(),
    };
    if (sessionId != null) params['session_id'] = sessionId;
    final url = _serverUrl!.http('/api/telemetry/timeseries', params);
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Fetch worker-level telemetry summary (aggregated across all sessions).
  Future<Map<String, dynamic>> fetchWorkerTelemetry() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/telemetry/worker/summary');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Fetch per-session telemetry summary. Returns null on 404.
  Future<Map<String, dynamic>?> fetchSessionTelemetry(String sessionId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/telemetry/sessions/$sessionId/summary');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      if (response.statusCode == 404) return null;
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> fetchToolStatus() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/status');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> triggerToolUpdate() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/update');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Update a single tool with NDJSON streaming progress.
  ///
  /// [onProgress] is called for each progress event.
  /// Returns the final tool data from the ``complete`` event.
  Future<Map<String, dynamic>> triggerSingleToolUpdate(
    String toolName, {
    void Function(Map<String, dynamic> event)? onProgress,
  }) async {
    return _streamToolOperation(
      '/api/tools/update/$toolName',
      onProgress: onProgress,
    );
  }

  /// Install the managed version of a tool with NDJSON streaming progress.
  Future<Map<String, dynamic>> installManagedTool(
    String toolName, {
    void Function(Map<String, dynamic> event)? onProgress,
  }) async {
    return _streamToolOperation(
      '/api/tools/$toolName/install',
      onProgress: onProgress,
    );
  }

  /// Shared helper: POST to [path], read NDJSON lines, call [onProgress],
  /// return the ``complete`` event's ``tool`` map wrapped as ``{"tool": ...}``.
  Future<Map<String, dynamic>> _streamToolOperation(
    String path, {
    void Function(Map<String, dynamic> event)? onProgress,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(path);
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      if (response.statusCode != 200) {
        final body = await response
            .transform(const io.SystemEncoding().decoder)
            .join();
        throw Exception('Server returned ${response.statusCode}: $body');
      }

      Map<String, dynamic>? lastComplete;
      // Read NDJSON lines from the streaming response
      await for (final line
          in response
              .transform(const io.SystemEncoding().decoder)
              .transform(const LineSplitter())) {
        if (line.trim().isEmpty) continue;
        final event = jsonDecode(line) as Map<String, dynamic>;
        final step = event['step'] as String?;
        if (step == 'error') {
          throw Exception(event['message'] ?? 'Unknown error');
        }
        if (step == 'complete') {
          lastComplete = event;
        }
        onProgress?.call(event);
      }

      if (lastComplete != null && lastComplete.containsKey('tool')) {
        return {'tool': lastComplete['tool']};
      }
      throw Exception('Stream ended without completion event');
    } finally {
      client.close();
    }
  }

  /// Start Codex ChatGPT login, streaming NDJSON progress.
  ///
  /// When [deviceCode] is true, uses device-code auth (shows a code to enter
  /// in the browser). Otherwise uses browser-based OAuth (returns a URL to open).
  /// [onProgress] is called for each event. Returns on completion or error.
  Future<void> codexLogin({
    bool deviceCode = false,
    void Function(Map<String, dynamic> event)? onProgress,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final queryParams = <String, String>{};
    if (deviceCode) queryParams['device_code'] = 'true';
    final url = _serverUrl!.http(
      '/api/tools/codex/login',
      queryParams.isNotEmpty ? queryParams : null,
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      if (response.statusCode != 200) {
        final body = await response
            .transform(const io.SystemEncoding().decoder)
            .join();
        throw Exception('Server returned ${response.statusCode}: $body');
      }

      await for (final line
          in response
              .transform(const io.SystemEncoding().decoder)
              .transform(const LineSplitter())) {
        if (line.trim().isEmpty) continue;
        final event = jsonDecode(line) as Map<String, dynamic>;
        final step = event['step'] as String?;
        if (step == 'error') {
          throw Exception(event['message'] ?? 'Unknown error');
        }
        onProgress?.call(event);
        if (step == 'complete') break;
      }
    } finally {
      client.close();
    }
  }

  /// Check Codex ChatGPT login status.
  ///
  /// Returns `{"logged_in": bool, "method": String?}`.
  Future<Map<String, dynamic>> codexLoginStatus() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/codex/login/status');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Start Claude Code Anthropic login (step 1).
  ///
  /// Returns `{"auth_url": "https://claude.ai/oauth/..."}`.
  /// After the user authenticates in the browser and gets a code,
  /// call [claudeCodeLoginCode] with the code to complete login.
  Future<Map<String, dynamic>> claudeCodeLogin() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/claude_code/login');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Submit OAuth code to complete Claude Code login (step 2).
  ///
  /// Returns `{"logged_in": bool, "email": String?, "subscription": String?}`.
  Future<Map<String, dynamic>> claudeCodeLoginCode(String code) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/claude_code/login/code');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'code': code})));
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Check Claude Code Anthropic login status.
  ///
  /// Returns `{"logged_in": bool, "method": String?, "email": String?, "subscription": String?}`.
  Future<Map<String, dynamic>> claudeCodeLoginStatus() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/claude_code/login/status');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Log out of Claude Code Anthropic account.
  Future<void> claudeCodeLogout() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/claude_code/logout');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> uninstallManagedTool(String toolName) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/$toolName/install');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.deleteUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> switchToolSource(
    String toolName,
    bool useManaged,
  ) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/$toolName/source');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.write(jsonEncode({'use_managed': useManaged}));
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> fetchToolSettings(String toolName) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/$toolName/settings');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> updateToolSettings(
    String toolName,
    Map<String, dynamic> updates,
  ) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/$toolName/settings');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.openUrl('PATCH', url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'updates': updates})));
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  // ---------------------------------------------------------------------------
  // Task CRUD
  // ---------------------------------------------------------------------------

  Future<Map<String, dynamic>> createTask({
    required String title,
    String? description,
    String source = 'user',
    String? sessionId,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tasks');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(
        utf8.encode(
          jsonEncode({
            'title': title,
            'description': ?description,
            'source': source,
            'session_id': ?sessionId,
          }),
        ),
      );
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 201) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> updateTask(
    String taskId, {
    String? title,
    String? description,
    String? status,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tasks/$taskId');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.openUrl('PATCH', url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(
        utf8.encode(
          jsonEncode({
            'title': ?title,
            'description': ?description,
            'status': ?status,
          }),
        ),
      );
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<void> deleteTask(String taskId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tasks/$taskId');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.deleteUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> attachSessionToTask(
    String taskId,
    String sessionId,
  ) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tasks/$taskId/sessions');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'session_id': sessionId})));
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 201) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> detachSessionFromTask(
    String taskId,
    String sessionId,
  ) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tasks/$taskId/sessions/$sessionId');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.deleteUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  // ---------------------------------------------------------------------------
  // Linear integration
  // ---------------------------------------------------------------------------

  /// Test a Linear API key and return accessible teams.
  ///
  /// This does not require an existing key to be configured — it is used
  /// during initial Linear setup to validate the key and discover teams.
  ///
  /// Returns `{"ok": true, "teams": [{"id": "...", "name": "..."}]}`.
  Future<Map<String, dynamic>> testLinearConnection(String apiKey) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/linear/test');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'api_key': apiKey})));
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Fetch teams accessible via the configured Linear API key.
  ///
  /// Requires `LINEAR_API_KEY` to be set in the backend configuration.
  /// Returns `{"teams": [{"id": "...", "name": "..."}]}`.
  Future<Map<String, dynamic>> fetchLinearTeams() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/linear/teams');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  void listLinearIssues() {
    if (_outputChannel == null) return;
    _outputChannel!.sink.add(jsonEncode({'type': 'list_linear_issues'}));
  }

  Future<Map<String, dynamic>> syncLinearIssues() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/linear/sync');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode('{}'));
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> createLinearIssue({
    required String title,
    String? description,
    int priority = 0,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/linear/issues');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(
        utf8.encode(
          jsonEncode({
            'title': title,
            'description': ?description,
            'priority': priority,
          }),
        ),
      );
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 201) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> updateLinearIssue(
    String issueId, {
    String? title,
    String? description,
    String? stateId,
    int? priority,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/linear/issues/$issueId');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.openUrl('PATCH', url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(
        utf8.encode(
          jsonEncode({
            'title': ?title,
            'description': ?description,
            'state_id': ?stateId,
            'priority': ?priority,
          }),
        ),
      );
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> linkLinearIssueToTask(
    String issueId,
    String taskId,
  ) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/integrations/linear/issues/$issueId/link',
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'task_id': taskId})));
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> unlinkLinearIssueFromTask(String issueId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/integrations/linear/issues/$issueId/link',
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.deleteUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Create a new RCFlow task from a cached Linear issue and link them.
  ///
  /// Returns `{"task": {...}, "issue": {...}}` on success (HTTP 201).
  /// Throws if the issue is already linked (HTTP 409) or not found (HTTP 404).
  Future<Map<String, dynamic>> createTaskFromLinearIssue(String issueId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/integrations/linear/issues/$issueId/create-task',
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode('{}'));
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 201) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  // ---------------------------------------------------------------------------
  // Artifact CRUD
  // ---------------------------------------------------------------------------

  Future<Map<String, dynamic>> getArtifacts({
    String? search,
    int limit = 100,
    int offset = 0,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final queryParams = <String, String>{};
    if (search != null && search.isNotEmpty) {
      queryParams['search'] = search;
    }
    queryParams['limit'] = limit.toString();
    queryParams['offset'] = offset.toString();

    final url = _serverUrl!
        .http('/api/artifacts')
        .replace(queryParameters: queryParams);
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> getArtifact(String artifactId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/artifacts/$artifactId');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<String> getArtifactContent(String artifactId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/artifacts/$artifactId/content');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      if (response.statusCode != 200) {
        final body = await response
            .transform(const io.SystemEncoding().decoder)
            .join();
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      // Return raw content
      return await response.transform(utf8.decoder).join();
    } finally {
      client.close();
    }
  }

  Future<void> deleteArtifact(String artifactId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/artifacts/$artifactId');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.deleteUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
    } finally {
      client.close();
    }
  }

  Future<void> recheckArtifacts() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/artifacts/recheck');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentLength = 0;
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> getArtifactSettings() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/artifacts/settings');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> updateArtifactSettings({
    String? includePattern,
    String? excludePattern,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/artifacts/settings');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.openUrl('PUT', url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      final body = <String, dynamic>{};
      if (includePattern != null) body['include_pattern'] = includePattern;
      if (excludePattern != null) body['exclude_pattern'] = excludePattern;
      request.write(jsonEncode(body));
      final response = await request.close();
      final responseBody = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
      if (response.statusCode != 200) {
        throw Exception(
          'Server returned ${response.statusCode}: $responseBody',
        );
      }
      return jsonDecode(responseBody) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

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
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
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
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
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
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
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
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
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
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
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
      final body = await response
          .transform(const io.SystemEncoding().decoder)
          .join();
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
