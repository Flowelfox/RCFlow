/// Unit tests for the four auto-update keys added to [SettingsService]:
///   currentVersion, lastUpdateCheck, cachedLatestVersion,
///   dismissedUpdateVersion.
///
/// Each key is exercised for the three behaviours that matter:
///   1. returns null before any value is written,
///   2. round-trips a written value correctly, and
///   3. writing null removes the key so it reads back as null.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/services/settings_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

Future<SettingsService> _buildSettings([
  Map<String, Object> initial = const {},
]) async {
  SharedPreferences.setMockInitialValues(initial);
  final settings = SettingsService();
  await settings.init();
  return settings;
}

void main() {
  group('SettingsService.currentVersion', () {
    test('null before any value is written', () async {
      final s = await _buildSettings();
      expect(s.currentVersion, isNull);
    });

    test('round-trips a version string', () async {
      final s = await _buildSettings();
      s.currentVersion = '1.38.0';
      expect(s.currentVersion, '1.38.0');
    });

    test('null assignment removes the key', () async {
      final s = await _buildSettings({'rcflow_current_version': '1.38.0'});
      s.currentVersion = null;
      expect(s.currentVersion, isNull);
    });
  });

  group('SettingsService.lastUpdateCheck', () {
    test('null before any value is written', () async {
      final s = await _buildSettings();
      expect(s.lastUpdateCheck, isNull);
    });

    test('round-trips a UTC DateTime', () async {
      final s = await _buildSettings();
      final dt = DateTime.utc(2026, 4, 10, 12, 30, 0);
      s.lastUpdateCheck = dt;
      expect(s.lastUpdateCheck, dt);
    });

    test('null assignment removes the key', () async {
      final dt = DateTime.utc(2026, 4, 10, 12, 30, 0);
      final s = await _buildSettings({
        'rcflow_last_update_check': dt.toIso8601String(),
      });
      s.lastUpdateCheck = null;
      expect(s.lastUpdateCheck, isNull);
    });

    test('stored value survives a new SettingsService instance', () async {
      // Seed SharedPreferences with a pre-stored ISO-8601 string to verify
      // the parser works when the value was already present on init.
      final dt = DateTime.utc(2026, 1, 15, 8, 0, 0);
      final s = await _buildSettings({
        'rcflow_last_update_check': dt.toUtc().toIso8601String(),
      });
      expect(s.lastUpdateCheck, dt);
    });
  });

  group('SettingsService.cachedLatestVersion', () {
    test('null before any value is written', () async {
      final s = await _buildSettings();
      expect(s.cachedLatestVersion, isNull);
    });

    test('round-trips a version string', () async {
      final s = await _buildSettings();
      s.cachedLatestVersion = '1.39.0';
      expect(s.cachedLatestVersion, '1.39.0');
    });

    test('null assignment removes the key', () async {
      final s = await _buildSettings({'rcflow_cached_latest_version': '1.39.0'});
      s.cachedLatestVersion = null;
      expect(s.cachedLatestVersion, isNull);
    });
  });

  group('SettingsService.dismissedUpdateVersion', () {
    test('null before any value is written', () async {
      final s = await _buildSettings();
      expect(s.dismissedUpdateVersion, isNull);
    });

    test('round-trips a version string', () async {
      final s = await _buildSettings();
      s.dismissedUpdateVersion = '1.38.0';
      expect(s.dismissedUpdateVersion, '1.38.0');
    });

    test('null assignment removes the key', () async {
      final s = await _buildSettings({
        'rcflow_dismissed_update_version': '1.38.0',
      });
      s.dismissedUpdateVersion = null;
      expect(s.dismissedUpdateVersion, isNull);
    });
  });
}
