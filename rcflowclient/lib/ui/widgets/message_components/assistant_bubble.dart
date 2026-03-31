import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';

import '../../../models/ws_messages.dart';
import '../../../theme.dart';
import '../../utils/link_utils.dart';

class AssistantBubble extends StatelessWidget {
  final DisplayMessage message;
  const AssistantBubble({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(top: 4, bottom: 4, right: 32),
      child: MarkdownBody(
        data: message.content.replaceAll('[SessionEndAsk]', '').trimRight(),
        shrinkWrap: true,
        onTapLink: openLinkOnCtrlClick,
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
          codeblockPadding: EdgeInsets.all(12),
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
          blockquotePadding: EdgeInsets.only(left: 12, top: 4, bottom: 4),
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
      ),
    );
  }
}
