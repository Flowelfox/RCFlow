import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';

import '../models/badge_spec.dart';
import '../models/legacy_badge_adapter.dart';
import '../models/session_info.dart';
import '../models/worker_config.dart';
import '../models/ws_message_type.dart';
import 'settings_service.dart';
import 'terminal_service.dart';
import 'websocket_service.dart';

enum WorkerConnectionStatus {
  disconnected,
  connecting,
  connected,
  reconnecting,
}

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

  /// Whether the server's active model supports any file attachments.
  /// Text/code files are always supported, so this is always true after
  /// the server info is fetched. Kept for backwards compatibility.
  bool supportsAttachments = true;

  /// Whether the server's active model supports image attachments
  /// (JPEG, PNG, GIF, WEBP). Defaults to true until server info is fetched.
  bool supportsImageAttachments = true;

  /// Whether this worker has a Linear API key configured.
  bool hasLinear = false;

  /// Token limits from server config (0 = unlimited).
  int inputTokenLimit = 0;
  int outputTokenLimit = 0;

  List<SessionInfo> sessions = [];
  final Set<String> subscribedSessions = {};

  /// Whether the server has more sessions beyond those currently loaded.
  bool hasMoreSessions = false;

  static const int _sessionPageSize = 30;

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

  // Hibernation state — set while the app is backgrounded on mobile. When
  // hibernating we drop the WebSocket to stop battery drain but keep the
  // cached session list visible and remember which sessions were subscribed
  // so wake() can re-subscribe (the backend replays buffer history on
  // subscribe, so live output resumes without any extra endpoint).
  bool _hibernating = false;
  bool get isHibernating => _hibernating;
  Set<String> _subscribedBeforeHibernate = {};

  // Callbacks for message routing (set by AppState)
  void Function(Map<String, dynamic> msg, String workerId)? onInputMessage;
  void Function(Map<String, dynamic> msg, String workerId)? onOutputMessage;
  VoidCallback? onSessionsChanged;

  /// Fires when a session's mainProjectPath transitions from null to a real path,
  /// or changes to a different path. Used to sync the project chip in PaneState.
  void Function(String sessionId, String projectPath)? onProjectPathAttached;

  /// Fires when the backend rejects a project_name with a validation error.
  void Function(String sessionId, String error)? onProjectNameError;

  /// Fires when a previously errored project is accepted (error cleared).
  void Function(String sessionId)? onProjectNameErrorCleared;

  /// Fires with the authoritative ``queued_messages`` snapshot from each
  /// ``session_update`` broadcast.  Consumers use this to rehydrate the
  /// pinned queue on reconnect.  See ``Queued User Messages`` in ``Design.md``.
  void Function(String sessionId, List<Map<String, dynamic>> snapshot)?
      onQueuedMessagesSnapshot;

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
      // During hibernation we intentionally keep [sessions] and
      // [subscribedSessions] populated so the UI shows cached state and
      // wake() can resubscribe. For every other disconnect path we clear
      // transient state so a reconnect starts from a clean slate.
      if (!_hibernating) {
        subscribedSessions.clear();
        sessions = [];
      }
      _inputSub?.cancel();
      _outputSub?.cancel();
      _inputSub = null;
      _outputSub = null;
      if (!_manualDisconnect) {
        _scheduleReconnect();
      }
      onSessionsChanged?.call();
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

      await ws.connect(
        config.hostWithPort,
        config.apiKey,
        secure: config.useSSL,
        allowSelfSigned: config.allowSelfSigned,
      );

      _status = WorkerConnectionStatus.connected;
      notifyListeners();

      ws.listSessions(offset: 0, limit: _sessionPageSize);
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
    _hibernating = false;
    _subscribedBeforeHibernate = {};
    _cancelReconnect();
    subscribedSessions.clear();
    sessions = [];
    serverOs = null;
    supportsAttachments = true;
    supportsImageAttachments = true;
    _terminalService?.disconnect();
    ws.disconnect();
    _inputSub?.cancel();
    _outputSub?.cancel();
    _inputSub = null;
    _outputSub = null;
    _status = WorkerConnectionStatus.disconnected;
    onSessionsChanged?.call();
    notifyListeners();
  }

  /// Release the WebSocket (and terminal WebSocket) while the app is
  /// backgrounded so the device can sleep. The cached session list, server
  /// info, and subscription set are preserved for restoration in [wake].
  /// No-op if the worker is not currently connected or connecting — a
  /// manually-disconnected worker stays disconnected.
  void hibernate() {
    if (!isConnected && !isConnecting) return;
    _hibernating = true;
    _subscribedBeforeHibernate = Set.of(subscribedSessions);
    _cancelReconnect();
    // Suppress the automatic reconnect that [_onConnectionStatus] would
    // otherwise trigger when we close the socket below.
    _manualDisconnect = true;
    _terminalService?.disconnect();
    ws.disconnect();
    notifyListeners();
  }

  /// Restore the connection that was torn down by [hibernate]. Reconnects
  /// and re-subscribes to every session that was subscribed at hibernate
  /// time — the backend replays buffered output on re-subscribe so the
  /// active pane catches up automatically.
  Future<void> wake() async {
    if (!_hibernating) return;
    _hibernating = false;
    final toRestore = _subscribedBeforeHibernate;
    _subscribedBeforeHibernate = {};
    // Clear the cached subscription set — [subscribe] below will repopulate
    // it. This keeps the local set in sync with what we actually re-send.
    subscribedSessions.clear();
    try {
      // connect() sets _manualDisconnect = false itself.
      await connect();
    } catch (_) {
      // Lifecycle-driven wake should not propagate connect failures. The
      // worker is now in disconnected state and will be reflected in the UI.
      return;
    }
    if (isConnected) {
      for (final sessionId in toRestore) {
        subscribe(sessionId);
      }
    }
  }

  void refreshSessions() {
    if (!isConnected) return;
    // Request at least as many sessions as currently loaded so that
    // "load more" pages are preserved across refreshes.
    final limit = sessions.length > _sessionPageSize
        ? sessions.length
        : _sessionPageSize;
    ws.listSessions(offset: 0, limit: limit);
  }

  /// Load the next page of sessions. No-op if already at the end or disconnected.
  void loadMoreSessions() {
    if (!isConnected || !hasMoreSessions) return;
    ws.listSessions(offset: sessions.length, limit: _sessionPageSize);
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
    final wsType = WsOutputType.tryParse(msg['type'] as String?);

    switch (wsType) {
      // Worker-level messages: consumed here, never forwarded upstream.
      case WsOutputType.sessionList:
        final list = msg['sessions'] as List<dynamic>?;
        if (list != null) {
          final offset = (msg['offset'] as num?)?.toInt() ?? 0;
          final hasMore = msg['has_more'] as bool? ?? false;
          _updateSessionList(list, offset: offset, hasMore: hasMore);
        }
        return;
      case WsOutputType.sessionUpdate:
        _handleSessionUpdate(msg);
        return;
      case WsOutputType.sessionReorder:
        _handleSessionReorder(msg);
        return;
      // Everything else (including task/artifact/app-level) forwarded upstream.
      default:
        onOutputMessage?.call(msg, config.id);
    }
  }

  void _updateSessionList(
    List<dynamic> list, {
    int offset = 0,
    bool hasMore = false,
  }) {
    // Build a lookup of existing sessions so we can preserve worktreeInfo when
    // the server omits or sends null for it (e.g. for archived sessions whose
    // metadata hasn't been re-hydrated yet).
    final existingById = {for (final s in sessions) s.sessionId: s};

    final incoming = list.map((s) {
      final json = s as Map<String, dynamic>;
      var parsed = SessionInfo.fromJson(json, workerId: config.id);
      final existing = existingById[parsed.sessionId];
      // If the server didn't include worktree context, fall back to whatever
      // we already have in memory for that session.
      if (parsed.worktreeInfo == null && existing?.worktreeInfo != null) {
        parsed = parsed.copyWith(worktreeInfo: existing!.worktreeInfo);
      }
      // Preserve the existing badge list when the server list response omits
      // badges (list responses don't always include the full badge set).
      // Badges are overwritten by individual session_update messages that
      // always carry the authoritative badge list.
      if (parsed.badges.isEmpty && existing != null && existing.badges.isNotEmpty) {
        parsed = parsed.copyWith(badges: existing.badges);
      }
      // Server worker badge carries backend_id as label. Replace with the
      // user-configured worker name — same replacement that session_update
      // applies. Archived sessions never receive a session_update, so this
      // is the only place where their worker badge label gets the friendly name.
      if (config.name.isNotEmpty && parsed.badges.isNotEmpty) {
        final replaced = parsed.badges.map((b) {
          if (b.type == 'worker') {
            return BadgeSpec(
              type: b.type,
              label: config.name,
              priority: b.priority,
              visible: b.visible,
              interactive: b.interactive,
              payload: b.payload,
            );
          }
          return b;
        }).toList();
        parsed = parsed.copyWith(badges: replaced);
      }
      return parsed;
    }).toList();

    if (offset == 0) {
      // Full refresh — replace the list.
      sessions = incoming;
    } else {
      // Subsequent page — append, avoiding duplicates from real-time updates.
      final existingIds = {for (final s in sessions) s.sessionId};
      sessions = [
        ...sessions,
        ...incoming.where((s) => !existingIds.contains(s.sessionId)),
      ];
    }
    hasMoreSessions = hasMore;
    _sortSessions();
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
    final cacheReadInputTokens = (msg['cache_read_input_tokens'] as num?)
        ?.toInt();
    final toolInputTokens = (msg['tool_input_tokens'] as num?)?.toInt();
    final toolOutputTokens = (msg['tool_output_tokens'] as num?)?.toInt();
    final toolCostUsd = (msg['tool_cost_usd'] as num?)?.toDouble();

    // Parse worktree context
    final wtJson = msg['worktree'] as Map<String, dynamic>?;
    final worktreeProvided = msg.containsKey('worktree');
    final worktreeInfo = wtJson != null ? WorktreeInfo.fromJson(wtJson) : null;

    // Parse project and worktree selection (always present in server broadcasts,
    // but use containsKey sentinels so a missing field never clears existing data).
    final mainProjectPathProvided = msg.containsKey('main_project_path');
    final newMainProjectPath = msg['main_project_path'] as String?;
    final selectedWorktreePathProvided = msg.containsKey(
      'selected_worktree_path',
    );
    final newSelectedWorktreePath = msg['selected_worktree_path'] as String?;

    // Project name error — non-null when backend rejected the last project_name.
    final projectNameErrorProvided = msg.containsKey('project_name_error');
    final newProjectNameError = msg['project_name_error'] as String?;

    // Parse agent type (always present in server broadcasts; use containsKey
    // sentinel so a missing field never clears existing data).
    final agentTypeProvided = msg.containsKey('agent_type');
    final newAgentType = msg['agent_type'] as String?;

    // Parse sort order
    final sortOrderProvided = msg.containsKey('sort_order');
    final newSortOrder = (msg['sort_order'] as num?)?.toInt();

    // Parse badges — use server-provided list when present; fall back to
    // LegacyBadgeAdapter for servers that pre-date the badge system (<0.39.0).
    final badgesProvided = msg.containsKey('badges');
    var badges = badgesProvided
        ? BadgeSpec.listFromJson(msg['badges'] as List<dynamic>?)
        : LegacyBadgeAdapter.adapt(msg, workerLabel: config.name);
    // Server worker badge carries backend_id as label (internal identifier).
    // Replace it with the user-configured worker name so the UI shows the
    // friendly name the user gave this connection.
    if (badgesProvided && config.name.isNotEmpty) {
      badges = badges.map((b) {
        if (b.type == 'worker') {
          return BadgeSpec(
            type: b.type,
            label: config.name,
            priority: b.priority,
            visible: b.visible,
            interactive: b.interactive,
            payload: b.payload,
          );
        }
        return b;
      }).toList();
    }

    final index = sessions.indexWhere((s) => s.sessionId == sessionId);
    if (index >= 0) {
      final existing = sessions[index];
      final prevMainProjectPath = existing.mainProjectPath;
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
        selectedWorktreePath: selectedWorktreePathProvided
            ? newSelectedWorktreePath
            : existing.selectedWorktreePath,
        mainProjectPath: mainProjectPathProvided
            ? newMainProjectPath
            : existing.mainProjectPath,
        agentType: agentTypeProvided ? newAgentType : existing.agentType,
        sortOrder: sortOrderProvided ? newSortOrder : existing.sortOrder,
        badges: badges,
      );
      // Fire callback when project path is attached or changes.
      if (newMainProjectPath != null &&
          newMainProjectPath != prevMainProjectPath) {
        onProjectPathAttached?.call(sessionId, newMainProjectPath);
      }
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
          selectedWorktreePath: newSelectedWorktreePath,
          mainProjectPath: newMainProjectPath,
          agentType: newAgentType,
          sortOrder: sortOrderProvided ? newSortOrder : null,
          badges: badges,
        ),
      );
      // Fire callback for a brand-new session that already has a project.
      if (newMainProjectPath != null) {
        onProjectPathAttached?.call(sessionId, newMainProjectPath);
      }
    }

    // Handle project name error/clear from the server.
    if (projectNameErrorProvided) {
      if (newProjectNameError != null) {
        onProjectNameError?.call(sessionId, newProjectNameError);
      } else {
        onProjectNameErrorCleared?.call(sessionId);
      }
    }
    _cacheSessions();
    onSessionsChanged?.call();

    // Forward the authoritative queued_messages snapshot to consumers so panes
    // reconcile their pinned queue on every session_update (reconnect-safe).
    if (msg.containsKey('queued_messages')) {
      final rawList = msg['queued_messages'];
      final snapshot = <Map<String, dynamic>>[];
      if (rawList is List) {
        for (final entry in rawList) {
          if (entry is Map) {
            snapshot.add(entry.cast<String, dynamic>());
          }
        }
      }
      onQueuedMessagesSnapshot?.call(sessionId, snapshot);
    }
  }

  void _cacheSessions() {
    try {
      _settings.setCachedSessions(
        config.id,
        jsonEncode(sessions.map((s) => s.toJson()).toList()),
      );
    } catch (_) {}
  }

  /// Sort sessions by sort_order ascending (nulls last), then createdAt desc.
  void _sortSessions() {
    const maxOrder = 1 << 62;
    sessions.sort((a, b) {
      final aOrder = a.sortOrder ?? maxOrder;
      final bOrder = b.sortOrder ?? maxOrder;
      final cmp = aOrder.compareTo(bOrder);
      if (cmp != 0) return cmp;
      final aTime = a.createdAt ?? DateTime(2000);
      final bTime = b.createdAt ?? DateTime(2000);
      return bTime.compareTo(aTime);
    });
  }

  /// Handle a lightweight session_reorder event from the server.
  void _handleSessionReorder(Map<String, dynamic> msg) {
    final order = msg['order'] as List<dynamic>?;
    if (order == null) return;
    final idOrder = <String, int>{};
    for (var i = 0; i < order.length; i++) {
      idOrder[order[i] as String] = i * 1000;
    }
    for (var i = 0; i < sessions.length; i++) {
      final newOrder = idOrder[sessions[i].sessionId];
      if (newOrder != null && sessions[i].sortOrder != newOrder) {
        sessions[i] = sessions[i].copyWith(sortOrder: newOrder);
      }
    }
    _sortSessions();
    _cacheSessions();
    onSessionsChanged?.call();
  }

  /// Reorder a session by moving it after another session (or to the top).
  Future<void> reorderSession(
    String sessionId, {
    String? afterSessionId,
  }) async {
    await ws.reorderSession(sessionId, afterSessionId: afterSessionId);
  }

  void loadCachedSessions() {
    final raw = _settings.getCachedSessions(config.id);
    if (raw == null) return;
    try {
      final list = jsonDecode(raw) as List<dynamic>;
      sessions = list
          .map(
            (s) => SessionInfo.fromJson(
              s as Map<String, dynamic>,
              workerId: config.id,
            ),
          )
          .toList();
    } catch (_) {}
  }

  void _fetchServerInfo() {
    ws
        .fetchServerInfo()
        .then((info) {
          serverOs = info['os'] as String?;
          supportsAttachments = (info['supports_attachments'] as bool?) ?? true;
          final caps = info['attachment_capabilities'] as Map<String, dynamic>?;
          supportsImageAttachments = (caps?['images'] as bool?) ?? true;
          notifyListeners();
        })
        .catchError((_) {});
  }

  /// Fetch time-series telemetry buckets from the backend.
  Future<Map<String, dynamic>> fetchTimeSeries({
    required String zoom,
    required DateTime start,
    required DateTime end,
    String? sessionId,
  }) async {
    return ws.fetchTimeSeries(
      zoom: zoom,
      start: start,
      end: end,
      sessionId: sessionId,
    );
  }

  /// Fetch worker-level telemetry summary from the backend.
  Future<Map<String, dynamic>> fetchWorkerTelemetry() async {
    return ws.fetchWorkerTelemetry();
  }

  /// Fetch per-session telemetry summary from the backend.
  Future<Map<String, dynamic>?> fetchSessionTelemetry(String sessionId) async {
    return ws.fetchSessionTelemetry(sessionId);
  }

  void _fetchTokenLimits() {
    ws
        .fetchConfig()
        .then((configOptions) {
          for (final opt in configOptions) {
            final key = opt['key'] as String?;
            final value = opt['value'];
            if (key == 'SESSION_INPUT_TOKEN_LIMIT') {
              inputTokenLimit = (value is num)
                  ? value.toInt()
                  : int.tryParse('$value') ?? 0;
            } else if (key == 'SESSION_OUTPUT_TOKEN_LIMIT') {
              outputTokenLimit = (value is num)
                  ? value.toInt()
                  : int.tryParse('$value') ?? 0;
            } else if (key == 'LINEAR_API_KEY') {
              hasLinear = value is String && value.isNotEmpty;
            }
          }
          notifyListeners();
        })
        .catchError((_) {});
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

        await ws.connect(
          config.hostWithPort,
          config.apiKey,
          secure: config.useSSL,
          allowSelfSigned: config.allowSelfSigned,
        );
        _status = WorkerConnectionStatus.connected;
        _retryCount = 0;
        notifyListeners();

        ws.listSessions(offset: 0, limit: _sessionPageSize);
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
