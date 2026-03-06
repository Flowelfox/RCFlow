/// Builds WebSocket and HTTP URLs from a raw host string.
///
/// Accepts any of:
///   - `192.168.1.100:8765`
///   - `example.com`
///   - `example.com:9000`
///   - `ws://host:port`  (scheme stripped)
///   - `http://host:port` (scheme stripped)
///
/// When [secure] is true, uses `wss://` and `https://` schemes.
class ServerUrl {
  final String host;
  final String apiKey;
  final bool secure;

  ServerUrl({required String rawHost, required this.apiKey, this.secure = false})
      : host = _stripScheme(rawHost);

  static String _stripScheme(String input) {
    var h = input.trim();
    // Remove trailing slashes
    h = h.replaceAll(RegExp(r'/+$'), '');
    // Remove any scheme prefix
    h = h.replaceFirst(RegExp(r'^(wss?|https?)://'), '');
    return h;
  }

  String get _encodedKey => Uri.encodeComponent(apiKey);
  String get _wsScheme => secure ? 'wss' : 'ws';
  String get _httpScheme => secure ? 'https' : 'http';

  Uri wsInputText() =>
      Uri.parse('$_wsScheme://$host/ws/input/text?api_key=$_encodedKey');

  Uri wsOutputText() =>
      Uri.parse('$_wsScheme://$host/ws/output/text?api_key=$_encodedKey');

  Uri wsTerminal() =>
      Uri.parse('$_wsScheme://$host/ws/terminal?api_key=$_encodedKey');

  Uri http(String path, [Map<String, String>? queryParams]) {
    final params = {'api_key': apiKey, ...?queryParams};
    return Uri.parse('$_httpScheme://$host$path')
        .replace(queryParameters: params);
  }
}
