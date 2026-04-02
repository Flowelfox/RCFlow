import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/app_notification.dart';
import 'package:rcflowclient/models/session_info.dart';
import 'package:rcflowclient/models/ws_messages.dart';
import 'package:rcflowclient/services/websocket_service.dart';
import 'package:rcflowclient/state/output_handlers.dart';
import 'package:rcflowclient/state/pane_state.dart';

class _FakePaneHost implements PaneHost {
  final bool connected_;
  _FakePaneHost({bool connected = false}) : connected_ = connected;

  @override
  bool get connected => connected_;

  @override
  List<SessionInfo> get sessions => [];

  @override
  WebSocketService wsForWorker(String workerId) => throw UnimplementedError();

  @override
  String? workerIdForSession(String sessionId) => null;

  @override
  String? get defaultWorkerId => null;

  @override
  void refreshSessions() {}

  @override
  void addSystemMessageToPane(
    String paneId,
    String text, {
    bool isError = false,
    String? label,
  }) {}

  @override
  void markSubscribed(String sessionId, {required String workerId}) {}

  @override
  void requestUnsubscribe(String sessionId, String workerId) {}

  @override
  void showNotification({
    required NotificationLevel level,
    required String title,
    String? body,
  }) {}

  @override
  bool workerSupportsAttachments(String? workerId) => false;

  @override
  bool workerSupportsImageAttachments(String? workerId) => false;

  @override
  String? defaultAgentForWorker(String? workerId) => null;

  @override
  String? getLastProjectForWorker(String workerId) => null;

  @override
  String? getLastAgentForWorker(String workerId) => null;

  @override
  Future<String?> resolveProjectOnWorker(
    String workerId,
    String projectName,
  ) async => null;

  @override
  void muteSessionSound(String sessionId) {}
}

void main() {
  group('handleTextChunk — user message attachments', () {
    late PaneState pane;

    setUp(() {
      pane = PaneState(paneId: 'test', host: _FakePaneHost());
    });

    test(
      'user text_chunk without attachments creates message with null attachments',
      () {
        handleTextChunk({
          'role': 'user',
          'content': 'hello',
          'session_id': 's1',
        }, pane);

        expect(pane.messages.length, 1);
        final msg = pane.messages.first;
        expect(msg.type, DisplayMessageType.user);
        expect(msg.content, 'hello');
        expect(msg.attachments, isNull);
      },
    );

    test('user text_chunk with attachments propagates attachment metadata', () {
      handleTextChunk({
        'role': 'user',
        'content': 'see image',
        'session_id': 's1',
        'attachments': [
          {'name': 'photo.png', 'mime_type': 'image/png', 'size': 1024},
        ],
      }, pane);

      expect(pane.messages.length, 1);
      final msg = pane.messages.first;
      expect(msg.type, DisplayMessageType.user);
      expect(msg.attachments, isNotNull);
      expect(msg.attachments!.length, 1);
      expect(msg.attachments!.first['name'], 'photo.png');
      expect(msg.attachments!.first['mime_type'], 'image/png');
    });

    test('user text_chunk with multiple attachments preserves all entries', () {
      handleTextChunk({
        'role': 'user',
        'content': 'files attached',
        'session_id': 's1',
        'attachments': [
          {'name': 'a.png', 'mime_type': 'image/png', 'size': 100},
          {'name': 'b.txt', 'mime_type': 'text/plain', 'size': 200},
        ],
      }, pane);

      final msg = pane.messages.first;
      expect(msg.attachments!.length, 2);
      expect(msg.attachments![0]['name'], 'a.png');
      expect(msg.attachments![1]['name'], 'b.txt');
    });

    test('local echo with attachments is consumed and not duplicated', () {
      // Use a connected host so sendPrompt actually enqueues the local message.
      final connectedPane = PaneState(
        paneId: 'p2',
        host: _FakePaneHost(connected: true),
      );
      // Simulate local message added by sendPrompt (with attachments already set)
      connectedPane.sendPrompt(
        'hello',
        attachments: [
          {'id': 'att1', 'name': 'photo.png', 'mime_type': 'image/png'},
        ],
      );
      expect(connectedPane.messages.length, 1);
      expect(connectedPane.messages.first.attachments, isNotNull);

      // Server echo should be consumed, not add a duplicate
      handleTextChunk({
        'role': 'user',
        'content': 'hello',
        'session_id': 's1',
        'attachments': [
          {'name': 'photo.png', 'mime_type': 'image/png', 'size': 1024},
        ],
      }, connectedPane);

      expect(connectedPane.messages.length, 1); // still only one message
    });
  });

  group('buildTextChunkHistory — user message attachments', () {
    test('history user message without attachments has null attachments', () {
      final messages = <DisplayMessage>[];
      buildTextChunkHistory(
        {
          'type': 'text_chunk',
          'content': 'hello',
          'metadata': {'role': 'user', 'content': 'hello'},
        },
        'sess1',
        messages,
      );

      expect(messages.length, 1);
      expect(messages.first.attachments, isNull);
    });

    test('history user message with attachments propagates metadata', () {
      final messages = <DisplayMessage>[];
      buildTextChunkHistory(
        {
          'type': 'text_chunk',
          'content': 'see image',
          'metadata': {
            'role': 'user',
            'content': 'see image',
            'attachments': [
              {'name': 'photo.png', 'mime_type': 'image/png', 'size': 1024},
            ],
          },
        },
        'sess1',
        messages,
      );

      expect(messages.length, 1);
      final msg = messages.first;
      expect(msg.attachments, isNotNull);
      expect(msg.attachments!.length, 1);
      expect(msg.attachments!.first['name'], 'photo.png');
      expect(msg.attachments!.first['mime_type'], 'image/png');
    });

    test('history user message with multiple attachments preserves all', () {
      final messages = <DisplayMessage>[];
      buildTextChunkHistory(
        {
          'type': 'text_chunk',
          'content': 'two files',
          'metadata': {
            'role': 'user',
            'content': 'two files',
            'attachments': [
              {'name': 'img.jpg', 'mime_type': 'image/jpeg', 'size': 500},
              {'name': 'notes.md', 'mime_type': 'text/markdown', 'size': 200},
            ],
          },
        },
        'sess1',
        messages,
      );

      expect(messages.first.attachments!.length, 2);
      expect(messages.first.attachments![0]['name'], 'img.jpg');
      expect(messages.first.attachments![1]['name'], 'notes.md');
    });
  });

  group('handlePermissionRequest', () {
    late PaneState pane;

    setUp(() {
      pane = PaneState(paneId: 'test', host: _FakePaneHost());
    });

    test('outside agent mode adds permission to top-level messages', () {
      handlePermissionRequest({
        'session_id': 'sess1',
        'description': 'Run shell command',
        'request_id': 'req1',
        'tool_name': 'bash',
        'tool_input': {'command': 'ls'},
        'risk_level': 'low',
        'scope_options': [],
      }, pane);

      expect(pane.messages.length, 1);
      expect(pane.messages.first.type, DisplayMessageType.permissionRequest);
    });

    test('inside agent mode adds permission inside the agent group', () {
      // Enter agent mode and trigger the first tool block so the group exists.
      pane.startAgentGroup('claude_code', null, displayName: 'Claude Code');
      pane.startToolBlock('bash', {'command': 'ls'});

      final messagesBefore = pane.messages.length;

      handlePermissionRequest({
        'session_id': 'sess1',
        'description': 'Run shell command',
        'request_id': 'req1',
        'tool_name': 'bash',
        'tool_input': {'command': 'rm -rf /'},
        'risk_level': 'high',
        'scope_options': ['once', 'always'],
      }, pane);

      // No new top-level message should have been added.
      expect(pane.messages.length, messagesBefore);

      // The permission should be inside the agent group's children.
      final group = pane.messages.firstWhere(
        (m) => m.type == DisplayMessageType.agentGroup,
      );
      final permissionChildren = group.children!
          .where((c) => c.type == DisplayMessageType.permissionRequest)
          .toList();
      expect(permissionChildren.length, 1);
      expect(permissionChildren.first.content, 'Run shell command');
    });

    test('inside agent mode permission appears after preceding tool block', () {
      pane.startAgentGroup('claude_code', null);
      pane.startToolBlock('read_file', {'path': '/etc/passwd'});

      handlePermissionRequest({
        'session_id': 'sess1',
        'description': 'Read sensitive file',
        'request_id': 'req2',
        'tool_name': 'read_file',
        'tool_input': {'path': '/etc/passwd'},
        'risk_level': 'medium',
        'scope_options': [],
      }, pane);

      final group = pane.messages.firstWhere(
        (m) => m.type == DisplayMessageType.agentGroup,
      );
      final children = group.children!;
      expect(children.length, 2);
      expect(children[0].type, DisplayMessageType.toolBlock);
      expect(children[1].type, DisplayMessageType.permissionRequest);
    });

    test('pre-resolved permission (accepted field) is stored correctly', () {
      pane.startAgentGroup('claude_code', null);

      handlePermissionRequest({
        'session_id': 'sess1',
        'description': 'Execute command',
        'request_id': 'req3',
        'tool_name': 'bash',
        'tool_input': {},
        'risk_level': 'low',
        'scope_options': [],
        'accepted': true,
      }, pane);

      final group = pane.messages.firstWhere(
        (m) => m.type == DisplayMessageType.agentGroup,
      );
      final permission = group.children!.first;
      expect(permission.type, DisplayMessageType.permissionRequest);
      expect(permission.accepted, true);
    });
  });

  group('handlePlanReviewAsk — content and deduplication', () {
    late PaneState pane;

    setUp(() {
      pane = PaneState(paneId: 'test', host: _FakePaneHost());
    });

    test('extracts plan text from plan_input.plan field', () {
      handlePlanReviewAsk({
        'session_id': 's1',
        'plan_input': {'plan': '1. Do X\n2. Do Y'},
      }, pane);

      expect(pane.messages.length, 1);
      final msg = pane.messages.first;
      expect(msg.type, DisplayMessageType.planReviewAsk);
      expect(msg.content, '1. Do X\n2. Do Y');
      expect(msg.accepted, isNull); // pending
    });

    test('falls back to plan_input.content when plan key is absent', () {
      handlePlanReviewAsk({
        'session_id': 's1',
        'plan_input': {'content': 'Step A\nStep B'},
      }, pane);

      expect(pane.messages.first.content, 'Step A\nStep B');
    });

    test('empty content when plan_input is absent', () {
      handlePlanReviewAsk({'session_id': 's1'}, pane);

      expect(pane.messages.first.content, '');
    });

    test('deduplicates: second pending review ask is ignored', () {
      handlePlanReviewAsk({'session_id': 's1', 'plan_input': null}, pane);
      handlePlanReviewAsk({'session_id': 's1', 'plan_input': null}, pane);

      expect(pane.messages.length, 1);
    });

    test('does not deduplicate if first is already resolved', () {
      handlePlanReviewAsk({'session_id': 's1', 'plan_input': null}, pane);
      // Resolve the first one
      pane.messages.first.accepted = true;

      // A second pending one should now be allowed through
      handlePlanReviewAsk({'session_id': 's1', 'plan_input': null}, pane);

      expect(pane.messages.length, 2);
    });
  });

  group('handleSubprocessStatus', () {
    late PaneState pane;

    setUp(() {
      pane = PaneState(paneId: 'test', host: _FakePaneHost());
    });

    test('sets runningSubprocess when subprocess_type is present', () {
      handleSubprocessStatus({
        'session_id': 's1',
        'subprocess_type': 'claude_code',
        'display_name': 'Claude Code',
        'working_directory': '/home/user/project',
        'started_at': DateTime.now().toIso8601String(),
      }, pane);

      expect(pane.runningSubprocess, isNotNull);
      expect(pane.runningSubprocess!.subprocessType, 'claude_code');
      expect(pane.runningSubprocess!.displayName, 'Claude Code');
      expect(pane.runningSubprocess!.workingDirectory, '/home/user/project');
    });

    test('sets currentTool when present', () {
      handleSubprocessStatus({
        'session_id': 's1',
        'subprocess_type': 'claude_code',
        'display_name': 'Claude Code',
        'working_directory': '/repo',
        'current_tool': 'Bash',
        'started_at': DateTime.now().toIso8601String(),
      }, pane);

      expect(pane.runningSubprocess!.currentTool, 'Bash');
    });

    test('clears runningSubprocess when subprocess_type is null', () {
      // First set a subprocess
      handleSubprocessStatus({
        'session_id': 's1',
        'subprocess_type': 'claude_code',
        'display_name': 'Claude Code',
        'working_directory': '/repo',
        'started_at': DateTime.now().toIso8601String(),
      }, pane);
      expect(pane.runningSubprocess, isNotNull);

      // Then clear it
      handleSubprocessStatus({
        'session_id': 's1',
        'subprocess_type': null,
      }, pane);

      expect(pane.runningSubprocess, isNull);
    });

    test('clears runningSubprocess when subprocess_type key is absent', () {
      handleSubprocessStatus({
        'session_id': 's1',
        'subprocess_type': 'claude_code',
        'display_name': 'Claude Code',
        'working_directory': '/repo',
        'started_at': DateTime.now().toIso8601String(),
      }, pane);

      handleSubprocessStatus({'session_id': 's1'}, pane);

      expect(pane.runningSubprocess, isNull);
    });

    test('is registered in outputHandlerRegistry', () {
      expect(outputHandlerRegistry.containsKey('subprocess_status'), isTrue);
    });
  });

  group('buildPlanReviewAskHistory — content extraction', () {
    test('extracts plan text from metadata.plan_input.plan', () {
      final messages = <DisplayMessage>[];
      buildPlanReviewAskHistory(
        {
          'type': 'plan_review_ask',
          'content': '',
          'metadata': {
            'plan_input': {'plan': 'Plan step 1\nPlan step 2'},
            'accepted': true,
          },
        },
        'sess1',
        messages,
      );

      expect(messages.length, 1);
      expect(messages.first.content, 'Plan step 1\nPlan step 2');
      expect(messages.first.accepted, true);
    });

    test('empty content when plan_input absent in metadata', () {
      final messages = <DisplayMessage>[];
      buildPlanReviewAskHistory(
        {
          'type': 'plan_review_ask',
          'content': '',
          'metadata': {'accepted': true},
        },
        'sess1',
        messages,
      );

      expect(messages.first.content, '');
    });
  });
}
