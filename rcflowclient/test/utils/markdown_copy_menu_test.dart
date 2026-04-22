import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/ui/utils/markdown_copy_menu.dart';

void main() {
  group('markdownToPlainText', () {
    test('strips heading markers', () {
      expect(markdownToPlainText('# Title'), 'Title');
      expect(markdownToPlainText('## Sub'), 'Sub');
      expect(markdownToPlainText('### Sub-sub'), 'Sub-sub');
    });

    test('strips emphasis', () {
      expect(markdownToPlainText('**bold** and *italic*'), 'bold and italic');
      expect(markdownToPlainText('__bold__ and _em_'), 'bold and em');
    });

    test('strips inline code and code fences', () {
      expect(markdownToPlainText('use `foo()` now'), 'use foo() now');
      expect(
        markdownToPlainText('```dart\nvar x = 1;\n```'),
        contains('var x = 1;'),
      );
    });

    test('preserves link text, drops URL', () {
      expect(
        markdownToPlainText('See [the docs](https://example.com) here'),
        'See the docs here',
      );
    });

    test('renders bullet lists with `- ` prefix', () {
      final plain = markdownToPlainText('- one\n- two\n- three');
      expect(plain, contains('- one'));
      expect(plain, contains('- two'));
      expect(plain, contains('- three'));
    });

    test('collapses multi-paragraph document', () {
      final input = '## Task\n\nDo the thing.\n\n## Description\n\nMore text.';
      final plain = markdownToPlainText(input);
      expect(plain, contains('Task'));
      expect(plain, contains('Do the thing.'));
      expect(plain, contains('Description'));
      expect(plain, isNot(contains('##')));
    });

    test('preserves plain text unchanged when no markdown syntax', () {
      expect(markdownToPlainText('just plain text'), 'just plain text');
    });
  });
}
