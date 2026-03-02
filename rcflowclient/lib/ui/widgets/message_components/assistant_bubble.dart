import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';

import '../../../models/ws_messages.dart';
import '../../../theme.dart';

class AssistantBubble extends StatelessWidget {
  final DisplayMessage message;
  const AssistantBubble({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 4, bottom: 4, right: 32),
      child: MarkdownBody(
        data: message.content.replaceAll('[SessionEndAsk]', '').trimRight(),
        shrinkWrap: true,
        styleSheet: MarkdownStyleSheet(
          p: const TextStyle(
            color: kAssistantText,
            fontSize: 13.5,
            height: 1.45,
          ),
          code: TextStyle(
            color: kAssistantText,
            backgroundColor: kToolBg.withValues(alpha: 0.6),
            fontSize: 12.5,
            fontFamily: 'monospace',
          ),
          codeblockDecoration: BoxDecoration(
            color: kToolBg,
            borderRadius: BorderRadius.circular(8),
          ),
          codeblockPadding: const EdgeInsets.all(12),
          a: const TextStyle(color: kAccentLight),
          listBullet: const TextStyle(color: kAssistantText, fontSize: 13.5),
          h1: const TextStyle(color: kAssistantText, fontSize: 20, fontWeight: FontWeight.bold),
          h2: const TextStyle(color: kAssistantText, fontSize: 18, fontWeight: FontWeight.bold),
          h3: const TextStyle(color: kAssistantText, fontSize: 16, fontWeight: FontWeight.bold),
          blockquoteDecoration: BoxDecoration(
            border: const Border(left: BorderSide(color: kAccentDim, width: 3)),
            color: kToolBg.withValues(alpha: 0.3),
          ),
          blockquotePadding: const EdgeInsets.only(left: 12, top: 4, bottom: 4),
          tableBorder: TableBorder.all(color: kDivider),
          tableHead: const TextStyle(color: kAssistantText, fontWeight: FontWeight.bold),
          tableBody: const TextStyle(color: kAssistantText),
          horizontalRuleDecoration: const BoxDecoration(
            border: Border(top: BorderSide(color: kDivider)),
          ),
        ),
      ),
    );
  }
}
