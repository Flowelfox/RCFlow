import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/app_notification.dart';
import 'package:rcflowclient/models/session_info.dart';
import 'package:rcflowclient/models/ws_messages.dart';
import 'package:rcflowclient/services/websocket_service.dart';
import 'package:rcflowclient/state/output_handlers.dart';
import 'package:rcflowclient/state/pane_state.dart';

class _FakePaneHost implements PaneHost {
  @override
  bool get connected => false;

  @override
  List<SessionInfo> get sessions => [];

  @override
  WebSocketService wsForWorker(String workerId) =>
      throw UnimplementedError();

  @override
  String? workerIdForSession(String sessionId) => null;

  @override
  String? get defaultWorkerId => null;

  @override
  void refreshSessions() {}

  @override
  void addSystemMessageToPane(String paneId, String text,
      {bool isError = false, String? label}) {}

  @override
  void muteSessionSound(String sessionId) {}

  @override
  void markSubscribed(String sessionId, {required String workerId}) {}

  @override
  void requestUnsubscribe(String sessionId, String workerId) {}

  @override
  void showNotification(
      {required NotificationLevel level,
      required String title,
      String? body}) {}

  @override
  bool workerSupportsAttachments(String? workerId) => false;

  @override
  bool workerSupportsImageAttachments(String? workerId) => false;
}

void main() {
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
}
