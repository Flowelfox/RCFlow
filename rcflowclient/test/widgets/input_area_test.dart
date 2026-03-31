/// Widget tests for [InputArea], focused on the subprocess status bar.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:rcflowclient/services/settings_service.dart';
import 'package:rcflowclient/state/app_state.dart';
import 'package:rcflowclient/state/pane_state.dart';
import 'package:rcflowclient/theme.dart';
import 'package:rcflowclient/ui/widgets/input_area.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../utils/subprocess_info_factory.dart';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Wraps [InputArea] in the providers and theme it needs.
Widget _buildInputArea({
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
      home: const Scaffold(
        body: Column(
          mainAxisAlignment: MainAxisAlignment.end,
          children: [InputArea()],
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
  group('InputArea — restore button', () {
    testWidgets('shows Restore button instead of Send when session ended', (
      tester,
    ) async {
      final (appState, paneState) = await _setupStates();

      // Simulate a session ending: set the session ID then mark it ended.
      paneState.handleAck('sess-1');
      paneState.handleSessionEnded('sess-1');

      await tester.pumpWidget(
        _buildInputArea(appState: appState, paneState: paneState),
      );
      await tester.pump();

      expect(find.byTooltip('Restore session'), findsOneWidget);
      expect(find.byIcon(Icons.restore_rounded), findsOneWidget);
      // Send icon should not be visible
      expect(find.byIcon(Icons.arrow_upward_rounded), findsNothing);
    });

    testWidgets('does not show Restore button when session is active', (
      tester,
    ) async {
      final (appState, paneState) = await _setupStates();

      await tester.pumpWidget(
        _buildInputArea(appState: appState, paneState: paneState),
      );
      await tester.pump();

      expect(find.byTooltip('Restore session'), findsNothing);
      expect(find.byIcon(Icons.arrow_upward_rounded), findsOneWidget);
    });

    testWidgets('Restore button disappears after session is restored', (
      tester,
    ) async {
      final (appState, paneState) = await _setupStates();

      paneState.handleAck('sess-2');
      paneState.handleSessionEnded('sess-2');

      await tester.pumpWidget(
        _buildInputArea(appState: appState, paneState: paneState),
      );
      await tester.pump();
      expect(find.byTooltip('Restore session'), findsOneWidget);

      paneState.handleSessionRestored('sess-2');
      await tester.pump();

      expect(find.byTooltip('Restore session'), findsNothing);
      expect(find.byIcon(Icons.arrow_upward_rounded), findsOneWidget);
    });
  });

  group('InputArea — subprocess status bar', () {
    testWidgets('status bar is hidden when runningSubprocess is null', (
      tester,
    ) async {
      final (appState, paneState) = await _setupStates();

      await tester.pumpWidget(
        _buildInputArea(appState: appState, paneState: paneState),
      );
      await tester.pump();

      // No subprocess — the display name should not be visible
      expect(find.text('Claude Code'), findsNothing);
      expect(find.byTooltip('Kill subprocess'), findsNothing);
    });

    testWidgets('status bar appears when runningSubprocess is set', (
      tester,
    ) async {
      final (appState, paneState) = await _setupStates();

      await tester.pumpWidget(
        _buildInputArea(appState: appState, paneState: paneState),
      );
      await tester.pump();

      paneState.setRunningSubprocess(makeSubprocessInfo());
      await tester.pump();

      expect(find.text('Claude Code'), findsOneWidget);
      expect(find.byTooltip('Kill subprocess'), findsOneWidget);
    });

    testWidgets('status bar shows tool name when currentTool is set', (
      tester,
    ) async {
      final (appState, paneState) = await _setupStates();

      await tester.pumpWidget(
        _buildInputArea(appState: appState, paneState: paneState),
      );
      await tester.pump();

      paneState.setRunningSubprocess(makeSubprocessInfo(currentTool: 'Bash'));
      await tester.pump();

      // label becomes 'Claude Code · Bash'
      expect(find.text('Claude Code · Bash'), findsOneWidget);
    });

    testWidgets('status bar disappears when runningSubprocess is cleared', (
      tester,
    ) async {
      final (appState, paneState) = await _setupStates();

      await tester.pumpWidget(
        _buildInputArea(appState: appState, paneState: paneState),
      );

      paneState.setRunningSubprocess(makeSubprocessInfo());
      await tester.pump();
      expect(find.text('Claude Code'), findsOneWidget);

      paneState.setRunningSubprocess(null);
      await tester.pump();

      expect(find.text('Claude Code'), findsNothing);
    });

    testWidgets('tapping kill button invokes interruptSubprocess (no crash)', (
      tester,
    ) async {
      final (appState, paneState) = await _setupStates();

      await tester.pumpWidget(
        _buildInputArea(appState: appState, paneState: paneState),
      );

      paneState.setRunningSubprocess(makeSubprocessInfo());
      await tester.pump();

      // Tap the kill button — PaneState.interruptSubprocess is a no-op when
      // the session ID is null (no session attached in this test), so it
      // should not throw.
      await tester.tap(find.byTooltip('Kill subprocess'));
      await tester.pump();

      // Advance past the 5-second auto-reset timer in _SubprocessStatusBarState.
      await tester.pump(const Duration(seconds: 6));

      // No exception — test passes
    });

    testWidgets('shows Codex display name for Codex subprocess', (
      tester,
    ) async {
      final (appState, paneState) = await _setupStates();

      await tester.pumpWidget(
        _buildInputArea(appState: appState, paneState: paneState),
      );
      await tester.pump();

      paneState.setRunningSubprocess(makeCodexSubprocessInfo());
      await tester.pump();

      expect(find.text('Codex'), findsOneWidget);
    });
  });
}
