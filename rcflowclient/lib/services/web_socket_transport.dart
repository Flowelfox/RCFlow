import 'dart:async';
import 'dart:convert';
import 'dart:io' as io;

import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

import 'server_url.dart';

/// Owns the raw input/output WebSocket channels and their lifecycle.
///
/// Split out of [WebSocketService]: this class is responsible purely for the
/// transport — opening the two sockets with ping keepalive, decoding incoming
/// frames onto broadcast streams, and writing outbound JSON.  [WebSocketService]
/// composes one of these and exposes the higher-level command/REST surface.
class WebSocketTransport {
  static const _pingInterval = Duration(seconds: 5);

  WebSocketChannel? _inputChannel;
  WebSocketChannel? _outputChannel;

  final _inputController = StreamController<Map<String, dynamic>>.broadcast();
  final _outputController = StreamController<Map<String, dynamic>>.broadcast();
  final _connectionController = StreamController<bool>.broadcast();

  Stream<Map<String, dynamic>> get inputMessages => _inputController.stream;
  Stream<Map<String, dynamic>> get outputMessages => _outputController.stream;
  Stream<bool> get connectionStatus => _connectionController.stream;

  bool get isConnected => _inputChannel != null && _outputChannel != null;

  StreamSubscription<dynamic>? _inputSub;
  StreamSubscription<dynamic>? _outputSub;

  /// Create an [io.HttpClient] that optionally trusts self-signed certificates.
  io.HttpClient _createHttpClient({required bool allowSelfSigned}) {
    final client = io.HttpClient();
    if (allowSelfSigned) {
      client.badCertificateCallback = (cert, host, port) => true;
    }
    return client;
  }

  /// Open a single [io.WebSocket] with ping keepalive and wrap it in an
  /// [IOWebSocketChannel].
  Future<IOWebSocketChannel> _connectSocket(
    Uri url, {
    required bool secure,
    required bool allowSelfSigned,
  }) async {
    io.HttpClient? client;
    if (secure) {
      client = _createHttpClient(allowSelfSigned: allowSelfSigned);
    }
    final socket = await io.WebSocket.connect(
      url.toString(),
      customClient: client,
    ).timeout(const Duration(seconds: 10));
    socket.pingInterval = _pingInterval;
    return IOWebSocketChannel(socket);
  }

  /// Connect both input and output WebSocket channels for [url].
  Future<void> connect(
    ServerUrl url, {
    required bool secure,
    required bool allowSelfSigned,
  }) async {
    disconnect();

    // Connect input channel
    try {
      _inputChannel = await _connectSocket(
        url.wsInputText(),
        secure: secure,
        allowSelfSigned: allowSelfSigned,
      );
    } catch (e) {
      _inputChannel = null;
      _connectionController.add(false);
      rethrow;
    }

    _inputSub = _inputChannel!.stream.listen(
      (data) {
        try {
          final msg = jsonDecode(data as String) as Map<String, dynamic>;
          _inputController.add(msg);
        } catch (_) {}
      },
      onError: (error) {
        _connectionController.add(false);
      },
      onDone: () {
        _connectionController.add(false);
      },
    );

    // Connect output channel after input succeeds
    try {
      _outputChannel = await _connectSocket(
        url.wsOutputText(),
        secure: secure,
        allowSelfSigned: allowSelfSigned,
      );
    } catch (e) {
      _inputChannel?.sink.close();
      _inputChannel = null;
      _outputChannel = null;
      _connectionController.add(false);
      rethrow;
    }

    _outputSub = _outputChannel!.stream.listen(
      (data) {
        try {
          final msg = jsonDecode(data as String) as Map<String, dynamic>;
          _outputController.add(msg);
        } catch (_) {}
      },
      onError: (error) {
        _connectionController.add(false);
      },
      onDone: () {
        _connectionController.add(false);
      },
    );

    _connectionController.add(true);
  }

  /// Encode and send [msg] over the input channel (no-op when disconnected).
  void sendInput(Map<String, dynamic> msg) {
    if (_inputChannel == null) return;
    _inputChannel!.sink.add(jsonEncode(msg));
  }

  /// Encode and send [msg] over the output channel (no-op when disconnected).
  void sendOutput(Map<String, dynamic> msg) {
    if (_outputChannel == null) return;
    _outputChannel!.sink.add(jsonEncode(msg));
  }

  void disconnect() {
    _inputSub?.cancel();
    _outputSub?.cancel();
    _inputSub = null;
    _outputSub = null;
    _inputChannel?.sink.close();
    _outputChannel?.sink.close();
    _inputChannel = null;
    _outputChannel = null;
    // Guard against a post-dispose disconnect (e.g. a doubly-disposed shared
    // instance): emitting on a closed controller would throw.
    if (!_connectionController.isClosed) {
      _connectionController.add(false);
    }
  }

  void dispose() {
    disconnect();
    _inputController.close();
    _outputController.close();
    _connectionController.close();
  }
}
