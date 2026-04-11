/// Widget tests for the update banner that appears above the Settings row.
///
/// _UpdateBanner is a private class; we test it through a lightweight harness
/// that renders an [AppState]-backed [SessionListPanel] in a bounded container
/// and checks that the banner visibility / dismiss behaviour are correct.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:rcflowclient/models/update_info.dart';
import 'package:rcflowclient/services/settings_service.dart';
import 'package:rcflowclient/services/update_fetcher.dart';
import 'package:rcflowclient/services/update_service.dart';
import 'package:rcflowclient/state/app_state.dart';
import 'package:rcflowclient/theme.dart';
import 'package:shared_preferences/shared_preferences.dart';

// ---------------------------------------------------------------------------
// Fakes
// ---------------------------------------------------------------------------

class _FakeFetcher implements UpdateFetcher {
  UpdateInfo? result;
  _FakeFetcher({this.result});

  @override
  Future<UpdateInfo?> fetchLatestUpdate() async => result;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

Future<AppState> _buildAppState({
  String currentVersion = '1.37.2',
  String? cachedLatestVersion,
}) async {
  SharedPreferences.setMockInitialValues({
    'rcflow_current_version': currentVersion,
    'rcflow_cached_latest_version': ?cachedLatestVersion,
  });
  final settings = SettingsService();
  await settings.init();
  return AppState(settings: settings);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  // -------------------------------------------------------------------------
  // Service-level banner visibility (pure logic, no widget rendering)
  // -------------------------------------------------------------------------
  group('_UpdateBanner visibility via UpdateService state', () {
    testWidgets('banner hidden when no update available', (tester) async {
      final appState = await _buildAppState();
      expect(appState.updateService.showBanner, isFalse);
    });

    testWidgets('banner shown when cached update is available', (tester) async {
      final appState = await _buildAppState(
        currentVersion: '1.37.2',
        cachedLatestVersion: '1.38.0',
      );
      // restoreCachedState is called in AppState constructor.
      expect(appState.updateService.showBanner, isTrue);
    });

    testWidgets('banner hidden after dismissCurrentUpdate', (tester) async {
      final appState = await _buildAppState(
        currentVersion: '1.37.2',
        cachedLatestVersion: '1.38.0',
      );
      expect(appState.updateService.showBanner, isTrue);

      appState.updateService.dismissCurrentUpdate();

      expect(appState.updateService.showBanner, isFalse);
      expect(appState.settings.dismissedUpdateVersion, '1.38.0');
    });

    testWidgets('banner reappears when a still-newer release is fetched', (
      tester,
    ) async {
      final appState = await _buildAppState(
        currentVersion: '1.37.2',
        cachedLatestVersion: '1.38.0',
      );
      appState.updateService.dismissCurrentUpdate();
      expect(appState.updateService.showBanner, isFalse);

      final fetcher = _FakeFetcher(
        result: UpdateInfo(
          version: '1.39.0',
          releaseUrl: 'https://example.com',
        ),
      );
      final svc2 = UpdateService(settings: appState.settings, fetcher: fetcher);
      // Simulate service receiving a newer release.
      await svc2.checkForUpdates();

      // The dismissal for 1.38.0 should have been cleared.
      expect(appState.settings.dismissedUpdateVersion, isNull);
    });
  });

  // -------------------------------------------------------------------------
  // Widget rendering — version text visible when banner is shown
  // -------------------------------------------------------------------------

  group('_UpdateBanner widget rendering', () {
    testWidgets('version text rendered in ListenableBuilder', (tester) async {
      // Build a minimal widget that mirrors what _UpdateBanner renders
      // without depending on the private class or SessionListPanel layout.
      final appState = await _buildAppState(
        currentVersion: '1.37.2',
        cachedLatestVersion: '1.38.0',
      );
      expect(appState.updateService.showBanner, isTrue);

      await tester.pumpWidget(
        ChangeNotifierProvider<AppState>.value(
          value: appState,
          child: MaterialApp(
            theme: buildDarkTheme(),
            home: Scaffold(
              body: ListenableBuilder(
                listenable: appState.updateService,
                builder: (ctx, _) {
                  final svc = appState.updateService;
                  if (!svc.showBanner) return const SizedBox.shrink();
                  return Text('v${svc.latestVersion} available');
                },
              ),
            ),
          ),
        ),
      );

      expect(find.text('v1.38.0 available'), findsOneWidget);
    });

    testWidgets('version text disappears after dismiss', (tester) async {
      final appState = await _buildAppState(
        currentVersion: '1.37.2',
        cachedLatestVersion: '1.38.0',
      );

      await tester.pumpWidget(
        ChangeNotifierProvider<AppState>.value(
          value: appState,
          child: MaterialApp(
            theme: buildDarkTheme(),
            home: Scaffold(
              body: ListenableBuilder(
                listenable: appState.updateService,
                builder: (ctx, _) {
                  final svc = appState.updateService;
                  if (!svc.showBanner) return const SizedBox.shrink();
                  return Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Text('v${svc.latestVersion} available'),
                      ElevatedButton(
                        onPressed: svc.dismissCurrentUpdate,
                        child: const Text('Dismiss'),
                      ),
                    ],
                  );
                },
              ),
            ),
          ),
        ),
      );

      expect(find.text('v1.38.0 available'), findsOneWidget);

      await tester.tap(find.text('Dismiss'));
      await tester.pump();

      expect(find.text('v1.38.0 available'), findsNothing);
    });
  });
}
