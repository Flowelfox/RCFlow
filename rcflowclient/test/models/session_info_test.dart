import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/session_info.dart';

void main() {
  group('SessionInfo.agentType', () {
    SessionInfo base() => SessionInfo(
      sessionId: 'abc123',
      sessionType: 'conversational',
      status: 'active',
      workerId: 'w1',
    );

    test('fromJson parses claude_code agent type', () {
      final info = SessionInfo.fromJson({
        'session_id': 's1',
        'session_type': 'conversational',
        'status': 'active',
        'agent_type': 'claude_code',
      });
      expect(info.agentType, 'claude_code');
    });

    test('fromJson parses codex agent type', () {
      final info = SessionInfo.fromJson({
        'session_id': 's1',
        'session_type': 'conversational',
        'status': 'active',
        'agent_type': 'codex',
      });
      expect(info.agentType, 'codex');
    });

    test('fromJson accepts null agent type', () {
      final info = SessionInfo.fromJson({
        'session_id': 's1',
        'session_type': 'conversational',
        'status': 'active',
      });
      expect(info.agentType, isNull);
    });

    test('toJson includes agent_type when non-null', () {
      final info = base().copyWith(agentType: 'claude_code');
      final json = info.toJson();
      expect(json['agent_type'], 'claude_code');
    });

    test('toJson omits agent_type when null', () {
      final info = base();
      expect(info.agentType, isNull);
      expect(info.toJson().containsKey('agent_type'), isFalse);
    });

    test('copyWith preserves agentType when not specified', () {
      final original = base().copyWith(agentType: 'claude_code');
      final copy = original.copyWith(status: 'paused');
      expect(copy.agentType, 'claude_code');
    });

    test('copyWith can clear agentType with explicit null', () {
      final original = base().copyWith(agentType: 'claude_code');
      final copy = original.copyWith(agentType: null);
      expect(copy.agentType, isNull);
    });
  });
}
