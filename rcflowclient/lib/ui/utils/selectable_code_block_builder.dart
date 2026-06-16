import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:markdown/markdown.dart' as md;
import '../../theme/spacing.dart';
import '../widgets/copy_icon_button.dart';
import '../widgets/message_components/assistant_bubble.dart'
    show messageActionsAlwaysVisible;
import 'markdown_copy_menu.dart';

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
    return _CodeBlockView(code: code, style: preferredStyle ?? textStyle);
  }
}

/// Code-block body with a hover-revealed copy button (top-right) that copies the
/// code verbatim. Kept as a [Text.rich] so the surrounding [SelectionArea] can
/// still select inside the block.
class _CodeBlockView extends StatefulWidget {
  final String code;
  final TextStyle? style;

  const _CodeBlockView({required this.code, required this.style});

  @override
  State<_CodeBlockView> createState() => _CodeBlockViewState();
}

class _CodeBlockViewState extends State<_CodeBlockView> {
  bool _hovered = false;

  @override
  Widget build(BuildContext context) {
    final showCopy = _hovered || messageActionsAlwaysVisible(context);
    return MouseRegion(
      onEnter: (_) => setState(() => _hovered = true),
      onExit: (_) => setState(() => _hovered = false),
      child: Stack(
        children: [
          Padding(
            padding: const EdgeInsets.all(kSpace3),
            child: Text.rich(TextSpan(style: widget.style, text: widget.code)),
          ),
          if (showCopy)
            Positioned(
              top: 2,
              right: 2,
              child: CopyIconButton(
                tooltip: 'Copy code',
                iconSize: 13,
                onCopy: () => writeRichClipboard(
                  html: markdownSourceToHtml('```\n${widget.code}\n```'),
                  plain: widget.code,
                ),
              ),
            ),
        ],
      ),
    );
  }
}
