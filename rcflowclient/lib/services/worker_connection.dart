import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';

import '../models/session_info.dart';
import '../models/worker_config.dart';
import 'settings_service.dart';
import 'terminal_service.dart';
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

  /// Whether the server's active model supports image/file attachments.
  /// Defaults to true (optimistic) until server info is fetched.
  bool supportsAttachments = true;

  /// Token limits from server config (0 = unlimited).
  int inputTokenLimit = 0;
  int outputTokenLimit = 0;

  List<SessionInfo> sessions = [];
  final Set<String> subscribedSessions = {};

  /// Lazily-created terminal WebSocket service for this worker.
  TerminalService? _terminalService;
  TerminalService get terminalService {
    _terminalService ??= TerminalService();
    return _terminalService!;
  }

  // Reconnection state
  static const maxRetries = 3;
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

      await ws.connect(config.hostWithPort, config.apiKey,
          secure: config.useSSL,
          allowSelfSigned: config.allowSelfSigned);

      _status = WorkerConnectionStatus.connected;
      notifyListeners();

      ws.listSessions();
      ws.listTasks();
      ws.listLinearIssues();
      ws.requestArtifacts();
      _fetchServerInfo();
      _fetchTokenLimits();

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

  /// Ensure the terminal WebSocket is connected (lazy connect).
  Future<void> ensureTerminalConnected() async {
    if (terminalService.isConnected) return;
    await terminalService.connect(
      config.hostWithPort,
      config.apiKey,
      secure: config.useSSL,
      allowSelfSigned: config.allowSelfSigned,
    );
  }

  void disconnect() {
    _manualDisconnect = true;
    _cancelReconnect();
    subscribedSessions.clear();
    serverOs = null;
    supportsAttachments = true;
    _terminalService?.disconnect();
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

    // task_list / task_update / task_deleted: forward to AppState with worker info
    if (type == 'task_list' || type == 'task_update' || type == 'task_deleted') {
      onOutputMessage?.call(msg, config.id);
      return;
    }

    // Forward everything else to AppState
    onOutputMessage?.call(msg, config.id);
  }

  void _updateSessionList(List<dynamic> list) {
    // Build a lookup of existing sessions so we can preserve worktreeInfo when
    // the server omits or sends null for it (e.g. for archived sessions whose
    // metadata hasn't been re-hydrated yet).
    final existingById = {for (final s in sessions) s.sessionId: s};

    sessions = list.map((s) {
      final json = s as Map<String, dynamic>;
      final parsed = SessionInfo.fromJson(json, workerId: config.id);
      // If the server didn't include worktree context, fall back to whatever
      // we already have in memory for that session.
      if (parsed.worktreeInfo == null) {
        final existing = existingById[parsed.sessionId];
        if (existing?.worktreeInfo != null) {
          return parsed.copyWith(worktreeInfo: existing!.worktreeInfo);
        }
      }
      return parsed;
    }).toList();
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

    // Parse token usage fields
    final inputTokens = (msg['input_tokens'] as num?)?.toInt();
    final outputTokens = (msg['output_tokens'] as num?)?.toInt();
    final cacheCreationInputTokens =
        (msg['cache_creation_input_tokens'] as num?)?.toInt();
    final cacheReadInputTokens =
        (msg['cache_read_input_tokens'] as num?)?.toInt();
    final toolInputTokens = (msg['tool_input_tokens'] as num?)?.toInt();
    final toolOutputTokens = (msg['tool_output_tokens'] as num?)?.toInt();
    final toolCostUsd = (msg['tool_cost_usd'] as num?)?.toDouble();

    // Parse worktree context
    final wtJson = msg['worktree'] as Map<String, dynamic>?;
    final worktreeProvided = msg.containsKey('worktree');
    final worktreeInfo =
        wtJson != null ? WorktreeInfo.fromJson(wtJson) : null;

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
        inputTokens: inputTokens ?? existing.inputTokens,
        outputTokens: outputTokens ?? existing.outputTokens,
        cacheCreationInputTokens:
            cacheCreationInputTokens ?? existing.cacheCreationInputTokens,
        cacheReadInputTokens:
            cacheReadInputTokens ?? existing.cacheReadInputTokens,
        toolInputTokens: toolInputTokens ?? existing.toolInputTokens,
        toolOutputTokens: toolOutputTokens ?? existing.toolOutputTokens,
        toolCostUsd: toolCostUsd ?? existing.toolCostUsd,
        worktreeInfo: worktreeProvided ? worktreeInfo : existing.worktreeInfo,
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
          inputTokens: inputTokens ?? 0,
          outputTokens: outputTokens ?? 0,
          cacheCreationInputTokens: cacheCreationInputTokens ?? 0,
          cacheReadInputTokens: cacheReadInputTokens ?? 0,
          toolInputTokens: toolInputTokens ?? 0,
          toolOutputTokens: toolOutputTokens ?? 0,
          toolCostUsd: toolCostUsd ?? 0.0,
          worktreeInfo: worktreeInfo,
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
      supportsAttachments = (info['supports_attachments'] as bool?) ?? true;
      notifyListeners();
    }).catchError((_) {});
  }

  void _fetchTokenLimits() {
    ws.fetchConfig().then((configOptions) {
      for (final opt in configOptions) {
        final key = opt['key'] as String?;
        final value = opt['value'];
        if (key == 'SESSION_INPUT_TOKEN_LIMIT') {
          inputTokenLimit = (value is num) ? value.toInt() : int.tryParse('$value') ?? 0;
        } else if (key == 'SESSION_OUTPUT_TOKEN_LIMIT') {
          outputTokenLimit = (value is num) ? value.toInt() : int.tryParse('$value') ?? 0;
        }
      }
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
    if (_retryCount >= maxRetries) {
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

        await ws.connect(config.hostWithPort, config.apiKey,
          secure: config.useSSL,
          allowSelfSigned: config.allowSelfSigned);
        _status = WorkerConnectionStatus.connected;
        _retryCount = 0;
        notifyListeners();

        ws.listSessions();
        ws.listTasks();
        ws.listLinearIssues();
        _fetchServerInfo();
        _fetchTokenLimits();

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
