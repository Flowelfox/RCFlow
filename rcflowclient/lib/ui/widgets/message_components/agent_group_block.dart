import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/ws_messages.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';
import '../message_bubble.dart';

class AgentGroupBlock extends StatelessWidget {
  final DisplayMessage message;
  const AgentGroupBlock({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    final expanded = message.expanded;
    final running = message.isGroupRunning;
    final toolCount = message.toolCount;
    final children = message.children ?? [];
    final hasChildren = children.isNotEmpty;
    final displayName =
        message.displayName ?? message.toolName ?? 'Agent';

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
              onTap: hasChildren
                  ? () {
                      message.expanded = !message.expanded;
                      context.read<PaneState>().refresh();
                    }
                  : null,
              child: Container(
                color: Colors.transparent,
                padding:
                    EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: Row(
                  children: [
                    if (hasChildren)
                      Icon(
                        expanded
                            ? Icons.expand_less_rounded
                            : Icons.expand_more_rounded,
                        color: context.appColors.accentLight,
                        size: 18,
                      )
                    else
                      SizedBox(width: 18),
                    SizedBox(width: 8),
                    if (running)
                      _SpinningIcon()
                    else
                      Icon(
                        Icons.check_circle_outline_rounded,
                        color: context.appColors.successText,
                        size: 14,
                      ),
                    SizedBox(width: 6),
                    Text(
                      displayName,
                      style: TextStyle(
                        color: context.appColors.accentLight,
                        fontSize: 13,
                        fontFamily: 'monospace',
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    if (toolCount > 0) ...[
                      SizedBox(width: 8),
                      Container(
                        padding: EdgeInsets.symmetric(
                            horizontal: 6, vertical: 1),
                        decoration: BoxDecoration(
                          color: context.appColors.accentDim,
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: Text(
                          '$toolCount tool${toolCount == 1 ? '' : 's'}',
                          style: TextStyle(
                            color: context.appColors.accentLight,
                            fontSize: 10,
                            fontFamily: 'monospace',
                          ),
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
            if (expanded && children.isNotEmpty)
              Padding(
                padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    for (final child in children) MessageBubble(message: child),
                  ],
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _SpinningIcon extends StatefulWidget {
  const _SpinningIcon();

  @override
  State<_SpinningIcon> createState() => _SpinningIconState();
}

class _SpinningIconState extends State<_SpinningIcon>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      duration: const Duration(milliseconds: 1200),
      vsync: this,
    )..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return RotationTransition(
      turns: _controller,
      child: Icon(
        Icons.sync_rounded,
        color: context.appColors.accentLight,
        size: 14,
      ),
    );
  }
}
