import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/services/web_socket_transport.dart';

void main() {
  group('WebSocketTransport (disconnected lifecycle)', () {
    test('starts disconnected', () {
      final t = WebSocketTransport();
      expect(t.isConnected, isFalse);
      t.dispose();
    });

    test('sendInput / sendOutput are no-ops when disconnected', () {
      final t = WebSocketTransport();
      // Must not throw with no channels open.
      t.sendInput({'type': 'prompt'});
      t.sendOutput({'type': 'subscribe'});
      t.dispose();
    });

    test('disconnect emits false on connectionStatus', () async {
      final t = WebSocketTransport();
      final events = <bool>[];
      final sub = t.connectionStatus.listen(events.add);
      t.disconnect();
      await Future<void>.delayed(Duration.zero);
      expect(events, contains(false));
      await sub.cancel();
      t.dispose();
    });

    test('dispose then disconnect does not throw (double-dispose guard)', () {
      final t = WebSocketTransport();
      t.dispose();
      // A stray disconnect after dispose must not emit on a closed controller.
      expect(t.disconnect, returnsNormally);
    });

    test('message streams are broadcast (multi-listener)', () {
      final t = WebSocketTransport();
      final s1 = t.inputMessages.listen((_) {});
      final s2 = t.inputMessages.listen((_) {});
      // Two concurrent listeners on a broadcast stream — no "already listened".
      s1.cancel();
      s2.cancel();
      t.dispose();
    });
  });
}
