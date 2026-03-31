import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';

import '../../../models/ws_messages.dart';
import '../../../theme.dart';
import '../../utils/link_utils.dart';

class UserBubble extends StatelessWidget {
  final DisplayMessage message;
  const UserBubble({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    final attachments = message.attachments;
    return Padding(
      padding: EdgeInsets.only(top: 12, bottom: 4, left: 48),
      child: Align(
        alignment: Alignment.centerRight,
        child: Container(
          padding: EdgeInsets.symmetric(horizontal: 16, vertical: 12),
          decoration: BoxDecoration(
            color: context.appColors.userBubble,
            borderRadius: BorderRadius.only(
              topLeft: Radius.circular(18),
              topRight: Radius.circular(18),
              bottomLeft: Radius.circular(18),
              bottomRight: Radius.circular(4),
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            mainAxisSize: MainAxisSize.min,
            children: [
              if (attachments != null && attachments.isNotEmpty) ...[
                Wrap(
                  spacing: 6,
                  runSpacing: 4,
                  alignment: WrapAlignment.end,
                  children: [
                    for (final att in attachments)
                      _AttachmentChip(
                        name: att['name'] as String? ?? '',
                        mimeType: att['mime_type'] as String? ?? '',
                      ),
                  ],
                ),
                SizedBox(height: 8),
              ],
              MarkdownBody(
                data: message.content.replaceAll('\n', '  \n'),
                shrinkWrap: true,
                onTapLink: openLinkOnCtrlClick,
                styleSheet: MarkdownStyleSheet(
                  p: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 15,
                    height: 1.4,
                  ),
                  code: TextStyle(
                    color: context.appColors.textPrimary,
                    backgroundColor: Colors.black.withValues(alpha: 0.2),
                    fontSize: 13.5,
                    fontFamily: 'monospace',
                  ),
                  codeblockDecoration: BoxDecoration(
                    color: Colors.black.withValues(alpha: 0.25),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  codeblockPadding: EdgeInsets.all(12),
                  a: TextStyle(color: context.appColors.accentLight),
                  listBullet: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 15,
                  ),
                  h1: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 20,
                    fontWeight: FontWeight.bold,
                  ),
                  h2: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 18,
                    fontWeight: FontWeight.bold,
                  ),
                  h3: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 16,
                    fontWeight: FontWeight.bold,
                  ),
                  blockquoteDecoration: BoxDecoration(
                    border: Border(
                      left: BorderSide(
                        color: context.appColors.accentLight,
                        width: 3,
                      ),
                    ),
                    color: Colors.black.withValues(alpha: 0.15),
                  ),
                  blockquotePadding: EdgeInsets.only(
                    left: 12,
                    top: 4,
                    bottom: 4,
                  ),
                  tableBorder: TableBorder.all(
                    color: context.appColors.divider,
                  ),
                  tableHead: TextStyle(
                    color: context.appColors.textPrimary,
                    fontWeight: FontWeight.bold,
                  ),
                  tableBody: TextStyle(color: context.appColors.textPrimary),
                  horizontalRuleDecoration: BoxDecoration(
                    border: Border(
                      top: BorderSide(color: context.appColors.divider),
                    ),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _AttachmentChip extends StatelessWidget {
  final String name;
  final String mimeType;

  const _AttachmentChip({required this.name, required this.mimeType});

  static bool _isImage(String mime) => mime.startsWith('image/');

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: Colors.black.withValues(alpha: 0.2),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: Colors.white.withValues(alpha: 0.15)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            _isImage(mimeType)
                ? Icons.image_rounded
                : Icons.insert_drive_file_rounded,
            size: 13,
            color: context.appColors.textSecondary,
          ),
          const SizedBox(width: 5),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 160),
            child: Text(
              name,
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 12,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );
  }
}
