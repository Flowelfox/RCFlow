import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:markdown/markdown.dart' as md;

/// Custom code-block builder that renders `<pre>` content as a plain [Text]
/// widget instead of wrapping it in a [SingleChildScrollView].
///
/// The default `flutter_markdown_plus` builder wraps code blocks in a
/// horizontal [SingleChildScrollView] + [Scrollbar].  That scrollable
/// intercepts pointer / drag events and prevents a parent [SelectionArea]
/// (or `selectable: true` on [MarkdownBody]) from selecting text inside
/// code blocks.
///
/// By returning a simple [Text.rich] here, the rendered code block
/// participates normally in Flutter's selection system while still
/// honouring the [MarkdownStyleSheet.code] text style and the
/// [MarkdownStyleSheet.codeblockDecoration] / [codeblockPadding] applied
/// by the library's `visitElementAfter` for the `pre` tag.
class SelectableCodeBlockBuilder extends MarkdownElementBuilder {
  SelectableCodeBlockBuilder({required this.textStyle});

  /// The [TextStyle] to apply to code-block text (typically
  /// [MarkdownStyleSheet.code]).
  final TextStyle? textStyle;

  @override
  bool isBlockElement() => false;

  @override
  Widget? visitText(md.Text text, TextStyle? preferredStyle) {
    // Strip trailing newline that the Markdown parser appends.
    final code = text.text.replaceAll(RegExp(r'\n$'), '');
    return Padding(
      padding: const EdgeInsets.all(12),
      child: Text.rich(
        TextSpan(style: preferredStyle ?? textStyle, text: code),
      ),
    );
  }
}
