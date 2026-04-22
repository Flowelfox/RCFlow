import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';

import '../../../models/ws_messages.dart';
import '../../../theme.dart';
import '../../utils/link_utils.dart';
import '../../utils/markdown_copy_menu.dart';
import '../../utils/selectable_code_block_builder.dart';

class AssistantBubble extends StatefulWidget {
  final DisplayMessage message;
  const AssistantBubble({super.key, required this.message});

  @override
  State<AssistantBubble> createState() => _AssistantBubbleState();
}

class _AssistantBubbleState extends State<AssistantBubble> {
  // Cache the rendered MarkdownBody by content so finished bubbles further up
  // the transcript don't rebuild their full styleSheet + parse tree on every
  // 16 ms PaneState notify driven by the actively-streaming bubble below.
  // didChangeDependencies handles theme flips by clearing the cache.
  String? _cachedContent;
  Widget? _cachedBody;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _cachedContent = null;
    _cachedBody = null;
  }

  @override
  void didUpdateWidget(AssistantBubble oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.message, widget.message)) {
      _cachedContent = null;
      _cachedBody = null;
    }
  }

  @override
  Widget build(BuildContext context) {
    final content = widget.message.content;
    if (_cachedBody == null || _cachedContent != content) {
      _cachedContent = content;
      _cachedBody = _buildMarkdown(context, content);
    }
    return Padding(
      padding: const EdgeInsets.only(top: 4, bottom: 4, right: 32),
      child: MarkdownCopyMenu(rawMarkdown: content, child: _cachedBody!),
    );
  }

  Widget _buildMarkdown(BuildContext context, String content) {
    return MarkdownBody(
      data: content,
      shrinkWrap: true,
      onTapLink: openLinkOnCtrlClick,
      checkboxBuilder: (bool checked) => Padding(
        padding: const EdgeInsets.only(right: 6),
        child: Icon(
          checked
              ? Icons.check_box_rounded
              : Icons.check_box_outline_blank_rounded,
          size: 16,
          color: checked
              ? context.appColors.accent
              : context.appColors.textSecondary,
        ),
      ),
      builders: {
        'pre': SelectableCodeBlockBuilder(
          textStyle: TextStyle(
            color: context.appColors.assistantText,
            fontSize: 12.5,
            fontFamily: 'monospace',
          ),
        ),
      },
      styleSheet: MarkdownStyleSheet(
        p: TextStyle(
          color: context.appColors.assistantText,
          fontSize: 13.5,
          height: 1.45,
        ),
        code: TextStyle(
          color: context.appColors.assistantText,
          backgroundColor: context.appColors.toolBg.withValues(alpha: 0.6),
          fontSize: 12.5,
          fontFamily: 'monospace',
        ),
        codeblockDecoration: BoxDecoration(
          color: context.appColors.toolBg,
          borderRadius: BorderRadius.circular(8),
        ),
        codeblockPadding: const EdgeInsets.all(12),
        a: TextStyle(color: context.appColors.accentLight),
        listBullet: TextStyle(
          color: context.appColors.assistantText,
          fontSize: 13.5,
        ),
        h1: TextStyle(
          color: context.appColors.assistantText,
          fontSize: 20,
          fontWeight: FontWeight.bold,
        ),
        h2: TextStyle(
          color: context.appColors.assistantText,
          fontSize: 18,
          fontWeight: FontWeight.bold,
        ),
        h3: TextStyle(
          color: context.appColors.assistantText,
          fontSize: 16,
          fontWeight: FontWeight.bold,
        ),
        blockquoteDecoration: BoxDecoration(
          border: Border(
            left: BorderSide(color: context.appColors.accentDim, width: 3),
          ),
          color: context.appColors.toolBg.withValues(alpha: 0.3),
        ),
        blockquotePadding: const EdgeInsets.only(left: 12, top: 4, bottom: 4),
        tableBorder: TableBorder.all(color: context.appColors.divider),
        tableHead: TextStyle(
          color: context.appColors.assistantText,
          fontWeight: FontWeight.bold,
        ),
        tableBody: TextStyle(color: context.appColors.assistantText),
        horizontalRuleDecoration: BoxDecoration(
          border: Border(top: BorderSide(color: context.appColors.divider)),
        ),
      ),
    );
  }
}
