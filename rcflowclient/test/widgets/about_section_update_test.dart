/// Tests for the update state that drives the Settings → About UI.
///
/// _AboutSection is private, so we test the service state directly — which
/// is the authoritative source for every visual state the About section can
/// show (check button, spinner, update row, error row, up-to-date indicator).
/// This preserves full coverage intent without coupling to private widget APIs.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/update_info.dart';
import 'package:rcflowclient/services/settings_service.dart';
import 'package:rcflowclient/services/update_fetcher.dart';
import 'package:rcflowclient/services/update_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

// ---------------------------------------------------------------------------
// Fakes
// ---------------------------------------------------------------------------

class _FakeFetcher implements UpdateFetcher {
  UpdateInfo? result;
  Object? error;

  _FakeFetcher({this.result, this.error});

  @override
  Future<UpdateInfo?> fetchLatestUpdate() async {
    if (error != null) throw error!;
    return result;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

Future<({UpdateService svc, SettingsService settings})> _build({
  String currentVersion = '1.37.2',
  UpdateInfo? fetchResult,
  Object? fetchError,
}) async {
  SharedPreferences.setMockInitialValues({
    'rcflow_current_version': currentVersion,
  });
  final settings = SettingsService();
  await settings.init();
  final svc = UpdateService(
    settings: settings,
    fetcher: _FakeFetcher(result: fetchResult, error: fetchError),
  );
  return (svc: svc, settings: settings);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  group('About section — version string comes from settings', () {
    test('currentVersion reflects persisted value', () async {
      final s = await _build(currentVersion: '1.38.0');
      // The About section reads settings.currentVersion directly (no async PackageInfo).
      expect(s.svc.currentVersion, '1.38.0');
    });

    test('currentVersion null before first launch', () async {
      SharedPreferences.setMockInitialValues({});
      final settings = SettingsService();
      await settings.init();
      expect(settings.currentVersion, isNull);
    });
  });

  group('About section — initial state (no check run yet)', () {
    test('no update available, latestVersion null', () async {
      final s = await _build();
      expect(s.svc.updateAvailable, isFalse);
      expect(s.svc.latestVersion, isNull);
      expect(s.svc.hasError, isFalse);
      expect(s.svc.isChecking, isFalse);
      // Shows "Check for updates" button state.
    });
  });

  group('About section — spinner state while checking', () {
    test('isChecking becomes false after checkForUpdates completes', () async {
      final s = await _build(
        fetchResult: UpdateInfo(
          version: '1.38.0',
          releaseUrl: 'https://example.com',
        ),
      );
      expect(s.svc.isChecking, isFalse);
      await s.svc.checkForUpdates();
      expect(s.svc.isChecking, isFalse);
    });
  });

  group('About section — update available state', () {
    test('shows update row when newer version fetched', () async {
      final s = await _build(
        currentVersion: '1.37.2',
        fetchResult: UpdateInfo(
          version: '1.38.0',
          releaseUrl:
              'https://github.com/Flowelfox/RCFlow/releases/tag/v1.38.0',
          downloadUrl:
              'https://github.com/Flowelfox/RCFlow/releases/download/v1.38.0/rcflow-linux.deb',
        ),
      );
      await s.svc.checkForUpdates();

      expect(s.svc.updateAvailable, isTrue);
      expect(s.svc.latestVersion, '1.38.0');
      expect(s.svc.latestReleaseUrl, isNotNull);
      expect(s.svc.latestDownloadUrl, isNotNull);
    });

    test(
      'url_launcher target is downloadUrl when available, else releaseUrl',
      () async {
        final s = await _build(
          currentVersion: '1.37.2',
          fetchResult: UpdateInfo(
            version: '1.38.0',
            releaseUrl: 'https://example.com/release',
            downloadUrl: 'https://example.com/download',
          ),
        );
        await s.svc.checkForUpdates();

        // The About section UI: latestDownloadUrl ?? latestReleaseUrl.
        final url = s.svc.latestDownloadUrl ?? s.svc.latestReleaseUrl;
        expect(url, 'https://example.com/download');
      },
    );

    test('fallback to releaseUrl when no download url', () async {
      final s = await _build(
        currentVersion: '1.37.2',
        fetchResult: UpdateInfo(
          version: '1.38.0',
          releaseUrl: 'https://example.com/release',
        ),
      );
      await s.svc.checkForUpdates();

      final url = s.svc.latestDownloadUrl ?? s.svc.latestReleaseUrl;
      expect(url, 'https://example.com/release');
    });
  });

  group('About section — error state', () {
    test('hasError true when check fails', () async {
      final s = await _build(fetchError: Exception('timeout'));
      await s.svc.checkForUpdates();

      expect(s.svc.hasError, isTrue);
      expect(s.svc.errorMessage, contains('timeout'));
      expect(s.svc.isChecking, isFalse);
    });
  });

  group('About section — up-to-date state', () {
    test(
      'updateAvailable false and latestVersion set when already current',
      () async {
        final s = await _build(
          currentVersion: '1.38.0',
          fetchResult: UpdateInfo(
            version: '1.38.0',
            releaseUrl: 'https://example.com',
          ),
        );
        await s.svc.checkForUpdates();

        expect(s.svc.updateAvailable, isFalse);
        // latestVersion is set (enables "Check again" vs "Check for updates").
        expect(s.svc.latestVersion, '1.38.0');
      },
    );
  });
}
