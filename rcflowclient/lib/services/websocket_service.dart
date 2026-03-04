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

  bool get isConnected =>
      _inputChannel != null && _outputChannel != null;

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
  Future<IOWebSocketChannel> _connectSocket(Uri url,
      {required bool secure, required bool allowSelfSigned}) async {
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
  Future<void> connect(String host, String apiKey,
      {bool secure = false, bool allowSelfSigned = true}) async {
    disconnect();

    final url = ServerUrl(rawHost: host, apiKey: apiKey, secure: secure);
    _serverUrl = url;
    _allowSelfSigned = allowSelfSigned;

    // Connect input channel
    try {
      _inputChannel = await _connectSocket(url.wsInputText(),
          secure: secure, allowSelfSigned: allowSelfSigned);
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
      _outputChannel = await _connectSocket(url.wsOutputText(),
          secure: secure, allowSelfSigned: allowSelfSigned);
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

  void sendPrompt(String text, String? sessionId) {
    if (_inputChannel == null) return;
    final msg = {
      'type': 'prompt',
      'text': text,
      'session_id': sessionId,
    };
    _inputChannel!.sink.add(jsonEncode(msg));
  }

  void subscribe(String sessionId) {
    if (_outputChannel == null) return;
    final msg = {
      'type': 'subscribe',
      'session_id': sessionId,
    };
    _outputChannel!.sink.add(jsonEncode(msg));
  }

  void unsubscribe(String sessionId) {
    if (_outputChannel == null) return;
    final msg = {
      'type': 'unsubscribe',
      'session_id': sessionId,
    };
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
      if (pathPrefix != null) 'path_prefix': pathPrefix,
    };
    _inputChannel!.sink.add(jsonEncode(msg));
  }

  void dismissSessionEndAsk(String sessionId) {
    if (_inputChannel == null) return;
    final msg = {
      'type': 'dismiss_session_end_ask',
      'session_id': sessionId,
    };
    _inputChannel!.sink.add(jsonEncode(msg));
  }

  void listSessions() {
    if (_outputChannel == null) return;
    _outputChannel!.sink.add(jsonEncode({'type': 'list_sessions'}));
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
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<void> endSession(String sessionId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/sessions/$sessionId/end');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      final response = await request.close();
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
      if (response.statusCode != 200) {
        throw Exception(response.statusCode == 404
            ? 'Session not found'
            : response.statusCode == 409
                ? 'Session already ended'
                : 'Server returned ${response.statusCode}: $body');
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
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
      if (response.statusCode != 200) {
        throw Exception(response.statusCode == 404
            ? 'Session not found'
            : response.statusCode == 409
                ? 'Session already ended'
                : 'Server returned ${response.statusCode}: $body');
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
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
      if (response.statusCode != 200) {
        throw Exception(response.statusCode == 404
            ? 'Session not found'
            : response.statusCode == 409
                ? 'Session cannot be paused'
                : 'Server returned ${response.statusCode}: $body');
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
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
      if (response.statusCode != 200) {
        throw Exception(response.statusCode == 404
            ? 'Session not found'
            : response.statusCode == 409
                ? 'Session is not paused'
                : 'Server returned ${response.statusCode}: $body');
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
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
      if (response.statusCode != 200) {
        throw Exception(response.statusCode == 404
            ? 'Session not found'
            : response.statusCode == 409
                ? 'Session cannot be restored'
                : 'Server returned ${response.statusCode}: $body');
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
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<List<String>> fetchProjects({String? query}) async {
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
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      final data = jsonDecode(body) as Map<String, dynamic>;
      final projects = data['projects'] as List<dynamic>;
      return projects.cast<String>();
    } finally {
      client.close();
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
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
      if (response.statusCode != 200) {
        throw Exception(response.statusCode == 404
            ? 'Session not found'
            : 'Server returned ${response.statusCode}: $body');
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
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
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
      Map<String, dynamic> updates) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/config');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.openUrl('PATCH', url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'updates': updates})));
      final response = await request.close();
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
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

  Future<Map<String, dynamic>> fetchToolStatus() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/status');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
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
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
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
        final body =
            await response.transform(const io.SystemEncoding().decoder).join();
        throw Exception('Server returned ${response.statusCode}: $body');
      }

      Map<String, dynamic>? lastComplete;
      // Read NDJSON lines from the streaming response
      await for (final line in response
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
        final body =
            await response.transform(const io.SystemEncoding().decoder).join();
        throw Exception('Server returned ${response.statusCode}: $body');
      }

      await for (final line in response
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
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
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
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> switchToolSource(
      String toolName, bool useManaged) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/$toolName/source');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.write(jsonEncode({'use_managed': useManaged}));
      final response = await request.close();
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
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
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  Future<Map<String, dynamic>> updateToolSettings(
      String toolName, Map<String, dynamic> updates) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/$toolName/settings');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.openUrl('PATCH', url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'updates': updates})));
      final response = await request.close();
      final body =
          await response.transform(const io.SystemEncoding().decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
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
