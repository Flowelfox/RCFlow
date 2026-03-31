import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/ws_messages.dart';
import 'package:rcflowclient/models/session_info.dart';
import 'package:rcflowclient/services/server_url.dart';

void main() {
  group('DisplayMessage', () {
    test('content is mutable for streaming', () {
      final msg = DisplayMessage(
        type: DisplayMessageType.assistant,
        content: 'Hello',
      );
      msg.content += ' World';
      expect(msg.content, 'Hello World');
    });

    test('finished defaults to false', () {
      final msg = DisplayMessage(type: DisplayMessageType.toolBlock);
      expect(msg.finished, false);
    });

    test('finished can be set to true', () {
      final msg = DisplayMessage(type: DisplayMessageType.toolBlock);
      msg.finished = true;
      expect(msg.finished, true);
    });
  });

  group('SessionInfo', () {
    test('fromJson parses all fields', () {
      final session = SessionInfo.fromJson({
        'session_id': 'abc12345-6789-0000-1111-222233334444',
        'session_type': 'one-shot',
        'status': 'active',
      });
      expect(session.sessionId, 'abc12345-6789-0000-1111-222233334444');
      expect(session.sessionType, 'one-shot');
      expect(session.status, 'active');
    });

    test('fromJson handles missing optional fields', () {
      final session = SessionInfo.fromJson({'session_id': 'test-id'});
      expect(session.sessionType, 'unknown');
      expect(session.status, 'unknown');
    });

    test('shortId truncates to first 8 chars', () {
      final session = SessionInfo.fromJson({
        'session_id': 'abc12345-6789-0000-1111-222233334444',
      });
      expect(session.shortId, 'abc12345...');
    });

    test('shortId handles short IDs', () {
      final session = SessionInfo.fromJson({'session_id': 'short'});
      expect(session.shortId, 'short');
    });
  });

  group('DisplayMessageType', () {
    test('has all expected values', () {
      expect(DisplayMessageType.values, [
        DisplayMessageType.user,
        DisplayMessageType.assistant,
        DisplayMessageType.toolBlock,
        DisplayMessageType.error,
        DisplayMessageType.system,
        DisplayMessageType.summary,
        DisplayMessageType.sessionEndAsk,
        DisplayMessageType.planModeAsk,
        DisplayMessageType.planReviewAsk,
        DisplayMessageType.permissionRequest,
        DisplayMessageType.agentGroup,
        DisplayMessageType.agentSessionStart,
        DisplayMessageType.thinking,
        DisplayMessageType.todoUpdate,
        DisplayMessageType.pausedMaxTurns,
      ]);
    });
  });

  group('ServerUrl', () {
    test('builds ws URLs from raw ip:port', () {
      final url = ServerUrl(rawHost: '192.168.1.100:8765', apiKey: 'mykey');
      expect(
        url.wsInputText().toString(),
        'ws://192.168.1.100:8765/ws/input/text?api_key=mykey',
      );
      expect(
        url.wsOutputText().toString(),
        'ws://192.168.1.100:8765/ws/output/text?api_key=mykey',
      );
    });

    test('strips ws:// scheme', () {
      final url = ServerUrl(rawHost: 'ws://example.com:9000', apiKey: 'k');
      expect(url.wsInputText().host, 'example.com');
      expect(url.wsInputText().port, 9000);
    });

    test('strips http:// scheme', () {
      final url = ServerUrl(rawHost: 'http://10.0.0.1:8765/', apiKey: 'k');
      expect(url.host, '10.0.0.1:8765');
    });

    test('strips trailing slashes', () {
      final url = ServerUrl(rawHost: '10.0.0.1:8765///', apiKey: 'k');
      expect(url.host, '10.0.0.1:8765');
    });

    test('builds http URL with path and query params', () {
      final url = ServerUrl(rawHost: '192.168.1.1:8765', apiKey: 'mykey');
      final httpUrl = url.http('/api/sessions');
      expect(httpUrl.scheme, 'http');
      expect(httpUrl.host, '192.168.1.1');
      expect(httpUrl.port, 8765);
      expect(httpUrl.path, '/api/sessions');
      expect(httpUrl.queryParameters['api_key'], 'mykey');
    });

    test('encodes special characters in api key for ws URLs', () {
      final url = ServerUrl(rawHost: 'host:8765', apiKey: 'key with spaces');
      expect(
        url.wsInputText().toString(),
        contains('api_key=key%20with%20spaces'),
      );
    });
  });
}
