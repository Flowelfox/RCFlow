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
/// `rcflow://add-worker` links as a single [AddWorkerLink] stream.
///
/// On Windows the underlying plugin delivers the cold-start URI through both
/// [AppLinks.getInitialLink] and [AppLinks.uriLinkStream], which would otherwise
/// open the "Add Worker" dialog twice. This service collapses those sources
/// behind a short URI-equality dedupe window. Cold-start links that arrive
/// before any UI listener has subscribed are buffered and replayed once on the
/// first subscription.
class DeepLinkService {
  DeepLinkService._();
  static final DeepLinkService instance = DeepLinkService._();

  final AppLinks _appLinks = AppLinks();
  final StreamController<AddWorkerLink> _controller =
      StreamController<AddWorkerLink>.broadcast();
  StreamSubscription<Uri>? _sub;
  bool _initialized = false;

  final List<AddWorkerLink> _pending = [];
  bool _drained = false;

  Uri? _lastUri;
  DateTime? _lastUriAt;
  static const Duration _dedupeWindow = Duration(seconds: 3);

  Stream<AddWorkerLink> get stream {
    if (!_drained) {
      _drained = true;
      if (_pending.isNotEmpty) {
        final pending = List<AddWorkerLink>.from(_pending);
        _pending.clear();
        scheduleMicrotask(() {
          for (final link in pending) {
            _controller.add(link);
          }
        });
      }
    }
    return _controller.stream;
  }

  void _emit(Uri uri) {
    final now = DateTime.now();
    final last = _lastUriAt;
    if (_lastUri == uri &&
        last != null &&
        now.difference(last) < _dedupeWindow) {
      return;
    }
    _lastUri = uri;
    _lastUriAt = now;

    final parsed = AddWorkerLink.tryParse(uri);
    if (parsed == null) return;

    if (_drained) {
      _controller.add(parsed);
    } else {
      _pending.add(parsed);
    }
  }

  Future<void> init() async {
    if (_initialized) return;
    _initialized = true;

    _sub = _appLinks.uriLinkStream.listen(_emit, onError: (_) {});

    try {
      final first = await _appLinks.getInitialLink();
      if (first != null) {
        _emit(first);
      }
    } catch (_) {
      // Platform plugin may not be available on some desktop builds.
    }
  }

  Future<void> dispose() async {
    await _sub?.cancel();
    await _controller.close();
  }
}
