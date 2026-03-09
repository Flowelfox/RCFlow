import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/ws_messages.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';

class AgentSessionStartBubble extends StatelessWidget {
  final DisplayMessage message;
  const AgentSessionStartBubble({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    final displayName =
        message.displayName ?? message.toolName ?? 'Agent';
    final prompt = message.content;
    final workingDir =
        message.toolInput?['working_directory'] as String?;
    final expanded = message.expanded;
    final shouldTruncate = prompt.length > 200;
    final displayPrompt = (!expanded && shouldTruncate)
        ? '${prompt.substring(0, 200)}...'
        : prompt;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Container(
        decoration: BoxDecoration(
          color: context.appColors.accent.withAlpha(20),
          borderRadius: BorderRadius.circular(10),
          border: Border(
            left: BorderSide(
                color: context.appColors.accent, width: 3),
          ),
        ),
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.rocket_launch_rounded,
                    color: context.appColors.accentLight, size: 16),
                const SizedBox(width: 8),
                Text(
                  '$displayName started',
                  style: TextStyle(
                    color: context.appColors.accentLight,
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                    fontFamily: 'monospace',
                  ),
                ),
              ],
            ),
            if (prompt.isNotEmpty) ...[
              const SizedBox(height: 8),
              Text(
                displayPrompt,
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 12.5,
                  fontStyle: FontStyle.italic,
                  height: 1.4,
                ),
              ),
              if (shouldTruncate)
                Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: MouseRegion(
                    cursor: SystemMouseCursors.click,
                    child: GestureDetector(
                      onTap: () {
                        message.expanded = !message.expanded;
                        context.read<PaneState>().refresh();
                      },
                      child: Text(
                        expanded ? 'Show less' : 'Show more',
                        style: TextStyle(
                          color: context.appColors.accentLight,
                          fontSize: 11.5,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ),
                  ),
                ),
            ],
            if (workingDir != null && workingDir.isNotEmpty) ...[
              const SizedBox(height: 6),
              Row(
                children: [
                  Icon(Icons.folder_outlined,
                      color: context.appColors.textSecondary,
                      size: 12),
                  const SizedBox(width: 4),
                  Flexible(
                    child: Text(
                      workingDir,
                      style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 11,
                        fontFamily: 'monospace',
                      ),
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                ],
              ),
            ],
          ],
        ),
      ),
    );
  }
}
