import 'dart:async';
import 'dart:convert';
import 'dart:io' as io;

import '../server_url.dart';

/// HTTP/REST surface for the worker connection, split out of
/// [WebSocketService].  Owns its own copy of the connection target;
/// [WebSocketService.connect] keeps it in sync via [configure].  WebSocketService
/// keeps thin virtual delegators so existing call sites and test-subclass
/// overrides keep working.
class RestClient {
  ServerUrl? _serverUrl;
  bool _allowSelfSigned = true;

  void configure(ServerUrl? serverUrl, {required bool allowSelfSigned}) {
    _serverUrl = serverUrl;
    _allowSelfSigned = allowSelfSigned;
  }

  io.HttpClient _createHttpClient({required bool allowSelfSigned}) {
    final client = io.HttpClient();
    if (allowSelfSigned) {
      client.badCertificateCallback = (cert, host, port) => true;
    }
    return client;
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
      final responseBody = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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

  Future<List<Map<String, dynamic>>> fetchRCFlowPlugins() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/rcflow-plugins');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response.transform(utf8.decoder).join();
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
      final responseBody = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200) {
        final detail = _extractDetail(body);
        throw Exception(detail);
      }
    } finally {
      client.close();
    }
  }

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
      final body = await response.transform(utf8.decoder).join();
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
      final responseBody = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200) {
        final detail = _extractDetail(body);
        throw Exception(detail);
      }
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

  Future<List<Map<String, dynamic>>> fetchConfig() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/config');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.getUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response.transform(utf8.decoder).join();
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

  /// Fetch the dynamic model catalog for a given provider/scope.
  ///
  /// Returns a payload mirroring ``GET /api/models``:
  /// ```
  /// {
  ///   "provider": str, "scope": str,
  ///   "options": [{"value": str, "label": str}, ...],
  ///   "allow_custom": bool,
  ///   "source": "live"|"cached"|"fallback",
  ///   "fetched_at": ISO8601 | null,
  ///   "ttl_seconds": int,
  ///   "error": str | null
  /// }
  /// ```
  Future<Map<String, dynamic>> fetchModels({
    required String provider,
    required String scope,
    bool refresh = false,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final params = <String, String>{
      'provider': provider,
      'scope': scope,
      if (refresh) 'refresh': 'true',
    };
    final url = _serverUrl!.http('/api/models', params);
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Fetches the per-agent auth-readiness preflight from the worker so the
  /// client can warn the user when they pick an agent chip whose CLI has no
  /// API key or login configured. Returns a map shaped like
  /// ``{"agents": {"claude_code": {"ready": bool, "issue": String?}, ...}}``.
  Future<Map<String, dynamic>> fetchCodingAgentAuthPreflight() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/auth/preflight');
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

  Future<Map<String, dynamic>> triggerToolUpdate() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/tools/update');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
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
        final body = await response.transform(utf8.decoder).join();
        throw Exception('Server returned ${response.statusCode}: $body');
      }

      Map<String, dynamic>? lastComplete;
      // Read NDJSON lines from the streaming response
      await for (final line
          in response.transform(utf8.decoder).transform(const LineSplitter())) {
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
        final body = await response.transform(utf8.decoder).join();
        throw Exception('Server returned ${response.statusCode}: $body');
      }

      await for (final line
          in response.transform(utf8.decoder).transform(const LineSplitter())) {
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 201) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  // ---------------------------------------------------------------------------
  // GitHub integration
  // ---------------------------------------------------------------------------

  /// Trigger a server-side sync of open pull requests from GitHub into the
  /// cache. The backend fetches review-requested + authored PRs and broadcasts
  /// a `github_pr_update` for each.
  ///
  /// When [role] is given (`for_me` or `created`), only that subset is synced.
  /// Returns `{"synced": int}`.
  Future<Map<String, dynamic>> syncGithubPrs({
    String? role,
    String? state,
    bool force = false,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/github/sync', {
      'role': ?role,
      'state': ?state,
      if (force) 'force': 'true',
    });
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      final response = await request.close();
      final body = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200 && response.statusCode != 201) {
        throw Exception('Server returned ${response.statusCode}: $body');
      }
      return jsonDecode(body) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Fetch the GitHub integration status.
  ///
  /// Returns a map describing whether a token is configured and the last sync
  /// time, e.g. `{"configured": true, "synced_at": "..."}`.
  Future<Map<String, dynamic>> fetchGithubStatus() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/github/status');
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

  /// List the repos this worker is the default action target for.
  /// Returns `{"defaults": [{"owner","repo"}, ...]}`.
  Future<Map<String, dynamic>> getGithubRepoDefaults() async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/github/repo-defaults');
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

  /// Set or clear this worker's default flag for [owner]/[repo].
  Future<Map<String, dynamic>> setGithubRepoDefault(
    String owner,
    String repo,
    bool isDefault,
  ) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/github/repo-defaults');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.putUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(
        utf8.encode(
          jsonEncode({'owner': owner, 'repo': repo, 'is_default': isDefault}),
        ),
      );
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

  /// Validate an unsaved GitHub token's scopes (settings live preview). Returns
  /// the same shape as [fetchGithubStatus] (`valid`, `login`, `fine_grained`,
  /// `scopes`, optional `error`) for the supplied [token].
  Future<Map<String, dynamic>> checkGithubToken(String token) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/github/status/check');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'token': token})));
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

  /// Fetch the changed files (with unified-diff patches) for a cached PR.
  ///
  /// Returns `{"pr_id": "...", "files": [...], "total": N}`. Each file entry has
  /// `filename`, `previous_filename`, `status`, `additions`, `deletions`,
  /// `changes`, `patch` (nullable for binary/large files), `sha`, `blob_url`.
  Future<Map<String, dynamic>> getGithubPrFiles(String prId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/github/prs/$prId/files');
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

  /// Resolve a cached PR's repository to a local project folder on the worker.
  ///
  /// Returns `{"pr_id": "...", "project_name": String?, "project_path": String?}`.
  /// Both `project_name` and `project_path` are null when the PR's repo does not
  /// map to any known local project.
  Future<Map<String, dynamic>> getGithubPrProject(String prId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/github/prs/$prId/project');
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

  /// Fetch a PR's conversation: global (issue-level) comments merged with
  /// review summaries, as a timeline. Returns `{pr_id, items, total}` where each
  /// item is `{kind, author, author_avatar_url, body, created_at, url, state?}`.
  Future<Map<String, dynamic>> getGithubPrConversation(String prId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/integrations/github/prs/$prId/conversation',
    );
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

  /// Post a global (issue-level) comment on a PR's conversation. Returns
  /// `{pr_id, comment}` with the created comment.
  Future<Map<String, dynamic>> postGithubPrConversation(
    String prId,
    String body,
  ) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/integrations/github/prs/$prId/conversation',
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'body': body})));
      final response = await request.close();
      final respBody = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200) {
        throw Exception('Server returned ${response.statusCode}: $respBody');
      }
      return jsonDecode(respBody) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Check a cached PR's merge-conflict status against its base branch.
  ///
  /// Returns `{"conflicted": bool?, "files": List<String>?, "reason": "..."}`.
  /// `conflicted` is null while GitHub is still computing mergeability; `files`
  /// is the list of conflicting paths, an empty list when clean, or null when
  /// the list could not be computed locally (no local clone). `reason` is one of
  /// `clean`, `computing`, `conflicting`, `no_local_clone`.
  Future<Map<String, dynamic>> getGithubPrConflicts(String prId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/integrations/github/prs/$prId/conflicts',
    );
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

  /// Fetch a file's full content at the PR [side] ("head" or "base").
  ///
  /// Used by the diff viewer to expand context lines hidden between/around the
  /// patch hunks. Returns the raw backend payload, which includes a `content`
  /// string with the full file text at the requested ref.
  Future<Map<String, dynamic>> getGithubPrFile(
    String prId,
    String path, {
    String side = 'head',
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/github/prs/$prId/file', {
      'path': path,
      'side': side,
    });
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

  /// Fetch the inline review threads for a cached PR.
  ///
  /// Returns `{"pr_id": "...", "threads": [...], "total": N}`. Each thread has
  /// `thread_id` (GraphQL node id), `is_resolved`, `is_outdated`, `path`,
  /// `line`, `side` ("LEFT"|"RIGHT"), and `comments` (each with `id`,
  /// `database_id`, `author`, `body`, `created_at`).
  Future<Map<String, dynamic>> getGithubPrThreads(String prId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/github/prs/$prId/threads');
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

  /// Fetch the local (not-yet-submitted) review draft for a cached PR.
  ///
  /// Returns `{"pr_id": "...", "event": "COMMENT", "body": "...",
  /// "comments": [{"path", "line", "side", "body"}, ...]}`.
  Future<Map<String, dynamic>> getGithubPrDraft(String prId) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/github/prs/$prId/draft');
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

  /// Patch the review draft's verdict [event] and/or summary [body].
  ///
  /// Returns the updated draft dict.
  Future<Map<String, dynamic>> patchGithubPrDraft(
    String prId, {
    String? event,
    String? body,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/github/prs/$prId/draft');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.openUrl('PATCH', url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'event': ?event, 'body': ?body})));
      final response = await request.close();
      final responseBody = await response.transform(utf8.decoder).join();
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

  /// Queue an inline comment into the review draft for a cached PR.
  ///
  /// Returns the updated draft dict.
  Future<Map<String, dynamic>> addGithubPrDraftComment(
    String prId, {
    required String path,
    required int line,
    required String side,
    required String body,
    int? startLine,
    String? startSide,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/integrations/github/prs/$prId/draft/comments',
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(
        utf8.encode(
          jsonEncode({
            'path': path,
            'line': line,
            'side': side,
            'body': body,
            // Range anchor — only sent when this is a multi-line comment.
            'start_line': ?startLine,
            if (startLine != null) 'start_side': ?startSide,
          }),
        ),
      );
      final response = await request.close();
      final responseBody = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200 && response.statusCode != 201) {
        throw Exception(
          'Server returned ${response.statusCode}: $responseBody',
        );
      }
      return jsonDecode(responseBody) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Remove the queued draft comment at [index] for a cached PR.
  ///
  /// Returns the updated draft dict.
  Future<Map<String, dynamic>> deleteGithubPrDraftComment(
    String prId,
    int index,
  ) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/integrations/github/prs/$prId/draft/comments/$index',
    );
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

  /// Submit a review for a cached PR, posting any queued draft comments.
  ///
  /// [event] is one of "APPROVE", "REQUEST_CHANGES", "COMMENT". Returns
  /// `{"review": {"id", "state"}, "pr": {...full PR...}}`. The draft is
  /// cleared server-side on success.
  Future<Map<String, dynamic>> submitGithubPrReview(
    String prId, {
    required String event,
    String? body,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/github/prs/$prId/review');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'event': event, 'body': ?body})));
      final response = await request.close();
      final responseBody = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200 && response.statusCode != 201) {
        throw Exception(
          'Server returned ${response.statusCode}: $responseBody',
        );
      }
      return jsonDecode(responseBody) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Reply to a review thread comment (identified by its [commentId], the
  /// comment's GitHub `database_id`). Returns `{"id", "body"}`.
  Future<Map<String, dynamic>> replyGithubPrComment(
    String prId,
    int commentId,
    String body,
  ) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/integrations/github/prs/$prId/comments/$commentId/reply',
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(utf8.encode(jsonEncode({'body': body})));
      final response = await request.close();
      final responseBody = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200 && response.statusCode != 201) {
        throw Exception(
          'Server returned ${response.statusCode}: $responseBody',
        );
      }
      return jsonDecode(responseBody) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

  /// Delete a review-thread comment, identified by its REST [commentId] (the
  /// comment's `database_id`). Returns `{"deleted": true, "comment_id": N}`.
  ///
  /// The backend maps a GitHub 403 (not the comment's author) to HTTP 502.
  Future<Map<String, dynamic>> deleteGithubPrComment(
    String prId,
    int commentId,
  ) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/integrations/github/prs/$prId/comments/$commentId',
    );
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

  /// Resolve or unresolve a review thread (identified by its GraphQL
  /// [threadId]). Returns `{"thread_id", "resolved"}`.
  Future<Map<String, dynamic>> resolveGithubPrThread(
    String prId,
    String threadId,
    bool resolved,
  ) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http(
      '/api/integrations/github/prs/$prId/threads/$threadId/resolve',
      {'resolved': resolved.toString()},
    );
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
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

  /// Merge a cached PR using [method] ("squash"|"merge"|"rebase").
  ///
  /// Returns `{"merged": bool, "message": "...", "pr": {...}}`.
  Future<Map<String, dynamic>> mergeGithubPr(
    String prId, {
    required String method,
    String? commitTitle,
    String? commitMessage,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/github/prs/$prId/merge');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(
        utf8.encode(
          jsonEncode({
            'method': method,
            'commit_title': ?commitTitle,
            'commit_message': ?commitMessage,
          }),
        ),
      );
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

  /// Open a pull request from a local worktree. The server pushes the
  /// worktree's branch (optionally committing pending changes first) and
  /// opens a PR for it. Returns `{"pr": {...}, "url": "..."}`.
  Future<Map<String, dynamic>> openGithubPr({
    String? selectedWorktreePath,
    String? projectName,
    required String title,
    String body = '',
    String base = 'main',
    String? headBranch,
    String? commitMessage,
    bool draft = false,
  }) async {
    if (_serverUrl == null) throw StateError('Not connected');
    final url = _serverUrl!.http('/api/integrations/github/open-pr');
    final client = _createHttpClient(allowSelfSigned: _allowSelfSigned);
    try {
      final request = await client.postUrl(url);
      request.headers.set('X-API-Key', _serverUrl!.apiKey);
      request.headers.contentType = io.ContentType.json;
      request.add(
        utf8.encode(
          jsonEncode({
            'selected_worktree_path': ?selectedWorktreePath,
            'project_name': ?projectName,
            'title': title,
            'body': body,
            'base': base,
            'head_branch': ?headBranch,
            'commit_message': ?commitMessage,
            'draft': draft,
          }),
        ),
      );
      final response = await request.close();
      final responseBody = await response.transform(utf8.decoder).join();
      if (response.statusCode != 200 && response.statusCode != 201) {
        throw Exception(
          'Server returned ${response.statusCode}: $responseBody',
        );
      }
      return jsonDecode(responseBody) as Map<String, dynamic>;
    } finally {
      client.close();
    }
  }

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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
        final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final body = await response.transform(utf8.decoder).join();
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
      final responseBody = await response.transform(utf8.decoder).join();
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

  static String _escapeFilename(String name) =>
      name.replaceAll('"', '\\"').replaceAll('\n', '').replaceAll('\r', '');

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
}
