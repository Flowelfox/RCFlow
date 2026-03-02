import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';

import '../models/session_info.dart';
import '../models/worker_config.dart';
import 'settings_service.dart';
import 'websocket_service.dart';

enum WorkerConnectionStatus { disconnected, connecting, connected, reconnecting }

class WorkerConnection extends ChangeNotifier {
  WorkerConfig config;
  final WebSocketService ws;
  final SettingsService _settings;

  WorkerConnectionStatus _status = WorkerConnectionStatus.disconnected;
  WorkerConnectionStatus get status => _status;
  bool get isConnected => _status == WorkerConnectionStatus.connected;
  bool get isConnecting =>
      _status == WorkerConnectionStatus.connecting ||
      _status == WorkerConnectionStatus.reconnecting;

  /// Operating system reported by the server (e.g. "Windows", "Linux").
  String? serverOs;

  List<SessionInfo> sessions = [];
  final Set<String> subscribedSessions = {};

  // Reconnection state
  static const _maxRetries = 3;
  static const _retryDelay = Duration(seconds: 10);
  int _retryCount = 0;
  Timer? _retryTimer;
  bool _manualDisconnect = false;
  int get reconnectAttempt => _retryCount;

  // Callbacks for message routing (set by AppState)
  void Function(Map<String, dynamic> msg, String workerId)? onInputMessage;
  void Function(Map<String, dynamic> msg, String workerId)? onOutputMessage;
  VoidCallback? onSessionsChanged;

  // Pending auto-load session after connect
  String? _pendingAutoLoadSessionId;
  String? get pendingAutoLoadSessionId => _pendingAutoLoadSessionId;
  void clearPendingAutoLoad() => _pendingAutoLoadSessionId = null;

  StreamSubscription<Map<String, dynamic>>? _inputSub;
  StreamSubscription<Map<String, dynamic>>? _outputSub;
  StreamSubscription<bool>? _connectionSub;

  WorkerConnection({
    required this.config,
    required this.ws,
    required SettingsService settings,
  }) : _settings = settings {
    _connectionSub = ws.connectionStatus.listen(_onConnectionStatus);
  }

  void _onConnectionStatus(bool connected) {
    final wasConnected = _status == WorkerConnectionStatus.connected;
    if (wasConnected && !connected) {
      _status = WorkerConnectionStatus.disconnected;
      subscribedSessions.clear();
      _inputSub?.cancel();
      _outputSub?.cancel();
      _inputSub = null;
      _outputSub = null;
      if (!_manualDisconnect) {
        _scheduleReconnect();
      }
      notifyListeners();
    }
  }

  Future<void> connect() async {
    _cancelReconnect();
    _manualDisconnect = false;

    if (config.apiKey.isEmpty) return;

    _status = WorkerConnectionStatus.connecting;
    notifyListeners();

    try {
      _inputSub?.cancel();
      _outputSub?.cancel();
      _inputSub = ws.inputMessages.listen(_handleInputMessage);
      _outputSub = ws.outputMessages.listen(_handleOutputMessage);

      await ws.connect(config.host, config.apiKey,
          secure: config.useSSL,
          allowSelfSigned: config.allowSelfSigned);

      _status = WorkerConnectionStatus.connected;
      notifyListeners();

      ws.listSessions();
      _fetchServerInfo();

      final lastId = _settings.getLastSessionId(config.id);
      if (lastId != null) {
        _pendingAutoLoadSessionId = lastId;
      }
    } on TimeoutException {
      _status = WorkerConnectionStatus.disconnected;
      notifyListeners();
      rethrow;
    } catch (e) {
      _status = WorkerConnectionStatus.disconnected;
      notifyListeners();
      rethrow;
    }
  }

  void disconnect() {
    _manualDisconnect = true;
    _cancelReconnect();
    subscribedSessions.clear();
    serverOs = null;
    ws.disconnect();
    _inputSub?.cancel();
    _outputSub?.cancel();
    _inputSub = null;
    _outputSub = null;
    _status = WorkerConnectionStatus.disconnected;
    notifyListeners();
  }

  void refreshSessions() {
    if (!isConnected) return;
    ws.listSessions();
  }

  void subscribe(String sessionId) {
    if (!isConnected) return;
    ws.subscribe(sessionId);
    subscribedSessions.add(sessionId);
  }

  void unsubscribe(String sessionId) {
    if (!isConnected) return;
    ws.unsubscribe(sessionId);
    subscribedSessions.remove(sessionId);
  }

  void _handleInputMessage(Map<String, dynamic> msg) {
    onInputMessage?.call(msg, config.id);
  }

  void _handleOutputMessage(Map<String, dynamic> msg) {
    final type = msg['type'] as String?;

    // session_list: update our own sessions
    if (type == 'session_list') {
      final list = msg['sessions'] as List<dynamic>?;
      if (list != null) {
        _updateSessionList(list);
      }
      return;
    }

    // session_update: patch our own sessions
    if (type == 'session_update') {
      _handleSessionUpdate(msg);
      return;
    }

    // Forward everything else to AppState
    onOutputMessage?.call(msg, config.id);
  }

  void _updateSessionList(List<dynamic> list) {
    sessions = list
        .map((s) => SessionInfo.fromJson(s as Map<String, dynamic>,
            workerId: config.id))
        .toList();
    sessions.sort((a, b) {
      final aTime = a.createdAt ?? DateTime(2000);
      final bTime = b.createdAt ?? DateTime(2000);
      return bTime.compareTo(aTime);
    });
    _cacheSessions();
    onSessionsChanged?.call();
  }

  void _handleSessionUpdate(Map<String, dynamic> msg) {
    final sessionId = msg['session_id'] as String?;
    if (sessionId == null) return;

    final status = msg['status'] as String?;
    final activityState = msg['activity_state'] as String?;
    final sessionType = msg['session_type'] as String?;
    final createdAtStr = msg['created_at'] as String?;
    final titleProvided = msg.containsKey('title');
    final title = msg['title'] as String?;

    final index = sessions.indexWhere((s) => s.sessionId == sessionId);
    if (index >= 0) {
      final existing = sessions[index];
      sessions[index] = SessionInfo(
        sessionId: sessionId,
        sessionType: sessionType ?? existing.sessionType,
        status: status ?? existing.status,
        activityState: activityState ?? existing.activityState,
        createdAt: createdAtStr != null
            ? DateTime.tryParse(createdAtStr) ?? existing.createdAt
            : existing.createdAt,
        title: titleProvided ? title : existing.title,
        workerId: config.id,
      );
    } else {
      sessions.insert(
        0,
        SessionInfo(
          sessionId: sessionId,
          sessionType: sessionType ?? 'conversational',
          status: status ?? 'created',
          activityState: activityState,
          createdAt: createdAtStr != null
              ? DateTime.tryParse(createdAtStr)
              : DateTime.now(),
          title: title,
          workerId: config.id,
        ),
      );
    }
    _cacheSessions();
    onSessionsChanged?.call();
  }

  void _cacheSessions() {
    try {
      _settings.setCachedSessions(
          config.id, jsonEncode(sessions.map((s) => s.toJson()).toList()));
    } catch (_) {}
  }

  void loadCachedSessions() {
    final raw = _settings.getCachedSessions(config.id);
    if (raw == null) return;
    try {
      final list = jsonDecode(raw) as List<dynamic>;
      sessions = list
          .map((s) => SessionInfo.fromJson(s as Map<String, dynamic>,
              workerId: config.id))
          .toList();
    } catch (_) {}
  }

  void _fetchServerInfo() {
    ws.fetchServerInfo().then((info) {
      serverOs = info['os'] as String?;
      notifyListeners();
    }).catchError((_) {});
  }

  // --- Reconnection ---

  void _scheduleReconnect() {
    _retryCount = 0;
    _status = WorkerConnectionStatus.reconnecting;
    notifyListeners();
    _tryReconnect();
  }

  void _tryReconnect() {
    if (_manualDisconnect || isConnected) {
      if (_status == WorkerConnectionStatus.reconnecting) {
        _status = WorkerConnectionStatus.disconnected;
        notifyListeners();
      }
      return;
    }
    if (_retryCount >= _maxRetries) {
      _status = WorkerConnectionStatus.disconnected;
      notifyListeners();
      return;
    }

    _retryCount++;
    notifyListeners();

    _retryTimer = Timer(_retryDelay, () async {
      if (_manualDisconnect || isConnected) {
        _status = WorkerConnectionStatus.disconnected;
        notifyListeners();
        return;
      }

      try {
        _inputSub?.cancel();
        _outputSub?.cancel();
        _inputSub = ws.inputMessages.listen(_handleInputMessage);
        _outputSub = ws.outputMessages.listen(_handleOutputMessage);

        await ws.connect(config.host, config.apiKey,
          secure: config.useSSL,
          allowSelfSigned: config.allowSelfSigned);
        _status = WorkerConnectionStatus.connected;
        _retryCount = 0;
        notifyListeners();

        ws.listSessions();
        _fetchServerInfo();

        final lastId = _settings.getLastSessionId(config.id);
        if (lastId != null) {
          _pendingAutoLoadSessionId = lastId;
        }
      } catch (_) {
        _tryReconnect();
      }
    });
  }

  void _cancelReconnect() {
    _retryTimer?.cancel();
    _retryTimer = null;
    _retryCount = 0;
    if (_status == WorkerConnectionStatus.reconnecting) {
      _status = WorkerConnectionStatus.disconnected;
    }
  }

  @override
  void dispose() {
    _retryTimer?.cancel();
    _inputSub?.cancel();
    _outputSub?.cancel();
    _connectionSub?.cancel();
    ws.dispose();
    super.dispose();
  }
}
