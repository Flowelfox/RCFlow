/// Widget tests for [DiffViewer]: selectable code, gutter range-drag callbacks,
/// and GitHub-style context-expansion gap bands.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/theme.dart';
import 'package:rcflowclient/ui/widgets/diff/diff_viewer.dart';

Widget _wrap(Widget child) => MaterialApp(
  theme: buildDarkTheme(),
  home: Scaffold(body: SingleChildScrollView(child: child)),
);

Widget _wrapNoScroll(Widget child) => MaterialApp(
  theme: buildDarkTheme(),
  home: Scaffold(body: child),
);

// A patch with two hunks separated by a context gap (new lines 5..19 hidden),
// and a leading gap (new lines 1..1 hidden before the first hunk at +2).
const _twoHunkDiff = '''--- a/lib/foo.dart
+++ b/lib/foo.dart
@@ -2,3 +2,3 @@
 line two
-old three
+new three
@@ -20,3 +20,3 @@
 line twenty
-old twentyone
+new twentyone''';

void main() {
  group('DiffViewer — tool-block path (no callbacks)', () {
    testWidgets('renders patch with no gap bands or expand affordances', (
      tester,
    ) async {
      await tester.pumpWidget(_wrap(const DiffViewer(diff: _twoHunkDiff)));
      await tester.pump();

      // Both hunk headers render.
      expect(find.text('@@ -2,3 +2,3 @@'), findsOneWidget);
      expect(find.text('@@ -20,3 +20,3 @@'), findsOneWidget);
      // No gap bands when onExpandContext is null.
      expect(find.textContaining('hidden line'), findsNothing);
      // Still wrapped in a SelectionArea so code is selectable.
      expect(find.byType(SelectionArea), findsOneWidget);
    });
  });

  group('DiffViewer — gap detection + expansion', () {
    testWidgets('shows a between-hunk gap band with the hidden-line count', (
      tester,
    ) async {
      await tester.pumpWidget(
        _wrap(
          DiffViewer(
            diff: _twoHunkDiff,
            onAddComment: (_, _) {},
            onExpandContext: (side, start, end) async =>
                List.generate(end - start + 1, (i) => 'ctx ${start + i}'),
          ),
        ),
      );
      await tester.pump();

      // Between hunk 1 (new 2..4) and hunk 2 (new 20..) → new 5..19 = 15 lines.
      expect(find.textContaining('15 hidden lines'), findsOneWidget);
    });

    testWidgets('expanding a small gap reveals its context lines', (
      tester,
    ) async {
      var fetchCalls = 0;
      await tester.pumpWidget(
        _wrap(
          DiffViewer(
            diff: _twoHunkDiff,
            onAddComment: (_, _) {},
            onExpandContext: (side, start, end) async {
              fetchCalls++;
              return List.generate(end - start + 1, (i) => 'CTX${start + i}');
            },
          ),
        ),
      );
      await tester.pump();

      // The leading gap (new line 1 only, before hunk at +2) is a single hidden
      // line → "1 hidden line" + a one-click unfold affordance.
      expect(find.textContaining('1 hidden line'), findsOneWidget);

      // Tap the first unfold (small-gap) button to reveal it.
      await tester.tap(find.byIcon(Icons.unfold_more).first);
      await tester.pump(); // start fetch
      await tester.pump(); // resolve future + setState

      expect(fetchCalls, greaterThan(0));
      expect(find.textContaining('CTX1'), findsOneWidget);
    });
  });

  group('DiffViewer — gutter drag handshake', () {
    testWidgets(
      'pointer down/up on a gutter fires the drag start/end callbacks '
      'and a single-line onAddComment for a plain click',
      (tester) async {
        var dragStarts = 0;
        var dragEnds = 0;
        int? gotLine;
        String? gotSide;

        await tester.pumpWidget(
          _wrapNoScroll(
            DiffViewer(
              diff: _twoHunkDiff,
              onAddComment: (line, side) {
                gotLine = line;
                gotSide = side;
              },
              onGutterDragStart: () => dragStarts++,
              onGutterDragEnd: () => dragEnds++,
            ),
          ),
        );
        await tester.pump();

        // Find a gutter Listener — uniquely identified as the one wrapping a
        // MouseRegion with the cell cursor (set only on gutter cells).
        final cellRegion = find.byWidgetPredicate(
          (w) => w is MouseRegion && w.cursor == SystemMouseCursors.cell,
        );
        expect(cellRegion, findsWidgets);
        final listener = find
            .ancestor(of: cellRegion.first, matching: find.byType(Listener))
            .first;
        final center = tester.getCenter(listener);

        final gesture = await tester.startGesture(center);
        await tester.pump();
        expect(dragStarts, 1);

        // A tiny move keeps the pointer over the same gutter, then release.
        await gesture.moveBy(const Offset(0, 1));
        await tester.pump();
        await gesture.up();
        await tester.pump();
        expect(dragEnds, 1);

        // Plain click (no move) → single-line comment fired.
        expect(gotLine, isNotNull);
        expect(gotSide, anyOf('LEFT', 'RIGHT'));
      },
    );
  });
}
