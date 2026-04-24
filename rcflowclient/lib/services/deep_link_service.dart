import 'dart:async';

import 'package:app_links/app_links.dart';

/// Parsed form of a `rcflow://add-worker?...` deep link.
class AddWorkerLink {
  final String host;
  final int port;
  final String token;
  final bool ssl;
  final String? name;

  const AddWorkerLink({
    required this.host,
    required this.port,
    required this.token,
    required this.ssl,
    this.name,
  });

  static AddWorkerLink? tryParse(Uri uri) {
    if (uri.scheme != 'rcflow' || uri.host != 'add-worker') return null;
    final host = uri.queryParameters['host'];
    final portStr = uri.queryParameters['port'];
    final token = uri.queryParameters['token'];
    if (host == null || host.isEmpty) return null;
    if (token == null || token.isEmpty) return null;
    final port = int.tryParse(portStr ?? '');
    if (port == null || port < 1 || port > 65535) return null;
    final ssl = uri.queryParameters['ssl'] == '1';
    final name = uri.queryParameters['name'];
    return AddWorkerLink(
      host: host,
      port: port,
      token: token,
      ssl: ssl,
      name: (name != null && name.isNotEmpty) ? name : null,
    );
  }
}

/// Singleton wrapper around [AppLinks] that surfaces parsed
/// `rcflow://add-worker` links as an [AddWorkerLink] stream.
///
/// Subsequent links (the app is already running) arrive on [stream].
/// The first link delivered on cold start is available via [initialLink]
/// after [init] completes.
class DeepLinkService {
  DeepLinkService._();
  static final DeepLinkService instance = DeepLinkService._();

  final AppLinks _appLinks = AppLinks();
  final StreamController<AddWorkerLink> _controller =
      StreamController<AddWorkerLink>.broadcast();
  StreamSubscription<Uri>? _sub;
  AddWorkerLink? _initial;
  bool _initialized = false;

  Stream<AddWorkerLink> get stream => _controller.stream;
  AddWorkerLink? get initialLink => _initial;

  Future<void> init() async {
    if (_initialized) return;
    _initialized = true;

    try {
      final first = await _appLinks.getInitialLink();
      if (first != null) {
        _initial = AddWorkerLink.tryParse(first);
      }
    } catch (_) {
      // Platform plugin may not be available on some desktop builds;
      // fall through to stream subscription below.
    }

    _sub = _appLinks.uriLinkStream.listen(
      (uri) {
        final parsed = AddWorkerLink.tryParse(uri);
        if (parsed != null) _controller.add(parsed);
      },
      onError: (_) {},
    );
  }

  Future<void> dispose() async {
    await _sub?.cancel();
    await _controller.close();
  }
}
