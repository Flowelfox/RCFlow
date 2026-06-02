import 'dart:async';
import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/services/server_url.dart';
import 'package:rcflowclient/services/web_socket_transport.dart';
import 'package:stream_channel/stream_channel.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

/// Records everything written to a channel sink without touching a socket.
class _FakeSink implements WebSocketSink {
  final List<dynamic> added = [];
  bool closed = false;

  @override
  void add(dynamic data) => added.add(data);

  @override
  Future<void> close([int? closeCode, String? closeReason]) async {
    closed = true;
  }

  @override
  void addError(Object error, [StackTrace? stackTrace]) {}

  @override
  Future<void> addStream(Stream<dynamic> stream) async {}

  @override
  Future<void> get done => Future<void>.value();
}

/// In-memory [WebSocketChannel]: `_incoming` drives the read stream, `sink`
/// captures writes. Tests push frames by adding to the backing controller.
class _FakeChannel extends StreamChannelMixin<dynamic>
    implements WebSocketChannel {
  final StreamController<dynamic> incoming;
  final _FakeSink _sink = _FakeSink();

  _FakeChannel(this.incoming);

  @override
  Stream<dynamic> get stream => incoming.stream;

  @override
  WebSocketSink get sink => _sink;

  @override
  int? get closeCode => null;

  @override
  String? get closeReason => null;

  @override
  String? get protocol => null;

  @override
  Future<void> get ready => Future<void>.value();
}

ServerUrl _url() => ServerUrl(rawHost: 'host:8765', apiKey: 'k');

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

  group('WebSocketTransport (connected via injected connector)', () {
    late StreamController<dynamic> inputRaw;
    late StreamController<dynamic> outputRaw;
    late _FakeChannel inputChannel;
    late _FakeChannel outputChannel;
    late WebSocketTransport t;

    setUp(() {
      inputRaw = StreamController<dynamic>.broadcast();
      outputRaw = StreamController<dynamic>.broadcast();
      inputChannel = _FakeChannel(inputRaw);
      outputChannel = _FakeChannel(outputRaw);
      // First connector call is the input channel, second is the output.
      var call = 0;
      t = WebSocketTransport(
        connector: (url, {required secure, required allowSelfSigned}) async {
          return (call++ == 0) ? inputChannel : outputChannel;
        },
      );
    });

    tearDown(() {
      t.dispose();
      inputRaw.close();
      outputRaw.close();
    });

    test('connect opens both channels and reports connected', () async {
      final events = <bool>[];
      final sub = t.connectionStatus.listen(events.add);

      await t.connect(_url(), secure: false, allowSelfSigned: false);
      await Future<void>.delayed(Duration.zero);

      expect(t.isConnected, isTrue);
      expect(events, contains(true));
      await sub.cancel();
    });

    test('incoming input frame is decoded onto inputMessages', () async {
      await t.connect(_url(), secure: false, allowSelfSigned: false);

      final received = <Map<String, dynamic>>[];
      final sub = t.inputMessages.listen(received.add);

      inputRaw.add(jsonEncode({'type': 'prompt', 'text': 'hi'}));
      await Future<void>.delayed(Duration.zero);

      expect(received, hasLength(1));
      expect(received.first['type'], 'prompt');
      expect(received.first['text'], 'hi');
      await sub.cancel();
    });

    test('incoming output frame is decoded onto outputMessages', () async {
      await t.connect(_url(), secure: false, allowSelfSigned: false);

      final received = <Map<String, dynamic>>[];
      final sub = t.outputMessages.listen(received.add);

      outputRaw.add(jsonEncode({'type': 'status', 'value': 1}));
      await Future<void>.delayed(Duration.zero);

      expect(received.single['type'], 'status');
      expect(received.single['value'], 1);
      await sub.cancel();
    });

    test('malformed frame is swallowed (no crash, no emission)', () async {
      await t.connect(_url(), secure: false, allowSelfSigned: false);

      final received = <Map<String, dynamic>>[];
      final sub = t.inputMessages.listen(received.add);

      inputRaw.add('{not valid json');
      await Future<void>.delayed(Duration.zero);

      expect(received, isEmpty);
      await sub.cancel();
    });

    test('sendInput / sendOutput JSON-encode onto the right sink', () async {
      await t.connect(_url(), secure: false, allowSelfSigned: false);

      t.sendInput({'type': 'prompt', 'text': 'go'});
      t.sendOutput({'type': 'subscribe', 'id': 7});

      expect(
        inputChannel._sink.added.single,
        jsonEncode({'type': 'prompt', 'text': 'go'}),
      );
      expect(
        outputChannel._sink.added.single,
        jsonEncode({'type': 'subscribe', 'id': 7}),
      );
    });

    test('input stream closing reports disconnected', () async {
      await t.connect(_url(), secure: false, allowSelfSigned: false);

      final events = <bool>[];
      final sub = t.connectionStatus.listen(events.add);

      await inputRaw.close();
      await Future<void>.delayed(Duration.zero);

      expect(events, contains(false));
      await sub.cancel();
    });

    test('input stream error reports disconnected', () async {
      await t.connect(_url(), secure: false, allowSelfSigned: false);

      final events = <bool>[];
      final sub = t.connectionStatus.listen(events.add);

      inputRaw.addError(Exception('socket blew up'));
      await Future<void>.delayed(Duration.zero);

      expect(events, contains(false));
      await sub.cancel();
    });

    test('disconnect closes both channel sinks', () async {
      await t.connect(_url(), secure: false, allowSelfSigned: false);
      expect(inputChannel._sink.closed, isFalse);
      expect(outputChannel._sink.closed, isFalse);

      t.disconnect();

      expect(inputChannel._sink.closed, isTrue);
      expect(outputChannel._sink.closed, isTrue);
      expect(t.isConnected, isFalse);
    });
  });
}
