import 'dart:async';
import 'dart:convert';
import 'dart:io' as io;
import 'dart:typed_data';

import 'package:web_socket_channel/io.dart';
import 'package:web_socket_channel/web_socket_channel.dart';

/// Manages a WebSocket connection to /ws/terminal for one worker.
///
/// Multiplexes multiple terminal sessions over a single WebSocket.
/// Binary frames carry terminal I/O; JSON text frames carry control messages.
class TerminalService {
  WebSocketChannel? _channel;
  StreamSubscription<dynamic>? _channelSub;

  final _outputControllers = <String, StreamController<Uint8List>>{};
  final _controlController =
      StreamController<Map<String, dynamic>>.broadcast();

  /// Stream of JSON control messages from server (created, closed, error).
  Stream<Map<String, dynamic>> get controlMessages =>
      _controlController.stream;

  bool get isConnected => _channel != null;

  /// Register a terminal ID to receive output. Returns the stream controller.
  StreamController<Uint8List> registerTerminal(String terminalId) {
    final controller = StreamController<Uint8List>.broadcast();
    _outputControllers[terminalId] = controller;
    return controller;
  }

  /// Unregister a terminal ID.
  void unregisterTerminal(String terminalId) {
    _outputControllers[terminalId]?.close();
    _outputControllers.remove(terminalId);
  }

  Future<void> connect(
    String host,
    String apiKey, {
    bool secure = false,
    bool allowSelfSigned = true,
  }) async {
    disconnect();

    final scheme = secure ? 'wss' : 'ws';
    // Strip any existing scheme
    var h = host.trim().replaceAll(RegExp(r'/+$'), '');
    h = h.replaceFirst(RegExp(r'^(wss?|https?)://'), '');
    final encodedKey = Uri.encodeComponent(apiKey);
    final wsUrl = Uri.parse('$scheme://$h/ws/terminal?api_key=$encodedKey');

    io.HttpClient? client;
    if (secure) {
      client = io.HttpClient();
      if (allowSelfSigned) {
        client.badCertificateCallback = (cert, host, port) => true;
      }
    }

    final socket = await io.WebSocket.connect(
      wsUrl.toString(),
      customClient: client,
    ).timeout(const Duration(seconds: 10));
    socket.pingInterval = const Duration(seconds: 5);
    _channel = IOWebSocketChannel(socket);

    _channelSub = _channel!.stream.listen(
      (data) {
        if (data is String) {
          // JSON control message
          try {
            final msg = jsonDecode(data) as Map<String, dynamic>;
            _controlController.add(msg);
          } catch (_) {}
        } else if (data is List<int>) {
          // Binary I/O frame
          final bytes = Uint8List.fromList(data);
          if (bytes.length < 17) return;

          final direction = bytes[0];
          if (direction != 0x01) return; // Only server→client output

          final tidBytes = bytes.sublist(1, 17);
          final terminalId = _uuidFromBytes(tidBytes);
          final payload = bytes.sublist(17);

          _outputControllers[terminalId]?.add(payload);
        }
      },
      onError: (e) {
        _channel = null;
        _channelSub = null;
        _closeAllOutputStreams();
      },
      onDone: () {
        _channel = null;
        _channelSub = null;
        _closeAllOutputStreams();
      },
    );
  }

  /// Send a JSON control message.
  void sendControl(Map<String, dynamic> message) {
    _channel?.sink.add(jsonEncode(message));
  }

  /// Send terminal input data for a specific terminal session.
  void sendInput(String terminalId, Uint8List data) {
    if (_channel == null) return;
    final tidBytes = _uuidToBytes(terminalId);
    final frame = Uint8List(1 + 16 + data.length);
    frame[0] = 0x00; // direction: client → server
    frame.setRange(1, 17, tidBytes);
    frame.setRange(17, 17 + data.length, data);
    _channel!.sink.add(frame);
  }

  void disconnect() {
    _channelSub?.cancel();
    _channelSub = null;
    _channel?.sink.close();
    _channel = null;
    _closeAllOutputStreams();
  }

  void _closeAllOutputStreams() {
    for (final controller in _outputControllers.values) {
      controller.close();
    }
    _outputControllers.clear();
  }

  /// Convert UUID string to 16 bytes.
  static Uint8List _uuidToBytes(String uuid) {
    final hex = uuid.replaceAll('-', '');
    final bytes = Uint8List(16);
    for (var i = 0; i < 16; i++) {
      bytes[i] = int.parse(hex.substring(i * 2, i * 2 + 2), radix: 16);
    }
    return bytes;
  }

  /// Convert 16 bytes to UUID string.
  static String _uuidFromBytes(Uint8List bytes) {
    final hex =
        bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
    return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
        '${hex.substring(12, 16)}-${hex.substring(16, 20)}-${hex.substring(20)}';
  }
}
