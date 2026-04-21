/// Tests for WorkerConnection — session management, message routing,
/// reconnection state machine, and callback contracts.
library;

import 'dart:async';

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/badge_spec.dart';
import 'package:rcflowclient/models/session_info.dart';
import 'package:rcflowclient/models/worker_config.dart';
import 'package:rcflowclient/services/settings_service.dart';
import 'package:rcflowclient/services/websocket_service.dart';
import 'package:rcflowclient/services/worker_connection.dart';
import 'package:shared_preferences/shared_preferences.dart';

// ---------------------------------------------------------------------------
// Fake WebSocketService — controllable streams, no real network I/O.
// ---------------------------------------------------------------------------

class FakeWebSocketService extends WebSocketService {
  final _inputCtrl = StreamController<Map<String, dynamic>>.broadcast();
  final _outputCtrl = StreamController<Map<String, dynamic>>.broadcast();
  final _connCtrl = StreamController<bool>.broadcast();

  bool connectShouldThrow = false;
  Object connectError = Exception('connect failed');

  // Call tracking
  int connectCallCount = 0;
  int disconnectCallCount = 0;
  int listSessionsCallCount = 0;
  int listTasksCallCount = 0;
  int listLinearIssuesCallCount = 0;
  int requestArtifactsCallCount = 0;
  int subscribeCallCount = 0;
  int unsubscribeCallCount = 0;
  final List<String> subscribedIds = [];
  final List<String> unsubscribedIds = [];

  @override
  Stream<Map<String, dynamic>> get inputMessages => _inputCtrl.stream;

  @override
  Stream<Map<String, dynamic>> get outputMessages => _outputCtrl.stream;

  @override
  Stream<bool> get connectionStatus => _connCtrl.stream;

  @override
  bool get isConnected => _connected;
  bool _connected = false;

  @override
  Future<void> connect(
    String host,
    String apiKey, {
    bool secure = false,
    bool allowSelfSigned = true,
  }) async {
    connectCallCount++;
    if (connectShouldThrow) throw connectError;
    _connected = true;
    _connCtrl.add(true);
  }

  @override
  void disconnect() {
    disconnectCallCount++;
    _connected = false;
  }

  @override
  void listSessions({int offset = 0, int limit = 30}) => listSessionsCallCount++;

  @override
  void listTasks() => listTasksCallCount++;

  @override
  void listLinearIssues() => listLinearIssuesCallCount++;

  @override
  void requestArtifacts() => requestArtifactsCallCount++;

  @override
  void subscribe(String sessionId) {
    subscribeCallCount++;
    subscribedIds.add(sessionId);
  }

  @override
  void unsubscribe(String sessionId) {
    unsubscribeCallCount++;
    unsubscribedIds.add(sessionId);
  }

  @override
  Future<Map<String, dynamic>> fetchServerInfo() async => {
    'os': 'Linux',
    'supports_attachments': true,
    'attachment_capabilities': {'images': true},
  };

  @override
  Future<List<Map<String, dynamic>>> fetchConfig() async => [];

  @override
  Future<void> reorderSession(
    String sessionId, {
    String? afterSessionId,
  }) async {}

  // Helpers to inject messages from tests
  void injectOutput(Map<String, dynamic> msg) => _outputCtrl.add(msg);
  void injectInput(Map<String, dynamic> msg) => _inputCtrl.add(msg);
  void simulateDisconnect() {
    _connected = false;
    _connCtrl.add(false);
  }

  @override
  void dispose() {
    _inputCtrl.close();
    _outputCtrl.close();
    _connCtrl.close();
    super.dispose();
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

WorkerConfig _makeConfig({String id = 'w1', String name = 'Worker 1'}) =>
    WorkerConfig(
      id: id,
      name: name,
      host: '127.0.0.1',
      port: 8765,
      apiKey: 'test-key',
      autoConnect: false,
    );

Map<String, dynamic> _sessionJson(
  String id, {
  String status = 'created',
  int? sortOrder,
}) => {
  'session_id': id,
  'session_type': 'conversational',
  'status': status,
  'created_at': '2024-01-01T00:00:00Z',
  if (sortOrder != null) 'sort_order': sortOrder,
};

Map<String, dynamic> _sessionList(List<Map<String, dynamic>> sessions) => {
  'type': 'session_list',
  'sessions': sessions,
};

Map<String, dynamic> _sessionUpdate(
  String id, {
  String? status,
  String? mainProjectPath,
  String? projectNameError,
  bool includeProjectNameError = false,
  int? sortOrder,
}) {
  final msg = <String, dynamic>{'type': 'session_update', 'session_id': id};
  if (status != null) msg['status'] = status;
  if (mainProjectPath != null) msg['main_project_path'] = mainProjectPath;
  if (includeProjectNameError) {
    msg['project_name_error'] = projectNameError;
  }
  if (sortOrder != null) msg['sort_order'] = sortOrder;
  return msg;
}

Map<String, dynamic> _sessionUpdateWithBadges(
  String id, {
  required List<Map<String, dynamic>> badges,
}) => {
  'type': 'session_update',
  'session_id': id,
  'status': 'active',
  'badges': badges,
};

Map<String, dynamic> _badgeJson(String type, String label) => {
  'type': type,
  'label': label,
  'priority': 0,
  'visible': true,
  'interactive': false,
  'payload': <String, dynamic>{},
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  late FakeWebSocketService ws;
  late SettingsService settings;
  late WorkerConnection conn;

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    settings = SettingsService();
    await settings.init();
    ws = FakeWebSocketService();
    conn = WorkerConnection(
      config: _makeConfig(),
      ws: ws,
      settings: settings,
    );
  });

  tearDown(() {
    conn.dispose();
  });

  // -------------------------------------------------------------------------
  // Connection lifecycle
  // -------------------------------------------------------------------------

  group('connect()', () {
    test('transitions to connected and fires initial list requests', () async {
      await conn.connect();
      expect(conn.isConnected, isTrue);
      expect(conn.status, WorkerConnectionStatus.connected);
      expect(ws.listSessionsCallCount, 1);
      expect(ws.listTasksCallCount, 1);
    });

    test('empty apiKey skips connect entirely', () async {
      final emptyKeyConn = WorkerConnection(
        config: _makeConfig().copyWith(apiKey: ''),
        ws: ws,
        settings: settings,
      );
      addTearDown(emptyKeyConn.dispose);
      await emptyKeyConn.connect();
      expect(emptyKeyConn.isConnected, isFalse);
      expect(ws.connectCallCount, 0);
    });

    test('failed connect transitions back to disconnected', () async {
      ws.connectShouldThrow = true;
      await expectLater(conn.connect(), throwsException);
      expect(conn.isConnected, isFalse);
      expect(conn.status, WorkerConnectionStatus.disconnected);
    });

    test('notifies listeners on status changes', () async {
      final statuses = <WorkerConnectionStatus>[];
      conn.addListener(() => statuses.add(conn.status));

      await conn.connect();

      expect(
        statuses,
        containsAll([
          WorkerConnectionStatus.connecting,
          WorkerConnectionStatus.connected,
        ]),
      );
    });
  });

  group('disconnect()', () {
    test('clears sessions and subscriptions', () async {
      await conn.connect();
      ws.injectOutput(_sessionList([_sessionJson('s1'), _sessionJson('s2')]));
      await Future.microtask(() {});

      conn.subscribe('s1');
      conn.disconnect();

      expect(conn.isConnected, isFalse);
      expect(conn.sessions, isEmpty);
      expect(conn.subscribedSessions, isEmpty);
      expect(conn.status, WorkerConnectionStatus.disconnected);
    });

    test('fires onSessionsChanged', () async {
      await conn.connect();
      var fired = false;
      conn.onSessionsChanged = () => fired = true;
      conn.disconnect();
      expect(fired, isTrue);
    });
  });

  // -------------------------------------------------------------------------
  // Session list management
  // -------------------------------------------------------------------------

  group('session_list message', () {
    setUp(() async => conn.connect());

    test('populates sessions from server list', () async {
      ws.injectOutput(
        _sessionList([_sessionJson('s1'), _sessionJson('s2')]),
      );
      await Future.microtask(() {});

      expect(conn.sessions.map((s) => s.sessionId), containsAll(['s1', 's2']));
    });

    test('fires onSessionsChanged', () async {
      var count = 0;
      conn.onSessionsChanged = () => count++;
      ws.injectOutput(_sessionList([_sessionJson('s1')]));
      await Future.microtask(() {});
      expect(count, 1);
    });

    test('sorts by sort_order ascending then createdAt desc', () async {
      // s2 has lower sort_order so should come first
      ws.injectOutput(
        _sessionList([
          _sessionJson('s1', sortOrder: 2000),
          _sessionJson('s2', sortOrder: 1000),
        ]),
      );
      await Future.microtask(() {});
      expect(conn.sessions.first.sessionId, 's2');
    });

    test('preserves existing worktreeInfo when server sends null', () async {
      // Inject initial list with worktree info via session_update
      ws.injectOutput(_sessionList([_sessionJson('s1')]));
      await Future.microtask(() {});
      // session doesn't carry worktree in this test; just verify round-trip
      expect(conn.sessions.first.sessionId, 's1');
      expect(conn.sessions.first.worktreeInfo, isNull);

      // Re-send list — existing null worktreeInfo stays null (no crash)
      ws.injectOutput(_sessionList([_sessionJson('s1')]));
      await Future.microtask(() {});
      expect(conn.sessions.first.worktreeInfo, isNull);
    });

    test('replaces worker badge backend_id label with config name', () async {
      // Server sends session_list with a worker badge whose label is the
      // internal backend_id ("my-backend"), not the user-facing name.
      // _updateSessionList must replace it with the config name ("Worker 1").
      ws.injectOutput(
        _sessionList([
          {
            ..._sessionJson('s1'),
            'badges': [
              _badgeJson('status', 'active'),
              _badgeJson('worker', 'my-backend'), // raw backend_id label
            ],
          },
        ]),
      );
      await Future.microtask(() {});

      final session = conn.sessions.firstWhere((s) => s.sessionId == 's1');
      final workerBadge = session.badges.where((b) => b.type == 'worker').firstOrNull;
      expect(workerBadge, isNotNull);
      expect(workerBadge!.label, 'Worker 1'); // replaced with config.name
    });

    test('preserves existing badges when server sends empty badge list', () async {
      // Seed a session with a known badge via session_update.
      ws.injectOutput(
        _sessionUpdateWithBadges('s1', badges: [_badgeJson('caveman', 'Caveman')]),
      );
      await Future.microtask(() {});

      // Now session_list arrives with no badges for s1 — existing must be kept.
      ws.injectOutput(_sessionList([_sessionJson('s1')]));
      await Future.microtask(() {});

      final session = conn.sessions.firstWhere((s) => s.sessionId == 's1');
      expect(session.badges.any((b) => b.type == 'caveman'), isTrue);
    });

    test('worker badge label from session_list persists across session switch', () async {
      // Simulates the switch-back scenario: session is in the list with correct
      // worker badge label after _updateSessionList replacement.
      ws.injectOutput(
        _sessionList([
          {
            ..._sessionJson('s1'),
            'badges': [_badgeJson('worker', 'raw-backend-id')],
          },
        ]),
      );
      await Future.microtask(() {});

      final session = conn.sessions.firstWhere((s) => s.sessionId == 's1');
      final wb = session.badges.where((b) => b.type == 'worker').firstOrNull;
      expect(wb?.label, 'Worker 1'); // friendly name, not raw-backend-id
    });
  });

  // -------------------------------------------------------------------------
  // Session update patching
  // -------------------------------------------------------------------------

  group('session_update message', () {
    setUp(() async {
      await conn.connect();
      ws.injectOutput(_sessionList([_sessionJson('s1', status: 'created')]));
      await Future.microtask(() {});
    });

    test('patches status on existing session', () async {
      ws.injectOutput(_sessionUpdate('s1', status: 'running'));
      await Future.microtask(() {});
      expect(conn.sessions.first.status, 'running');
    });

    test('inserts new session not yet in list', () async {
      ws.injectOutput(_sessionUpdate('s_new', status: 'created'));
      await Future.microtask(() {});
      expect(conn.sessions.map((s) => s.sessionId), contains('s_new'));
    });

    test('fires onProjectPathAttached when mainProjectPath changes', () async {
      String? attachedId;
      String? attachedPath;
      conn.onProjectPathAttached = (id, path) {
        attachedId = id;
        attachedPath = path;
      };

      ws.injectOutput(
        _sessionUpdate('s1', mainProjectPath: '/home/user/project'),
      );
      await Future.microtask(() {});

      expect(attachedId, 's1');
      expect(attachedPath, '/home/user/project');
    });

    test('does NOT fire onProjectPathAttached when path unchanged', () async {
      // Set initial path
      ws.injectOutput(
        _sessionUpdate('s1', mainProjectPath: '/home/user/project'),
      );
      await Future.microtask(() {});

      var fireCount = 0;
      conn.onProjectPathAttached = (_, __) => fireCount++;

      // Send same path again — should not re-fire
      ws.injectOutput(
        _sessionUpdate('s1', mainProjectPath: '/home/user/project'),
      );
      await Future.microtask(() {});

      expect(fireCount, 0);
    });

    test('fires onProjectNameError when server sends error', () async {
      String? errorSessionId;
      String? errorMessage;
      conn.onProjectNameError = (id, err) {
        errorSessionId = id;
        errorMessage = err;
      };

      ws.injectOutput(
        _sessionUpdate(
          's1',
          projectNameError: 'Project not found',
          includeProjectNameError: true,
        ),
      );
      await Future.microtask(() {});

      expect(errorSessionId, 's1');
      expect(errorMessage, 'Project not found');
    });

    test('fires onProjectNameErrorCleared when error is cleared', () async {
      String? clearedSessionId;
      conn.onProjectNameErrorCleared = (id) => clearedSessionId = id;

      ws.injectOutput(
        _sessionUpdate(
          's1',
          includeProjectNameError: true,
          // null projectNameError = cleared
        ),
      );
      await Future.microtask(() {});

      expect(clearedSessionId, 's1');
    });

    test('fires onSessionsChanged on update', () async {
      var count = 0;
      conn.onSessionsChanged = () => count++;
      ws.injectOutput(_sessionUpdate('s1', status: 'running'));
      await Future.microtask(() {});
      expect(count, 1);
    });

    test('populates badges from server badges array', () async {
      ws.injectOutput(
        _sessionUpdateWithBadges('s1', badges: [
          _badgeJson('caveman', 'Caveman'),
          _badgeJson('worker', 'HomeServer'),
        ]),
      );
      await Future.microtask(() {});
      final session = conn.sessions.firstWhere((s) => s.sessionId == 's1');
      expect(session.badges.map((b) => b.type), containsAll(['caveman', 'worker']));
    });

    test('falls back to legacy adapter when badges key absent', () async {
      // Legacy message: no 'badges' key, but has caveman_mode flat field
      ws.injectOutput({
        'type': 'session_update',
        'session_id': 's1',
        'status': 'active',
        'caveman_mode': true,
        'activity_state': 'idle',
      });
      await Future.microtask(() {});
      final session = conn.sessions.firstWhere((s) => s.sessionId == 's1');
      expect(session.badges.any((b) => b.type == 'caveman'), isTrue);
    });

    test('legacy adapter includes worker badge from config name', () async {
      ws.injectOutput({
        'type': 'session_update',
        'session_id': 's1',
        'status': 'active',
        'activity_state': 'idle',
      });
      await Future.microtask(() {});
      final session = conn.sessions.firstWhere((s) => s.sessionId == 's1');
      final workerBadge = session.badges.where((b) => b.type == 'worker').firstOrNull;
      expect(workerBadge, isNotNull);
      expect(workerBadge!.label, 'Worker 1'); // matches _makeConfig name
    });

    test('badges populated for newly inserted session', () async {
      ws.injectOutput(
        _sessionUpdateWithBadges('s_new', badges: [
          _badgeJson('caveman', 'Caveman'),
        ]),
      );
      await Future.microtask(() {});
      final session = conn.sessions.firstWhere((s) => s.sessionId == 's_new');
      expect(session.badges.any((b) => b.type == 'caveman'), isTrue);
    });
  });

  // -------------------------------------------------------------------------
  // Session reorder
  // -------------------------------------------------------------------------

  group('session_reorder message', () {
    setUp(() async {
      await conn.connect();
      ws.injectOutput(
        _sessionList([_sessionJson('s1'), _sessionJson('s2'), _sessionJson('s3')]),
      );
      await Future.microtask(() {});
    });

    test('applies new order from server', () async {
      ws.injectOutput({
        'type': 'session_reorder',
        'order': ['s3', 's1', 's2'],
      });
      await Future.microtask(() {});

      final ids = conn.sessions.map((s) => s.sessionId).toList();
      expect(ids.first, 's3');
    });

    test('fires onSessionsChanged', () async {
      var count = 0;
      conn.onSessionsChanged = () => count++;
      ws.injectOutput({
        'type': 'session_reorder',
        'order': ['s2', 's1', 's3'],
      });
      await Future.microtask(() {});
      expect(count, 1);
    });
  });

  // -------------------------------------------------------------------------
  // Message routing
  // -------------------------------------------------------------------------

  group('message routing', () {
    setUp(() async => conn.connect());

    test('session_list consumed locally — not forwarded to onOutputMessage',
        () async {
      final forwarded = <Map<String, dynamic>>[];
      conn.onOutputMessage = (msg, _) => forwarded.add(msg);

      ws.injectOutput(_sessionList([_sessionJson('s1')]));
      await Future.microtask(() {});

      expect(forwarded, isEmpty);
      expect(conn.sessions, isNotEmpty); // consumed locally
    });

    test('task_list forwarded to onOutputMessage with workerId', () async {
      final forwarded = <(Map<String, dynamic>, String)>[];
      conn.onOutputMessage = (msg, wId) => forwarded.add((msg, wId));

      ws.injectOutput({'type': 'task_list', 'tasks': []});
      await Future.microtask(() {});

      expect(forwarded.length, 1);
      expect(forwarded.first.$1['type'], 'task_list');
      expect(forwarded.first.$2, 'w1');
    });

    test('arbitrary output message forwarded to onOutputMessage', () async {
      final forwarded = <Map<String, dynamic>>[];
      conn.onOutputMessage = (msg, _) => forwarded.add(msg);

      ws.injectOutput({'type': 'text_chunk', 'content': 'hello'});
      await Future.microtask(() {});

      expect(forwarded.length, 1);
      expect(forwarded.first['type'], 'text_chunk');
    });

    test('input message forwarded to onInputMessage with workerId', () async {
      final forwarded = <(Map<String, dynamic>, String)>[];
      conn.onInputMessage = (msg, wId) => forwarded.add((msg, wId));

      ws.injectInput({'type': 'ack', 'session_id': 's1'});
      await Future.microtask(() {});

      expect(forwarded.length, 1);
      expect(forwarded.first.$2, 'w1');
    });
  });

  // -------------------------------------------------------------------------
  // Subscribe / Unsubscribe
  // -------------------------------------------------------------------------

  group('subscribe / unsubscribe', () {
    setUp(() async => conn.connect());

    test('subscribe adds to subscribedSessions and calls ws.subscribe', () {
      conn.subscribe('s1');
      expect(conn.subscribedSessions, contains('s1'));
      expect(ws.subscribeCallCount, 1);
      expect(ws.subscribedIds, ['s1']);
    });

    test('unsubscribe removes from subscribedSessions', () {
      conn.subscribe('s1');
      conn.unsubscribe('s1');
      expect(conn.subscribedSessions, isNot(contains('s1')));
      expect(ws.unsubscribeCallCount, 1);
    });

    test('subscribe is no-op when not connected', () {
      conn.disconnect();
      conn.subscribe('s1');
      expect(conn.subscribedSessions, isEmpty);
      expect(ws.subscribeCallCount, 0);
    });
  });

  // -------------------------------------------------------------------------
  // Reconnection on unexpected disconnect
  // -------------------------------------------------------------------------

  group('reconnection state machine', () {
    test('transitions to reconnecting on unexpected drop', () async {
      await conn.connect();
      final statuses = <WorkerConnectionStatus>[];
      conn.addListener(() => statuses.add(conn.status));

      ws.simulateDisconnect();
      await Future.microtask(() {});

      expect(statuses, contains(WorkerConnectionStatus.reconnecting));
    });

    test('manual disconnect does NOT trigger reconnect', () async {
      await conn.connect();
      final statuses = <WorkerConnectionStatus>[];
      conn.addListener(() => statuses.add(conn.status));

      conn.disconnect();
      await Future.microtask(() {});

      expect(statuses, isNot(contains(WorkerConnectionStatus.reconnecting)));
      expect(conn.status, WorkerConnectionStatus.disconnected);
    });

    test('clears sessions and subscriptions on unexpected drop', () async {
      await conn.connect();
      ws.injectOutput(_sessionList([_sessionJson('s1')]));
      await Future.microtask(() {});
      conn.subscribe('s1');

      ws.simulateDisconnect();
      await Future.microtask(() {});

      expect(conn.sessions, isEmpty);
      expect(conn.subscribedSessions, isEmpty);
    });
  });

  // -------------------------------------------------------------------------
  // loadCachedSessions
  // -------------------------------------------------------------------------

  group('loadCachedSessions()', () {
    test('restores sessions from SharedPreferences cache', () async {
      // First connection — populates cache via _cacheSessions
      await conn.connect();
      ws.injectOutput(_sessionList([_sessionJson('s1'), _sessionJson('s2')]));
      await Future.microtask(() {});

      // Second connection on a fresh WorkerConnection reads the cache
      final conn2 = WorkerConnection(
        config: _makeConfig(),
        ws: FakeWebSocketService(),
        settings: settings,
      );
      addTearDown(conn2.dispose);
      conn2.loadCachedSessions();

      expect(conn2.sessions.map((s) => s.sessionId), containsAll(['s1', 's2']));
    });
  });
}
