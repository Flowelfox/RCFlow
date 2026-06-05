import 'package:flutter/material.dart';

import '../../../models/github_pr_info.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import '../session_panel/github_pr_drag_data.dart';
import '../session_panel/helpers.dart';
import 'pr_status.dart';

/// Sidebar tile for a single cached GitHub pull request.
class PrTile extends StatelessWidget {
  final GithubPrInfo pr;
  final AppState state;
  final VoidCallback? onSelected;

  const PrTile({
    super.key,
    required this.pr,
    required this.state,
    this.onSelected,
  });

  @override
  Widget build(BuildContext context) {
    final isViewed = _isPrViewed();
    final isActive = _isPrActive();
    final status = prStatusVisual(pr);
    final badgeLabel = status.label;
    final badgeColor = status.color;

    final tile = Container(
      decoration: BoxDecoration(
        color: isActive
            ? context.appColors.accent.withAlpha(25)
            : isViewed
            ? context.appColors.accent.withAlpha(12)
            : null,
        border: isActive
            ? Border(
                left: BorderSide(color: context.appColors.accent, width: 3),
              )
            : isViewed
            ? Border(
                left: BorderSide(
                  color: context.appColors.accent.withAlpha(80),
                  width: 2,
                ),
              )
            : null,
      ),
      child: ListTile(
        leading: Container(
          width: 30,
          height: 30,
          decoration: BoxDecoration(
            color: badgeColor.withAlpha(30),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Icon(status.icon, color: badgeColor, size: 16),
        ),
        title: Row(
          children: [
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
              decoration: BoxDecoration(
                color: context.appColors.bgElevated,
                borderRadius: BorderRadius.circular(4),
                border: Border.all(
                  color: context.appColors.divider,
                  width: 0.5,
                ),
              ),
              child: Text(
                '#${pr.number}',
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 10,
                  fontWeight: FontWeight.w600,
                  fontFamily: 'monospace',
                ),
              ),
            ),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                pr.title,
                style: TextStyle(
                  color: isActive
                      ? context.appColors.accentLight
                      : context.appColors.textPrimary,
                  fontSize: 12,
                  fontWeight: isActive ? FontWeight.w600 : FontWeight.w400,
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
        subtitle: Text(
          _subtitle(badgeLabel),
          style: TextStyle(color: context.appColors.textMuted, fontSize: 10),
        ),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              '+${pr.additions}',
              style: const TextStyle(color: Color(0xFF56D364), fontSize: 10),
            ),
            const SizedBox(width: 4),
            Text(
              '-${pr.deletions}',
              style: const TextStyle(color: Color(0xFFF85149), fontSize: 10),
            ),
          ],
        ),
        dense: true,
        visualDensity: const VisualDensity(vertical: -4),
        contentPadding: const EdgeInsets.only(left: 16, right: 8),
        onTap: () {
          state.openGithubPrInPane(pr.id);
          onSelected?.call();
        },
      ),
    );

    // Drag a PR onto a pane to open/split it there (mirrors session/task drag).
    return Draggable<GithubPrDragData>(
      data: GithubPrDragData(
        prId: pr.id,
        workerId: pr.workerId,
        label: pr.title,
      ),
      feedback: Material(
        color: Colors.transparent,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          decoration: BoxDecoration(
            color: context.appColors.bgElevated,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: context.appColors.accent.withAlpha(120)),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withAlpha(100),
                blurRadius: 8,
                offset: const Offset(0, 2),
              ),
            ],
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(status.icon, color: badgeColor, size: 14),
              const SizedBox(width: 6),
              Text(
                '#${pr.number}',
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 11,
                  fontFamily: 'monospace',
                  decoration: TextDecoration.none,
                ),
              ),
              const SizedBox(width: 6),
              ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 180),
                child: Text(
                  pr.title,
                  style: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 12,
                    fontWeight: FontWeight.w500,
                    decoration: TextDecoration.none,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
      ),
      childWhenDragging: Opacity(opacity: 0.4, child: tile),
      child: tile,
    );
  }

  bool _isPrViewed() {
    for (final pane in state.panes.values) {
      if (pane.githubPrId == pr.id) return true;
    }
    return false;
  }

  bool _isPrActive() {
    if (state.hasNoPanes) return false;
    return state.activePane.githubPrId == pr.id;
  }

  String _subtitle(String badgeLabel) {
    final local = pr.updatedAt.toLocal();
    final time =
        '${monthAbbr(local.month)} ${local.day}, '
        '${local.hour.toString().padLeft(2, '0')}:'
        '${local.minute.toString().padLeft(2, '0')}';
    return '${pr.repoSlug} · $badgeLabel · $time';
  }
}
