/// Unit tests for [UpdateService].
///
/// All tests use a [FakeUpdateFetcher] so no network I/O occurs.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/update_info.dart';
import 'package:rcflowclient/services/settings_service.dart';
import 'package:rcflowclient/services/update_fetcher.dart';
import 'package:rcflowclient/services/update_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

class FakeUpdateFetcher implements UpdateFetcher {
  UpdateInfo? result;
  Object? error;
  int callCount = 0;

  FakeUpdateFetcher({this.result, this.error});

  @override
  Future<UpdateInfo?> fetchLatestUpdate() async {
    callCount++;
    if (error != null) throw error!;
    return result;
  }
}

Future<SettingsService> _buildSettings({
  String? currentVersion,
  String? cachedLatestVersion,
  DateTime? lastUpdateCheck,
  String? dismissedUpdateVersion,
}) async {
  SharedPreferences.setMockInitialValues({
    'rcflow_current_version': ?currentVersion,
    'rcflow_cached_latest_version': ?cachedLatestVersion,
    if (lastUpdateCheck != null)
      'rcflow_last_update_check': lastUpdateCheck.toUtc().toIso8601String(),
    'rcflow_dismissed_update_version': ?dismissedUpdateVersion,
  });
  final settings = SettingsService();
  await settings.init();
  return settings;
}

void main() {
  group('UpdateService.isNewer', () {
    test('1.38.0 is newer than 1.37.2', () {
      expect(UpdateService.isNewer('1.38.0', '1.37.2'), isTrue);
    });

    test('1.10.0 is newer than 1.9.0 (numeric comparison)', () {
      expect(UpdateService.isNewer('1.10.0', '1.9.0'), isTrue);
    });

    test('equal versions are not newer', () {
      expect(UpdateService.isNewer('1.38.0', '1.38.0'), isFalse);
    });

    test('older version is not newer', () {
      expect(UpdateService.isNewer('1.37.0', '1.38.0'), isFalse);
    });
  });

  group('restoreCachedState', () {
    test('sets latestVersion from cache when current < cached', () async {
      final settings = await _buildSettings(
        currentVersion: '1.37.2',
        cachedLatestVersion: '1.38.0',
      );
      final svc = UpdateService(
        settings: settings,
        fetcher: FakeUpdateFetcher(),
      );
      svc.restoreCachedState();

      expect(svc.latestVersion, '1.38.0');
      expect(svc.updateAvailable, isTrue);
    });

    test('clears stale cache when current >= cached', () async {
      final settings = await _buildSettings(
        currentVersion: '1.38.0',
        cachedLatestVersion: '1.38.0',
      );
      final svc = UpdateService(
        settings: settings,
        fetcher: FakeUpdateFetcher(),
      );
      svc.restoreCachedState();

      expect(svc.latestVersion, isNull);
      expect(settings.cachedLatestVersion, isNull);
    });

    test('clears stale cache and dismissal when current is newer', () async {
      final settings = await _buildSettings(
        currentVersion: '1.39.0',
        cachedLatestVersion: '1.38.0',
        dismissedUpdateVersion: '1.38.0',
      );
      final svc = UpdateService(
        settings: settings,
        fetcher: FakeUpdateFetcher(),
      );
      svc.restoreCachedState();

      expect(svc.latestVersion, isNull);
      expect(settings.cachedLatestVersion, isNull);
      expect(settings.dismissedUpdateVersion, isNull);
    });

    test('no-op when there is no cached version', () async {
      final settings = await _buildSettings(currentVersion: '1.37.2');
      final svc = UpdateService(
        settings: settings,
        fetcher: FakeUpdateFetcher(),
      );
      svc.restoreCachedState();

      expect(svc.latestVersion, isNull);
      expect(svc.updateAvailable, isFalse);
    });
  });

  // -------------------------------------------------------------------------
  // checkForUpdates
  // -------------------------------------------------------------------------

  group('checkForUpdates', () {
    test('sets latestVersion on success', () async {
      final settings = await _buildSettings(currentVersion: '1.37.2');
      final fetcher = FakeUpdateFetcher(
        result: UpdateInfo(
          version: '1.38.0',
          releaseUrl:
              'https://github.com/Flowelfox/RCFlow/releases/tag/v1.38.0',
        ),
      );
      final svc = UpdateService(settings: settings, fetcher: fetcher);

      await svc.checkForUpdates();

      expect(svc.latestVersion, '1.38.0');
      expect(svc.updateAvailable, isTrue);
      expect(svc.hasError, isFalse);
      expect(settings.cachedLatestVersion, '1.38.0');
    });

    test('sets errorMessage on failure', () async {
      final settings = await _buildSettings(currentVersion: '1.37.2');
      final fetcher = FakeUpdateFetcher(error: Exception('network failure'));
      final svc = UpdateService(settings: settings, fetcher: fetcher);

      await svc.checkForUpdates();

      expect(svc.hasError, isTrue);
      expect(svc.errorMessage, contains('network failure'));
      expect(svc.updateAvailable, isFalse);
    });

    test('clears previous error on retry', () async {
      final settings = await _buildSettings(currentVersion: '1.37.2');
      final fetcher = FakeUpdateFetcher(error: Exception('network failure'));
      final svc = UpdateService(settings: settings, fetcher: fetcher);

      await svc.checkForUpdates();
      expect(svc.hasError, isTrue);

      fetcher.error = null;
      fetcher.result = UpdateInfo(
        version: '1.38.0',
        releaseUrl: 'https://github.com/example',
      );
      await svc.checkForUpdates();

      expect(svc.hasError, isFalse);
      expect(svc.latestVersion, '1.38.0');
    });

    test('notifies listeners on completion', () async {
      final settings = await _buildSettings(currentVersion: '1.37.2');
      final fetcher = FakeUpdateFetcher(
        result: UpdateInfo(
          version: '1.38.0',
          releaseUrl: 'https://example.com',
        ),
      );
      final svc = UpdateService(settings: settings, fetcher: fetcher);
      var notified = 0;
      svc.addListener(() => notified++);

      await svc.checkForUpdates();

      // At least two notifications: one when isChecking→true, one when done.
      expect(notified, greaterThanOrEqualTo(2));
    });
  });

  // -------------------------------------------------------------------------
  // maybeCheck (TTL)
  // -------------------------------------------------------------------------

  group('maybeCheck', () {
    test('skips fetch when cache is fresh and result is cached', () async {
      final settings = await _buildSettings(
        currentVersion: '1.37.2',
        cachedLatestVersion: '1.38.0',
        lastUpdateCheck: DateTime.now().toUtc(),
      );
      final fetcher = FakeUpdateFetcher();
      final svc = UpdateService(settings: settings, fetcher: fetcher);
      svc.restoreCachedState();

      await svc.maybeCheck();

      expect(fetcher.callCount, 0);
    });

    test('fetches when last check was more than 24h ago', () async {
      final settings = await _buildSettings(
        currentVersion: '1.37.2',
        cachedLatestVersion: '1.38.0',
        lastUpdateCheck: DateTime.now().toUtc().subtract(
          const Duration(hours: 25),
        ),
      );
      final fetcher = FakeUpdateFetcher(
        result: UpdateInfo(
          version: '1.38.0',
          releaseUrl: 'https://example.com',
        ),
      );
      final svc = UpdateService(settings: settings, fetcher: fetcher);
      svc.restoreCachedState();

      await svc.maybeCheck();

      expect(fetcher.callCount, 1);
    });
  });

  // -------------------------------------------------------------------------
  // dismissCurrentUpdate
  // -------------------------------------------------------------------------

  group('checkForUpdates — null result', () {
    test('no-op when fetcher returns null (no update found)', () async {
      final settings = await _buildSettings(currentVersion: '1.37.2');
      final fetcher = FakeUpdateFetcher(); // result is null by default
      final svc = UpdateService(settings: settings, fetcher: fetcher);

      await svc.checkForUpdates();

      expect(svc.latestVersion, isNull);
      expect(svc.updateAvailable, isFalse);
      expect(svc.hasError, isFalse);
      expect(settings.cachedLatestVersion, isNull);
    });
  });

  group('checkForUpdates — concurrent guard', () {
    test('second call while first is in-flight is a no-op', () async {
      final settings = await _buildSettings(currentVersion: '1.37.2');
      final fetcher = FakeUpdateFetcher(
        result: UpdateInfo(
          version: '1.38.0',
          releaseUrl: 'https://example.com',
        ),
      );
      final svc = UpdateService(settings: settings, fetcher: fetcher);

      // Both calls are started without awaiting; the second hits the
      // _isChecking guard that the first set synchronously.
      final f1 = svc.checkForUpdates();
      final f2 = svc.checkForUpdates();
      await Future.wait([f1, f2]);

      expect(fetcher.callCount, 1);
    });
  });

  group('checkForUpdates — release URL / download URL', () {
    test('populates latestReleaseUrl and latestDownloadUrl on success', () async {
      final settings = await _buildSettings(currentVersion: '1.37.2');
      final fetcher = FakeUpdateFetcher(
        result: UpdateInfo(
          version: '1.38.0',
          releaseUrl:
              'https://github.com/Flowelfox/RCFlow/releases/tag/v1.38.0',
          downloadUrl: 'https://example.com/rcflow-v1.38.0-linux-client-amd64.deb',
        ),
      );
      final svc = UpdateService(settings: settings, fetcher: fetcher);

      await svc.checkForUpdates();

      expect(
        svc.latestReleaseUrl,
        'https://github.com/Flowelfox/RCFlow/releases/tag/v1.38.0',
      );
      expect(
        svc.latestDownloadUrl,
        'https://example.com/rcflow-v1.38.0-linux-client-amd64.deb',
      );
    });
  });

  group('updateAvailable — null currentVersion', () {
    test('returns false when currentVersion is null', () async {
      final settings = await _buildSettings(); // no currentVersion
      final fetcher = FakeUpdateFetcher(
        result: UpdateInfo(
          version: '1.38.0',
          releaseUrl: 'https://example.com',
        ),
      );
      final svc = UpdateService(settings: settings, fetcher: fetcher);

      await svc.checkForUpdates();

      expect(svc.latestVersion, '1.38.0');
      expect(svc.updateAvailable, isFalse);
    });
  });

  group('isDismissed', () {
    test('false when dismissedVersion differs from latestVersion', () async {
      final settings = await _buildSettings(
        currentVersion: '1.37.2',
        dismissedUpdateVersion: '1.37.0', // older, different version
      );
      final fetcher = FakeUpdateFetcher(
        result: UpdateInfo(
          version: '1.38.0',
          releaseUrl: 'https://example.com',
        ),
      );
      final svc = UpdateService(settings: settings, fetcher: fetcher);
      await svc.checkForUpdates();

      expect(svc.isDismissed, isFalse);
      expect(svc.showBanner, isTrue);
    });
  });

  group('dismissCurrentUpdate — null latestVersion', () {
    test('no-op when no update has been fetched', () async {
      final settings = await _buildSettings(currentVersion: '1.37.2');
      final svc = UpdateService(
        settings: settings,
        fetcher: FakeUpdateFetcher(),
      );

      // Should not throw and must not write a dismissedUpdateVersion.
      svc.dismissCurrentUpdate();

      expect(settings.dismissedUpdateVersion, isNull);
    });
  });

  group('maybeCheck — null lastUpdateCheck', () {
    test('fetches when lastUpdateCheck has never been set', () async {
      final settings = await _buildSettings(
        currentVersion: '1.37.2',
        // no lastUpdateCheck
      );
      final fetcher = FakeUpdateFetcher(
        result: UpdateInfo(
          version: '1.38.0',
          releaseUrl: 'https://example.com',
        ),
      );
      final svc = UpdateService(settings: settings, fetcher: fetcher);

      await svc.maybeCheck();

      expect(fetcher.callCount, 1);
      expect(svc.latestVersion, '1.38.0');
    });
  });

  group('restoreCachedState — null currentVersion', () {
    test('sets latestVersion from cache when currentVersion is null', () async {
      final settings = await _buildSettings(
        cachedLatestVersion: '1.38.0',
        // currentVersion intentionally omitted (null)
      );
      final svc = UpdateService(
        settings: settings,
        fetcher: FakeUpdateFetcher(),
      );
      svc.restoreCachedState();

      // Cache is read; currentVersion null skips the stale-check guard.
      expect(svc.latestVersion, '1.38.0');
      // updateAvailable is still false because currentVersion is null.
      expect(svc.updateAvailable, isFalse);
    });
  });

  group('dismissCurrentUpdate', () {
    test('hides banner after dismissal', () async {
      final settings = await _buildSettings(currentVersion: '1.37.2');
      final fetcher = FakeUpdateFetcher(
        result: UpdateInfo(
          version: '1.38.0',
          releaseUrl: 'https://example.com',
        ),
      );
      final svc = UpdateService(settings: settings, fetcher: fetcher);
      await svc.checkForUpdates();
      expect(svc.showBanner, isTrue);

      svc.dismissCurrentUpdate();

      expect(svc.isDismissed, isTrue);
      expect(svc.showBanner, isFalse);
      expect(settings.dismissedUpdateVersion, '1.38.0');
    });

    test('banner reappears for newer version after dismissal', () async {
      final settings = await _buildSettings(currentVersion: '1.37.2');
      final fetcher = FakeUpdateFetcher(
        result: UpdateInfo(
          version: '1.38.0',
          releaseUrl: 'https://example.com',
        ),
      );
      final svc = UpdateService(settings: settings, fetcher: fetcher);
      await svc.checkForUpdates();
      svc.dismissCurrentUpdate();
      expect(svc.showBanner, isFalse);

      // A newer release comes in.
      fetcher.result = UpdateInfo(
        version: '1.39.0',
        releaseUrl: 'https://example.com',
      );
      await svc.checkForUpdates();

      expect(svc.showBanner, isTrue);
      expect(settings.dismissedUpdateVersion, isNull);
    });
  });
}
