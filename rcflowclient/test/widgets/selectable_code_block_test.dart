import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:rcflowclient/ui/utils/selectable_code_block_builder.dart';

void main() {
  group('SelectableCodeBlockBuilder', () {
    Widget buildApp(Widget child) {
      return MaterialApp(home: Scaffold(body: child));
    }

    testWidgets('renders code block text without SingleChildScrollView', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        buildApp(
          SelectionArea(
            child: MarkdownBody(
              data: '```\nhello world\n```',
              shrinkWrap: true,
              builders: {
                'pre': SelectableCodeBlockBuilder(
                  textStyle: const TextStyle(fontFamily: 'monospace'),
                ),
              },
            ),
          ),
        ),
      );

      // The code text must be present.
      expect(find.text('hello world'), findsOneWidget);

      // No SingleChildScrollView should exist (the default renderer wraps
      // code blocks in one for horizontal scrolling, but our builder avoids
      // that to preserve SelectionArea compatibility).
      expect(find.byType(SingleChildScrollView), findsNothing);
    });

    testWidgets('code block text participates in SelectionArea', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        buildApp(
          SelectionArea(
            child: MarkdownBody(
              data: '```\nselectable code\n```',
              shrinkWrap: true,
              builders: {
                'pre': SelectableCodeBlockBuilder(
                  textStyle: const TextStyle(fontFamily: 'monospace'),
                ),
              },
            ),
          ),
        ),
      );

      // The text widget exists and is a descendant of SelectionArea.
      final textFinder = find.text('selectable code');
      expect(textFinder, findsOneWidget);

      // It should be a Text widget (not SelectableText), relying on the
      // parent SelectionArea for selection.
      final widget = tester.widget(textFinder);
      expect(widget, isA<Text>());
    });

    testWidgets('multiline code block renders all lines', (
      WidgetTester tester,
    ) async {
      await tester.pumpWidget(
        buildApp(
          SelectionArea(
            child: MarkdownBody(
              data: '```\nline one\nline two\nline three\n```',
              shrinkWrap: true,
              builders: {
                'pre': SelectableCodeBlockBuilder(
                  textStyle: const TextStyle(fontFamily: 'monospace'),
                ),
              },
            ),
          ),
        ),
      );

      // All lines should be in a single Text widget joined by newlines.
      expect(
        find.textContaining('line one\nline two\nline three'),
        findsOneWidget,
      );
    });

    testWidgets(
      'without builder, code block uses SingleChildScrollView (baseline)',
      (WidgetTester tester) async {
        await tester.pumpWidget(
          buildApp(
            SelectionArea(
              child: MarkdownBody(
                data: '```\nhello\n```',
                shrinkWrap: true,
                // No custom builder — default behaviour.
              ),
            ),
          ),
        );

        // The default renderer wraps code blocks in a horizontal
        // SingleChildScrollView for wide code lines.
        expect(find.byType(SingleChildScrollView), findsOneWidget);
      },
    );
  });
}
