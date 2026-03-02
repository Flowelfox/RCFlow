import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';

import '../../../models/ws_messages.dart';
import '../../../theme.dart';

class UserBubble extends StatelessWidget {
  final DisplayMessage message;
  const UserBubble({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 12, bottom: 4, left: 48),
      child: Align(
        alignment: Alignment.centerRight,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          decoration: const BoxDecoration(
            color: kUserBubble,
            borderRadius: BorderRadius.only(
              topLeft: Radius.circular(18),
              topRight: Radius.circular(18),
              bottomLeft: Radius.circular(18),
              bottomRight: Radius.circular(4),
            ),
          ),
          child: MarkdownBody(
            data: message.content,
            shrinkWrap: true,
            styleSheet: MarkdownStyleSheet(
              p: const TextStyle(
                color: kTextPrimary,
                fontSize: 15,
                height: 1.4,
              ),
              code: TextStyle(
                color: kTextPrimary,
                backgroundColor: Colors.black.withValues(alpha: 0.2),
                fontSize: 13.5,
                fontFamily: 'monospace',
              ),
              codeblockDecoration: BoxDecoration(
                color: Colors.black.withValues(alpha: 0.25),
                borderRadius: BorderRadius.circular(8),
              ),
              codeblockPadding: const EdgeInsets.all(12),
              a: const TextStyle(color: kAccentLight),
              listBullet: const TextStyle(color: kTextPrimary, fontSize: 15),
              h1: const TextStyle(color: kTextPrimary, fontSize: 20, fontWeight: FontWeight.bold),
              h2: const TextStyle(color: kTextPrimary, fontSize: 18, fontWeight: FontWeight.bold),
              h3: const TextStyle(color: kTextPrimary, fontSize: 16, fontWeight: FontWeight.bold),
              blockquoteDecoration: BoxDecoration(
                border: const Border(left: BorderSide(color: kAccentLight, width: 3)),
                color: Colors.black.withValues(alpha: 0.15),
              ),
              blockquotePadding: const EdgeInsets.only(left: 12, top: 4, bottom: 4),
              tableBorder: TableBorder.all(color: kDivider),
              tableHead: const TextStyle(color: kTextPrimary, fontWeight: FontWeight.bold),
              tableBody: const TextStyle(color: kTextPrimary),
              horizontalRuleDecoration: const BoxDecoration(
                border: Border(top: BorderSide(color: kDivider)),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
