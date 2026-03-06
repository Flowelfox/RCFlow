import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/split_tree.dart';
import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';

/// Thin 32px header bar for a pane — shows session title and close button.
/// Only displayed when there are multiple panes.
class PaneHeader extends StatelessWidget {
  const PaneHeader({super.key});

  @override
  Widget build(BuildContext context) {
    final pane = context.watch<PaneState>();
    final appState = context.read<AppState>();
    final isActive = appState.activePaneId == pane.paneId;

    final sessionId = pane.sessionId;
    String title;
    if (sessionId != null) {
      final session = appState.sessions.cast().firstWhere(
            (s) => s?.sessionId == sessionId,
            orElse: () => null,
          );
      title = session?.title ?? _shortId(sessionId);
    } else if (pane.readyForNewChat) {
      title = 'New Chat';
    } else {
      title = 'Home';
    }

    return Container(
      height: 32,
      decoration: BoxDecoration(
        color: isActive ? context.appColors.accent.withAlpha(20) : context.appColors.bgSurface,
        border: Border(bottom: BorderSide(color: context.appColors.divider)),
      ),
      padding: EdgeInsets.symmetric(horizontal: 8),
      child: Row(
        children: [
          if (isActive)
            Container(
              width: 6,
              height: 6,
              margin: EdgeInsets.only(right: 6),
              decoration: BoxDecoration(
                color: context.appColors.accent,
                shape: BoxShape.circle,
              ),
            ),
          Expanded(
            child: Text(
              title,
              style: TextStyle(
                color: isActive ? context.appColors.textPrimary : context.appColors.textSecondary,
                fontSize: 12,
                fontWeight: isActive ? FontWeight.w600 : FontWeight.w400,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          SizedBox(
            width: 24,
            height: 24,
            child: IconButton(
              padding: EdgeInsets.zero,
              iconSize: 14,
              icon: Icon(
                Icons.view_column_outlined,
                color: context.appColors.textMuted,
              ),
              tooltip: 'Split pane',
              onPressed: () => _showSplitMenu(context, pane, appState),
            ),
          ),
          SizedBox(
            width: 24,
            height: 24,
            child: IconButton(
              padding: EdgeInsets.zero,
              iconSize: 14,
              icon: Icon(Icons.close_rounded, color: context.appColors.textMuted),
              tooltip: 'Close pane',
              onPressed: () => appState.closePane(pane.paneId),
            ),
          ),
        ],
      ),
    );
  }

  static String _shortId(String id) =>
      id.length >= 8 ? '${id.substring(0, 8)}...' : id;

  void _showSplitMenu(
    BuildContext context,
    PaneState pane,
    AppState appState,
  ) {
    final button = context.findRenderObject() as RenderBox;
    final overlay =
        Overlay.of(context).context.findRenderObject() as RenderBox;
    final position = RelativeRect.fromRect(
      Rect.fromPoints(
        button.localToGlobal(Offset(0, button.size.height), ancestor: overlay),
        button.localToGlobal(
          button.size.bottomRight(Offset.zero),
          ancestor: overlay,
        ),
      ),
      Offset.zero & overlay.size,
    );

    showMenu<SplitAxis>(
      context: context,
      position: position,
      color: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      items: [
        PopupMenuItem(
          value: SplitAxis.horizontal,
          child: Row(
            children: [
              Icon(Icons.view_column_outlined, color: context.appColors.textSecondary, size: 18),
              SizedBox(width: 10),
              Text(
                'Split Right',
                style: TextStyle(color: context.appColors.textPrimary, fontSize: 14),
              ),
            ],
          ),
        ),
        PopupMenuItem(
          value: SplitAxis.vertical,
          child: Row(
            children: [
              Icon(Icons.view_agenda_outlined, color: context.appColors.textSecondary, size: 18),
              SizedBox(width: 10),
              Text(
                'Split Down',
                style: TextStyle(color: context.appColors.textPrimary, fontSize: 14),
              ),
            ],
          ),
        ),
      ],
    ).then((axis) {
      if (axis != null && context.mounted) {
        appState.splitPane(pane.paneId, axis);
      }
    });
  }
}
