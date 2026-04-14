/// Widget tests for [SessionIdentityBar] — worker badge visibility and
/// interactivity for new-chat vs existing-session states.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:rcflowclient/services/settings_service.dart';
import 'package:rcflowclient/state/app_state.dart';
import 'package:rcflowclient/state/pane_state.dart';
import 'package:rcflowclient/theme.dart';
import 'package:rcflowclient/ui/widgets/session_identity_bar.dart';
import 'package:shared_preferences/shared_preferences.dart';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

Widget _buildBar({
  required AppState appState,
  required PaneState paneState,
}) {
  return MultiProvider(
    providers: [
      ChangeNotifierProvider<AppState>.value(value: appState),
      ChangeNotifierProvider<PaneState>.value(value: paneState),
    ],
    child: MaterialApp(
      theme: buildDarkTheme(),
      home: Scaffold(
        body: SingleChildScrollView(
          child: Column(children: const [SessionIdentityBar()]),
        ),
      ),
    ),
  );
}

Future<(AppState, PaneState)> _setupStates() async {
  SharedPreferences.setMockInitialValues({});
  final settings = SettingsService();
  await settings.init();
  final appState = AppState(settings: settings);
  final paneState = PaneState(paneId: 'test-pane', host: appState);
  return (appState, paneState);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  group('SessionIdentityBar — new-chat state', () {
    testWidgets('shows "New chat" text when no session exists', (
      tester,
    ) async {
      final (appState, paneState) = await _setupStates();

      await tester.pumpWidget(
        _buildBar(appState: appState, paneState: paneState),
      );
      await tester.pump();

      expect(find.text('New chat'), findsOneWidget);
      expect(find.text('send a message to start'), findsOneWidget);
    });

    testWidgets('no worker badge shown when no workers configured', (
      tester,
    ) async {
      final (appState, paneState) = await _setupStates();

      await tester.pumpWidget(
        _buildBar(appState: appState, paneState: paneState),
      );
      await tester.pump();

      // No worker badge should appear — no workers configured.
      expect(find.byIcon(Icons.dns_outlined), findsNothing);
    });
  });
}
