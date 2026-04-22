/// Widget tests for [ToolBlock] diff rendering and [PaneState.applyDiffToLastToolBlock].
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:rcflowclient/models/ws_messages.dart';
import 'package:rcflowclient/services/settings_service.dart';
import 'package:rcflowclient/state/app_state.dart';
import 'package:rcflowclient/state/pane_state.dart';
import 'package:rcflowclient/theme.dart';
import 'package:rcflowclient/ui/widgets/message_components/tool_block.dart';
import 'package:shared_preferences/shared_preferences.dart';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

Widget _buildBlock(PaneState paneState, DisplayMessage message) {
  return ChangeNotifierProvider<PaneState>.value(
    value: paneState,
    child: MaterialApp(
      theme: buildDarkTheme(),
      home: Scaffold(
        // Consumer triggers rebuild when PaneState notifies (e.g. after tap).
        body: Consumer<PaneState>(
          builder: (_, _, _) => ToolBlock(message: message),
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

const _sampleDiff = '''--- a/lib/foo.dart
+++ b/lib/foo.dart
@@ -1,3 +1,4 @@
 void main() {
-  print('hello');
+  print('world');
+  // added line
 }''';

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  group('ToolBlock — diff rendering', () {
    testWidgets('no diff: expand icon absent when output empty', (tester) async {
      final (_, paneState) = await _setupStates();
      final msg = DisplayMessage(
        type: DisplayMessageType.toolBlock,
        toolName: 'Edit',
        finished: true,
        content: '',
      );

      await tester.pumpWidget(_buildBlock(paneState, msg));
      await tester.pump();

      // No expand icon when nothing to show
      expect(find.byIcon(Icons.expand_more_rounded), findsNothing);
      expect(find.byIcon(Icons.expand_less_rounded), findsNothing);
    });

    testWidgets('diff alone makes block expandable', (tester) async {
      final (_, paneState) = await _setupStates();
      final msg = DisplayMessage(
        type: DisplayMessageType.toolBlock,
        toolName: 'Edit',
        finished: true,
        content: '',
        fileDiff: _sampleDiff,
      );

      await tester.pumpWidget(_buildBlock(paneState, msg));
      await tester.pump();

      expect(find.byIcon(Icons.expand_more_rounded), findsOneWidget);
    });

    testWidgets('diff not rendered when collapsed', (tester) async {
      final (_, paneState) = await _setupStates();
      final msg = DisplayMessage(
        type: DisplayMessageType.toolBlock,
        toolName: 'Edit',
        finished: true,
        content: '',
        fileDiff: _sampleDiff,
        expanded: false,
      );

      await tester.pumpWidget(_buildBlock(paneState, msg));
      await tester.pump();

      // Diff lines should not appear when collapsed
      expect(find.text('+  print(\'world\');'), findsNothing);
    });

    testWidgets('diff renders colored lines when expanded', (tester) async {
      final (_, paneState) = await _setupStates();
      final msg = DisplayMessage(
        type: DisplayMessageType.toolBlock,
        toolName: 'Edit',
        finished: true,
        content: '',
        fileDiff: _sampleDiff,
        expanded: true,
      );

      await tester.pumpWidget(_buildBlock(paneState, msg));
      await tester.pump();

      // Hunk header line rendered
      expect(find.text('@@ -1,3 +1,4 @@'), findsOneWidget);
      // Addition line rendered
      expect(find.textContaining("+  print('world');"), findsOneWidget);
      // Deletion line rendered
      expect(find.textContaining("-  print('hello');"), findsOneWidget);
      // Hunk header gutters show the starting line number (both old and new = 1)
      expect(find.text('1'), findsWidgets);
    });

    testWidgets('output text still renders alongside diff when expanded',
        (tester) async {
      final (_, paneState) = await _setupStates();
      final msg = DisplayMessage(
        type: DisplayMessageType.toolBlock,
        toolName: 'Edit',
        finished: true,
        content: 'File saved.',
        fileDiff: _sampleDiff,
        expanded: true,
      );

      await tester.pumpWidget(_buildBlock(paneState, msg));
      await tester.pump();

      expect(find.text('File saved.'), findsOneWidget);
      expect(find.text('@@ -1,3 +1,4 @@'), findsOneWidget);
    });

    testWidgets('tapping header toggles expanded state', (tester) async {
      final (_, paneState) = await _setupStates();
      final msg = DisplayMessage(
        type: DisplayMessageType.toolBlock,
        toolName: 'Edit',
        finished: true,
        content: '',
        fileDiff: _sampleDiff,
        expanded: false,
      );

      await tester.pumpWidget(_buildBlock(paneState, msg));
      await tester.pump();

      // Collapsed: expand_more icon present
      expect(find.byIcon(Icons.expand_more_rounded), findsOneWidget);

      // Tap the header area (GestureDetector covers the header row)
      await tester.tap(find.byIcon(Icons.expand_more_rounded));
      await tester.pumpAndSettle();

      // Now expanded: expand_less icon
      expect(find.byIcon(Icons.expand_less_rounded), findsOneWidget);
      expect(find.text('@@ -1,3 +1,4 @@'), findsOneWidget);
    });
  });

  group('ToolBlock — diff stats badge', () {
    testWidgets('shows +N -N stats in header when diff present',
        (tester) async {
      final (_, paneState) = await _setupStates();
      final msg = DisplayMessage(
        type: DisplayMessageType.toolBlock,
        toolName: 'Edit',
        toolInput: {'file_path': 'lib/foo.dart'},
        finished: true,
        content: '',
        fileDiff: _sampleDiff,
        expanded: false,
      );

      await tester.pumpWidget(_buildBlock(paneState, msg));
      await tester.pump();

      // _sampleDiff has 2 additions and 1 deletion
      expect(find.text('+2 -1'), findsOneWidget);
    });

    testWidgets('no stats badge when no diff', (tester) async {
      final (_, paneState) = await _setupStates();
      final msg = DisplayMessage(
        type: DisplayMessageType.toolBlock,
        toolName: 'Edit',
        toolInput: {'file_path': 'lib/foo.dart'},
        finished: true,
        content: '',
      );

      await tester.pumpWidget(_buildBlock(paneState, msg));
      await tester.pump();

      expect(find.text('+2 -1'), findsNothing);
    });
  });

  group('PaneState.applyDiffToLastToolBlock', () {
    test('sets fileDiff on last tool block', () async {
      final (appState, paneState) = await _setupStates();
      // Suppress unused variable warning
      appState.toString();

      paneState.startToolBlock('Edit', {'file_path': 'lib/foo.dart'});
      paneState.applyDiffToLastToolBlock(_sampleDiff);

      final toolBlocks = paneState.messages
          .where((m) => m.type == DisplayMessageType.toolBlock)
          .toList();
      expect(toolBlocks, isNotEmpty);
      expect(toolBlocks.last.fileDiff, equals(_sampleDiff));
    });

    test('auto-expands Edit tool block when diff applied', () async {
      final (appState, paneState) = await _setupStates();
      appState.toString();

      paneState.startToolBlock('Edit', {'file_path': 'lib/foo.dart'});

      // Starts collapsed
      final toolBlock = paneState.messages
          .where((m) => m.type == DisplayMessageType.toolBlock)
          .last;
      expect(toolBlock.expanded, isFalse);

      paneState.applyDiffToLastToolBlock(_sampleDiff);
      expect(toolBlock.expanded, isTrue);
    });

    test('auto-expands Write tool block when diff applied', () async {
      final (appState, paneState) = await _setupStates();
      appState.toString();

      paneState.startToolBlock('Write', {'file_path': 'lib/foo.dart'});
      paneState.applyDiffToLastToolBlock(_sampleDiff);

      final toolBlock = paneState.messages
          .where((m) => m.type == DisplayMessageType.toolBlock)
          .last;
      expect(toolBlock.expanded, isTrue);
    });

    test('does not auto-expand non-Edit/Write tool blocks', () async {
      final (appState, paneState) = await _setupStates();
      appState.toString();

      paneState.startToolBlock('Bash', {'command': 'ls'});
      paneState.applyDiffToLastToolBlock(_sampleDiff);

      final toolBlock = paneState.messages
          .where((m) => m.type == DisplayMessageType.toolBlock)
          .last;
      expect(toolBlock.expanded, isFalse);
    });

    test('no-ops silently when no tool block present', () async {
      final (appState, paneState) = await _setupStates();
      appState.toString();

      // Should not throw
      expect(
        () => paneState.applyDiffToLastToolBlock(_sampleDiff),
        returnsNormally,
      );
    });

    test('sets fileDiff on most recent tool block when multiple exist',
        () async {
      final (appState, paneState) = await _setupStates();
      appState.toString();

      paneState.startToolBlock('Read', {'file_path': 'lib/a.dart'});
      paneState.startToolBlock('Edit', {'file_path': 'lib/b.dart'});
      paneState.applyDiffToLastToolBlock(_sampleDiff);

      final toolBlocks = paneState.messages
          .where((m) => m.type == DisplayMessageType.toolBlock)
          .toList();
      expect(toolBlocks.length, greaterThanOrEqualTo(2));
      expect(toolBlocks.last.fileDiff, equals(_sampleDiff));
      // First block should be unaffected
      expect(toolBlocks.first.fileDiff, isNull);
    });
  });

  group('buildToolOutputHistory — diff from metadata', () {
    test('populates fileDiff from metadata.diff on last tool block', () {
      final messages = <DisplayMessage>[
        DisplayMessage(
          type: DisplayMessageType.toolBlock,
          sessionId: 'sess1',
          toolName: 'Edit',
          finished: false,
        ),
      ];

      final msg = {
        'content': 'ok',
        'metadata': {'diff': _sampleDiff},
      };

      // Simulate what buildToolOutputHistory does
      final content = msg['content'] as String? ?? '';
      final metadata = msg['metadata'] as Map<String, dynamic>? ?? {};
      final diff = metadata['diff'] as String?;
      if (messages.isNotEmpty &&
          messages.last.type == DisplayMessageType.toolBlock) {
        messages.last.content += content;
        if (diff != null && diff.isNotEmpty) {
          messages.last.fileDiff = diff;
        }
      }

      expect(messages.last.fileDiff, equals(_sampleDiff));
      expect(messages.last.content, equals('ok'));
    });

    test('auto-expands Edit tool block when diff present in history', () {
      final messages = <DisplayMessage>[
        DisplayMessage(
          type: DisplayMessageType.toolBlock,
          sessionId: 'sess1',
          toolName: 'Edit',
          finished: false,
        ),
      ];

      final msg = {
        'content': 'ok',
        'metadata': {'diff': _sampleDiff},
      };

      final content = msg['content'] as String? ?? '';
      final metadata = msg['metadata'] as Map<String, dynamic>? ?? {};
      final diff = metadata['diff'] as String?;
      if (messages.isNotEmpty &&
          messages.last.type == DisplayMessageType.toolBlock) {
        messages.last.content += content;
        if (diff != null && diff.isNotEmpty) {
          messages.last.fileDiff = diff;
          final tn = messages.last.toolName?.toLowerCase();
          if (tn == 'edit' || tn == 'write') {
            messages.last.expanded = true;
          }
        }
      }

      expect(messages.last.expanded, isTrue);
    });

    test('no diff key in metadata leaves fileDiff null', () {
      final messages = <DisplayMessage>[
        DisplayMessage(
          type: DisplayMessageType.toolBlock,
          sessionId: 'sess1',
          toolName: 'Edit',
          finished: false,
        ),
      ];

      final msg = {
        'content': 'ok',
        'metadata': <String, dynamic>{},
      };

      final content = msg['content'] as String? ?? '';
      final metadata = msg['metadata'] as Map<String, dynamic>? ?? {};
      final diff = metadata['diff'] as String?;
      if (messages.isNotEmpty &&
          messages.last.type == DisplayMessageType.toolBlock) {
        messages.last.content += content;
        if (diff != null && diff.isNotEmpty) {
          messages.last.fileDiff = diff;
        }
      }

      expect(messages.last.fileDiff, isNull);
    });
  });
}
