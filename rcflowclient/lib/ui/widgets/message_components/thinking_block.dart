import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/ws_messages.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';

class ThinkingBlock extends StatelessWidget {
  final DisplayMessage message;
  const ThinkingBlock({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    final expanded = message.expanded;
    final content = message.content;

    return Padding(
      padding: EdgeInsets.symmetric(vertical: 4),
      child: Container(
        decoration: BoxDecoration(
          color: context.appColors.toolBg,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: context.appColors.divider),
        ),
        clipBehavior: Clip.antiAlias,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            GestureDetector(
              onTap: () {
                message.expanded = !message.expanded;
                context.read<PaneState>().refresh();
              },
              child: Container(
                color: Colors.transparent,
                padding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: Row(
                  children: [
                    Icon(
                      expanded
                          ? Icons.expand_less_rounded
                          : Icons.expand_more_rounded,
                      color: context.appColors.toolAccent,
                      size: 18,
                    ),
                    SizedBox(width: 8),
                    Icon(
                      Icons.psychology_rounded,
                      color: context.appColors.toolAccent,
                      size: 14,
                    ),
                    SizedBox(width: 6),
                    Text(
                      'Thinking',
                      style: TextStyle(
                        color: context.appColors.toolAccent,
                        fontSize: 13,
                        fontFamily: 'monospace',
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ],
                ),
              ),
            ),
            if (expanded && content.isNotEmpty)
              Container(
                width: double.infinity,
                padding: EdgeInsets.fromLTRB(12, 0, 12, 10),
                child: Text(
                  content,
                  style: TextStyle(
                    color: context.appColors.toolOutputText,
                    fontSize: 11,
                    fontFamily: 'monospace',
                    height: 1.3,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}
