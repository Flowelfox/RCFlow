import 'dart:convert';
import 'dart:io' as io;

import 'package:flutter/foundation.dart' show kIsWeb;

import '../models/update_info.dart';

/// Abstract interface for fetching the latest release metadata.
///
/// The injectable design lets tests supply a [FakeUpdateFetcher] without
/// touching the network.
abstract class UpdateFetcher {
  Future<UpdateInfo?> fetchLatestUpdate();
}

/// Production implementation that fetches from the GitHub Releases API.
///
/// Uses a plain [io.HttpClient] with TLS validation intact — no certificate
/// overrides are applied here.
class HttpUpdateFetcher implements UpdateFetcher {
  static const String _apiUrl =
      'https://api.github.com/repos/Flowelfox/RCFlow/releases/latest';

  @override
  Future<UpdateInfo?> fetchLatestUpdate() async {
    // dart:io is unavailable on web; skip silently.
    if (kIsWeb) return null;

    final client = io.HttpClient();
    try {
      final uri = Uri.parse(_apiUrl);
      final request = await client.getUrl(uri);
      request.headers.set('Accept', 'application/vnd.github+json');
      request.headers.set('User-Agent', 'rcflowclient');
      final response = await request.close();

      if (response.statusCode != 200) return null;

      final body = await response.transform(utf8.decoder).join();
      final data = jsonDecode(body) as Map<String, dynamic>;

      final tagName = data['tag_name'] as String?;
      final htmlUrl = data['html_url'] as String?;
      if (tagName == null || htmlUrl == null) return null;

      final version = _normalizeVersion(tagName);
      final assets = data['assets'] as List<dynamic>? ?? [];
      final downloadUrl = _selectDownloadUrl(assets, version);

      return UpdateInfo(
        version: version,
        releaseUrl: htmlUrl,
        downloadUrl: downloadUrl,
      );
    } finally {
      client.close(force: false);
    }
  }

  /// Strips a leading 'v' and any '+build' suffix from a version string.
  ///
  /// Examples:
  ///   "v1.38.0"     → "1.38.0"
  ///   "1.38.0+75"   → "1.38.0"
  ///   "v1.38.0+75"  → "1.38.0"
  static String normalizeVersion(String version) {
    String v = version.trim();
    if (v.startsWith('v') || v.startsWith('V')) v = v.substring(1);
    final plusIdx = v.indexOf('+');
    if (plusIdx != -1) v = v.substring(0, plusIdx);
    return v;
  }

  String _normalizeVersion(String version) => normalizeVersion(version);

  /// Picks the best direct-download URL for the running platform.
  ///
  /// Matches asset names produced by the release workflow, e.g.:
  ///   rcflow-v1.38.0-linux-client-amd64.deb
  ///   rcflow-v1.38.0-windows-client-amd64.exe
  ///   rcflow-v1.38.0-macos-client-arm64.dmg
  ///   rcflow-v1.38.0-android-client-arm64.apk
  String? _selectDownloadUrl(List<dynamic> assets, String version) {
    // kIsWeb guard: Platform.* is unavailable on web.
    if (kIsWeb) return null;

    String suffix;
    if (io.Platform.isLinux) {
      suffix = 'linux-client-amd64.deb';
    } else if (io.Platform.isWindows) {
      suffix = 'windows-client-amd64.exe';
    } else if (io.Platform.isMacOS) {
      suffix = 'macos-client';
    } else if (io.Platform.isAndroid) {
      suffix = 'android-client-arm64.apk';
    } else {
      return null;
    }

    for (final asset in assets) {
      final map = asset as Map<String, dynamic>;
      final name = map['name'] as String? ?? '';
      if (name.contains(suffix)) {
        return map['browser_download_url'] as String?;
      }
    }
    return null;
  }
}
