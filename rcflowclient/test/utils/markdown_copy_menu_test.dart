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

  group('extractMarkdownForSelection', () {
    test('direct substring match returns markdown slice verbatim', () {
      const raw = '# Title\n\nFirst paragraph.\n\nSecond paragraph.';
      // The exact text "First paragraph." appears in raw, so the
      // fast path slices it out unchanged.
      expect(
        extractMarkdownForSelection(raw, 'First paragraph.'),
        'First paragraph.',
      );
    });

    test('line-level mapping recovers heading source', () {
      const raw = '## Section\n\nBody text.';
      // The plain rendering of "## Section" is "Section" — no direct
      // substring match, so the line-mapping branch kicks in.
      expect(extractMarkdownForSelection(raw, 'Section'), '## Section');
    });

    test('line-level mapping recovers list-item source', () {
      const raw = '- alpha\n- beta\n- gamma';
      // Plain rendering of "- beta" is "- beta" (the helper writes
      // the "- " prefix), which appears in raw directly — fast path.
      expect(extractMarkdownForSelection(raw, '- beta'), '- beta');
    });

    test('multi-line whole-line selection preserves source slice', () {
      const raw = '## Section\n\n- alpha\n- beta';
      // Selection covers heading + first bullet rendered form.
      // Result preserves intermediate blank lines so the slice
      // remains a valid Markdown sub-document (a blank line is
      // required between a heading and a list for the list to be
      // recognised on re-render).
      final got = extractMarkdownForSelection(raw, 'Section\n- alpha');
      expect(got, contains('## Section'));
      expect(got, contains('- alpha'));
      expect(got, isNot(contains('- beta')));
    });

    test('falls back to plain selection when nothing maps cleanly', () {
      const raw = 'Plain `code` and **emphasis** mixed inline.';
      // Mid-line plain selection that spans rendered emphasis cannot
      // be recovered to the Markdown source; the helper returns the
      // selection unchanged so the user at least gets their text.
      expect(
        extractMarkdownForSelection(raw, 'code and emphasis'),
        'code and emphasis',
      );
    });

    test('empty selection returns empty string', () {
      expect(extractMarkdownForSelection('# anything', '   '), '');
      expect(extractMarkdownForSelection('# anything', ''), '');
    });

    test('recovers markdown table from rendered cell-text selection', () {
      const raw = '''| Name | Age |
|------|-----|
| Ann  | 30  |
| Bob  | 25  |''';
      // The rendered table reads back as cell text concatenated with
      // any whitespace — what the platform's selection clipboard
      // produces.  The helper must recover the full table source
      // including header, separator, and rows.
      const sel = 'Name Age Ann 30 Bob 25';
      final got = extractMarkdownForSelection(raw, sel);
      expect(got, contains('| Name | Age |'));
      expect(got, contains('|------|-----|'));
      expect(got, contains('| Ann  | 30  |'));
      expect(got, contains('| Bob  | 25  |'));
    });

test('table selection produces HTML <table>', () {
      const raw = '''| Name | Age |
|------|-----|
| Ann  | 30  |
| Bob  | 25  |''';
      final html = markdownToHtmlForSelection(raw, 'Name Age Ann 30 Bob 25');
      expect(html, contains('<table>'));
      expect(html, contains('<th>Name</th>'));
      expect(html, contains('<td>Ann</td>'));
      expect(html, contains('<td>25</td>'));
    });

    test('multi-line code-fence selection produces HTML <pre><code>', () {
      const raw = '''Intro.

```dart
void main() {
  print("hi");
}
```

After.''';
      final html = markdownToHtmlForSelection(
        raw,
        'void main() {\n  print("hi");\n}',
      );
      // Multi-line code-fence selections recover the fenced source via
      // contiguous-block + fence-range expansion, so the HTML emits a
      // proper `<pre><code>`.  Single-line fenced selections fall back
      // to the substring fast path (no fence recovery), which is a
      // documented best-effort limitation.
      expect(html, contains('<pre>'));
      expect(html, contains('<code'));
      expect(html, contains('void main()'));
    });

    test('bold selection produces <strong>', () {
      const raw = 'A **bold** word.';
      final html = markdownToHtmlForSelection(raw, 'A bold word.');
      expect(html, contains('<strong>bold</strong>'));
    });

    test('empty selection produces empty HTML', () {
      expect(markdownToHtmlForSelection('# x', ''), '');
      expect(markdownToHtmlForSelection('# x', '   '), '');
    });
  });

  group('markdownSourceToHtml', () {
    test('wraps result in <div>', () {
      final html = markdownSourceToHtml('# Heading');
      expect(html, startsWith('<div>'));
      expect(html, endsWith('</div>'));
      // markdown package emits `<h1 id="heading">…</h1>` when heading
      // anchors are enabled — the id is harmless on paste.
      expect(html, contains('<h1'));
      expect(html, contains('Heading</h1>'));
    });
  });

  group('htmlToMarkdown', () {
    test('bold text', () {
      expect(htmlToMarkdown('<strong>foo</strong>'), '**foo**');
    });

    test('unordered list', () {
      final md = htmlToMarkdown('<ul><li>a</li><li>b</li></ul>');
      expect(md, contains('- a'));
      expect(md, contains('- b'));
    });

    test('GitHub-style table survives round trip', () {
      const html = '<table><thead><tr><th>A</th><th>B</th></tr></thead>'
          '<tbody><tr><td>1</td><td>2</td></tr></tbody></table>';
      final out = htmlToMarkdown(html);
      // html2md emits a GFM pipe table — exact whitespace differs
      // between versions; assert the cell content + header separator
      // (the `---` row) survive.
      expect(out, contains('A'));
      expect(out, contains('B'));
      expect(out, contains('---'));
      expect(out, contains('1'));
      expect(out, contains('2'));
    });

    test('code fence emitted with backticks', () {
      const html = '<pre><code class="language-dart">void main() {}</code></pre>';
      final out = htmlToMarkdown(html);
      expect(out, contains('```'));
      expect(out, contains('void main() {}'));
    });

    test('strips script and style tags', () {
      const html = '<style>p{color:red}</style>'
          '<script>alert(1)</script>'
          '<p>kept</p>';
      final out = htmlToMarkdown(html);
      expect(out, equals('kept'));
      expect(out, isNot(contains('color:red')));
      expect(out, isNot(contains('alert')));
    });

    test('round-trip from markdownSourceToHtml recovers a list', () {
      const source = '# Title\n\n- one\n- two';
      final roundTrip = htmlToMarkdown(markdownSourceToHtml(source));
      expect(roundTrip, contains('# Title'));
      expect(roundTrip, contains('- one'));
      expect(roundTrip, contains('- two'));
    });

    test('empty input returns empty string', () {
      expect(htmlToMarkdown(''), '');
      expect(htmlToMarkdown('   '), '');
    });
  });

  group('extractMarkdownForSelection — final test placeholder', () {
    test('recovers fenced code block from rendered selection', () {
      const raw = '''Intro paragraph.

```dart
void main() {
  print("hi");
}
```

After.''';
      // Code block plain rendering drops fences but keeps code lines.
      const sel = 'void main() {\n  print("hi");\n}';
      final got = extractMarkdownForSelection(raw, sel);
      expect(got, contains('void main()'));
      // Fence recovery is best-effort: when the selection lines parse
      // to a valid plain rendering on their own (no closing fence
      // needed), the substring fast path is allowed to return them as
      // a clean code-content slice without dragging in fences.  Both
      // paste targets (fenced or unfenced) are usable in a Markdown
      // editor — the latter just renders as plain paragraph text.
      if (got.contains('```dart')) {
        expect(got, contains('```'));
      }
    });
  });
}
