import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/session_info.dart';
import '../../models/split_tree.dart';
import '../../models/todo_item.dart';
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
          if (pane.canGoBack)
            SizedBox(
              width: 24,
              height: 24,
              child: IconButton(
                padding: EdgeInsets.zero,
                iconSize: 14,
                icon: Icon(Icons.arrow_back_rounded, color: context.appColors.textMuted),
                tooltip: 'Back',
                onPressed: () => appState.goBack(pane.paneId),
              ),
            ),
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
          if (pane.todos.isNotEmpty) _buildTodoBadge(context, pane),
          if (sessionId != null) _buildTokenBadge(context, appState, sessionId),
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

  Widget _buildTodoBadge(BuildContext context, PaneState pane) {
    final todos = pane.todos;
    final completed =
        todos.where((t) => t.status == TodoStatus.completed).length;
    final total = todos.length;

    return Padding(
      padding: const EdgeInsets.only(right: 4),
      child: InkWell(
        onTap: pane.toggleTodoPanel,
        borderRadius: BorderRadius.circular(4),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 2),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                Icons.checklist_rounded,
                size: 12,
                color: pane.todoPanelVisible
                    ? context.appColors.accent
                    : context.appColors.textMuted,
              ),
              const SizedBox(width: 3),
              Text(
                '$completed/$total',
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 10,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildTokenBadge(BuildContext context, AppState appState, String sessionId) {
    final session = appState.sessions.cast<SessionInfo?>().firstWhere(
          (s) => s?.sessionId == sessionId,
          orElse: () => null,
        );
    if (session == null) return const SizedBox.shrink();

    final totalIn = session.totalInputTokens;
    final totalOut = session.totalOutputTokens;
    if (totalIn == 0 && totalOut == 0) return const SizedBox.shrink();

    final worker = appState.workerForSession(sessionId);
    final inLimit = worker?.inputTokenLimit ?? 0;
    final outLimit = worker?.outputTokenLimit ?? 0;

    final inStr = inLimit > 0
        ? '${_formatTokens(totalIn)}/${_formatTokens(inLimit)}'
        : _formatTokens(totalIn);
    final outStr = outLimit > 0
        ? '${_formatTokens(totalOut)}/${_formatTokens(outLimit)}'
        : _formatTokens(totalOut);

    // Determine usage color based on highest ratio
    final usageRatio = _maxUsageRatio(totalIn, inLimit, totalOut, outLimit);
    final usageColor = _usageColor(context, usageRatio);

    // Build detailed tooltip
    final tooltipLines = <String>[
      'Input: ${_formatTokensLong(totalIn)}${inLimit > 0 ? ' / ${_formatTokensLong(inLimit)}' : ''}',
      'Output: ${_formatTokensLong(totalOut)}${outLimit > 0 ? ' / ${_formatTokensLong(outLimit)}' : ''}',
    ];
    if (session.cacheReadInputTokens > 0 || session.cacheCreationInputTokens > 0) {
      tooltipLines.add('Cache read: ${_formatTokensLong(session.cacheReadInputTokens)}');
      tooltipLines.add('Cache write: ${_formatTokensLong(session.cacheCreationInputTokens)}');
    }

    return Tooltip(
      message: tooltipLines.join('\n'),
      preferBelow: false,
      child: Container(
        margin: const EdgeInsets.only(right: 4),
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
        decoration: BoxDecoration(
          color: usageColor.withAlpha(25),
          borderRadius: BorderRadius.circular(4),
          border: Border.all(color: usageColor.withAlpha(60), width: 0.5),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.token_outlined, size: 11, color: usageColor),
            const SizedBox(width: 3),
            Text(
              '$inStr in \u00B7 $outStr out',
              style: TextStyle(
                color: usageColor,
                fontSize: 10,
                fontWeight: FontWeight.w500,
              ),
            ),
          ],
        ),
      ),
    );
  }

  static double _maxUsageRatio(int totalIn, int inLimit, int totalOut, int outLimit) {
    double ratio = 0;
    if (inLimit > 0) ratio = totalIn / inLimit;
    if (outLimit > 0) {
      final outRatio = totalOut / outLimit;
      if (outRatio > ratio) ratio = outRatio;
    }
    return ratio;
  }

  static Color _usageColor(BuildContext context, double ratio) {
    if (ratio >= 0.9) return context.appColors.errorText;
    if (ratio >= 0.7) return context.appColors.toolAccent;
    return context.appColors.textSecondary;
  }

  static String _formatTokensLong(int tokens) {
    if (tokens >= 1000000) {
      return '${(tokens / 1000000).toStringAsFixed(2)}M';
    }
    if (tokens >= 1000) {
      return '${(tokens / 1000).toStringAsFixed(1)}K';
    }
    return tokens.toString();
  }

  static String _formatTokens(int tokens) {
    if (tokens >= 1000000) return '${(tokens / 1000000).toStringAsFixed(1)}M';
    if (tokens >= 1000) return '${(tokens / 1000).toStringAsFixed(1)}K';
    return tokens.toString();
  }

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
