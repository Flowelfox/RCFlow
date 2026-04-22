import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:provider/provider.dart';

import '../../../models/ws_messages.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';
import '../../utils/link_utils.dart';
import '../../utils/markdown_copy_menu.dart';

class AgentSessionStartBubble extends StatefulWidget {
  final DisplayMessage message;
  const AgentSessionStartBubble({super.key, required this.message});

  @override
  State<AgentSessionStartBubble> createState() =>
      _AgentSessionStartBubbleState();
}

class _AgentSessionStartBubbleState extends State<AgentSessionStartBubble> {
  // Cache the rendered MarkdownBody by (displayed prompt). The body is the
  // expensive piece — outer chrome (icon, working-dir line) is cheap. Cleared
  // on theme change via didChangeDependencies and on message identity change
  // via didUpdateWidget. Agent-start prompts don't stream, but this bubble
  // does rebuild on every PaneState notify while a stream is in flight.
  String? _cachedDisplayPrompt;
  Widget? _cachedBody;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _cachedDisplayPrompt = null;
    _cachedBody = null;
  }

  @override
  void didUpdateWidget(AgentSessionStartBubble oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(oldWidget.message, widget.message)) {
      _cachedDisplayPrompt = null;
      _cachedBody = null;
    }
  }

  @override
  Widget build(BuildContext context) {
    final message = widget.message;
    final displayName = message.displayName ?? message.toolName ?? 'Agent';
    final prompt = message.content;
    final workingDir = message.toolInput?['working_directory'] as String?;
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
            left: BorderSide(color: context.appColors.accent, width: 3),
          ),
        ),
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  Icons.rocket_launch_rounded,
                  color: context.appColors.accentLight,
                  size: 16,
                ),
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
              MarkdownCopyMenu(
                rawMarkdown: prompt,
                child: _cachedMarkdownBody(context, displayPrompt),
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
                  Icon(
                    Icons.folder_outlined,
                    color: context.appColors.textSecondary,
                    size: 12,
                  ),
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

  Widget _cachedMarkdownBody(BuildContext context, String displayPrompt) {
    if (_cachedBody == null || _cachedDisplayPrompt != displayPrompt) {
      _cachedDisplayPrompt = displayPrompt;
      _cachedBody = MarkdownBody(
        data: displayPrompt,
        shrinkWrap: true,
        onTapLink: openLinkOnCtrlClick,
        styleSheet: MarkdownStyleSheet(
          p: TextStyle(
            color: context.appColors.textPrimary,
            fontSize: 12.5,
            height: 1.4,
          ),
          code: TextStyle(
            color: context.appColors.textPrimary,
            backgroundColor: context.appColors.toolBg.withValues(alpha: 0.6),
            fontSize: 11.5,
            fontFamily: 'monospace',
          ),
          codeblockDecoration: BoxDecoration(
            color: context.appColors.toolBg,
            borderRadius: BorderRadius.circular(8),
          ),
          codeblockPadding: const EdgeInsets.all(10),
          a: TextStyle(color: context.appColors.accentLight),
          listBullet: TextStyle(
            color: context.appColors.textPrimary,
            fontSize: 12.5,
          ),
          h1: TextStyle(
            color: context.appColors.textPrimary,
            fontSize: 16,
            fontWeight: FontWeight.bold,
          ),
          h2: TextStyle(
            color: context.appColors.textPrimary,
            fontSize: 14.5,
            fontWeight: FontWeight.bold,
          ),
          h3: TextStyle(
            color: context.appColors.textPrimary,
            fontSize: 13.5,
            fontWeight: FontWeight.bold,
          ),
          blockquoteDecoration: BoxDecoration(
            border: Border(
              left: BorderSide(color: context.appColors.accentDim, width: 3),
            ),
            color: context.appColors.toolBg.withValues(alpha: 0.3),
          ),
          blockquotePadding: const EdgeInsets.only(
            left: 10,
            top: 4,
            bottom: 4,
          ),
        ),
      );
    }
    return _cachedBody!;
  }
}
