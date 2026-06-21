import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../models/app_notification.dart';
import '../../../models/deduped_pr.dart';
import '../../../models/github_pr_info.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import '../session_panel/github_pr_drag_data.dart';
import '../session_panel/helpers.dart';
import 'pr_action_router.dart';
import 'pr_status.dart';

/// Sidebar tile for a single (deduplicated) GitHub pull request. When several
/// connected workers back the same PR, [pr] carries them all as sources and the
/// tile shows a "Worker / Project" badge per source.
class PrTile extends StatelessWidget {
  final DedupedPr pr;
  final AppState state;
  final VoidCallback? onSelected;

  const PrTile({
    super.key,
    required this.pr,
    required this.state,
    this.onSelected,
  });

  /// The displayed PR row (freshest source).
  GithubPrInfo get c => pr.canonical;

  /// The source to open/act on by default: prefer a worker that has the repo
  /// cloned (so conflicts/diff resolve locally), else the freshest.
  GithubPrInfo get _primary =>
      pr.cloneSources.isNotEmpty ? pr.cloneSources.first : c;

  @override
  Widget build(BuildContext context) {
    final isViewed = _isPrViewed();
    final isActive = _isPrActive();
    final status = prStatusVisual(c);
    final badgeColor = status.color;

    final tile = Container(
      decoration: BoxDecoration(
        color: isActive
            ? context.appColors.accent.withAlpha(25)
            : isViewed
            ? context.appColors.accent.withAlpha(12)
            : null,
        border: isActive
            ? Border(left: BorderSide(color: context.appColors.accent, width: 3))
            : isViewed
            ? Border(
                left: BorderSide(
                  color: context.appColors.accent.withAlpha(80),
                  width: 2,
                ),
              )
            : null,
      ),
      child: Material(
        type: MaterialType.transparency,
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
                border: Border.all(color: context.appColors.divider, width: 0.5),
              ),
              child: Text(
                '#${c.number}',
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
                c.title,
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
          _subtitle(status.label),
          style: TextStyle(color: context.appColors.textMuted, fontSize: 10),
        ),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              '+${c.additions}',
              style: const TextStyle(color: Color(0xFF56D364), fontSize: 10),
            ),
            const SizedBox(width: 4),
            Text(
              '-${c.deletions}',
              style: const TextStyle(color: Color(0xFFF85149), fontSize: 10),
            ),
          ],
        ),
        dense: true,
        visualDensity: const VisualDensity(vertical: -4),
        contentPadding: const EdgeInsets.only(left: 16, right: 8),
        onTap: () {
          state.openGithubPrInPane(_primary.id);
          onSelected?.call();
        },
      ),
      ),
    );

    // Drag a PR onto a pane to open/split it there (mirrors session/task drag).
    final draggable = Draggable<GithubPrDragData>(
      data: GithubPrDragData(
        prId: _primary.id,
        workerId: _primary.workerId,
        label: c.title,
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
                '#${c.number}',
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
                  c.title,
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

    // Right-click → context menu (AI review / Open on GitHub).
    return GestureDetector(
      onSecondaryTapUp: (d) => _showContextMenu(context, d.globalPosition),
      child: draggable,
    );
  }

  Future<void> _showContextMenu(BuildContext context, Offset pos) async {
    final colors = context.appColors;
    final selected = await showMenu<String>(
      context: context,
      position: RelativeRect.fromLTRB(pos.dx, pos.dy, pos.dx, pos.dy),
      color: colors.bgElevated,
      items: [
        PopupMenuItem(
          value: 'review',
          child: Row(
            children: [
              Icon(Icons.rate_review, size: 16, color: colors.textMuted),
              const SizedBox(width: 8),
              const Text('AI review'),
            ],
          ),
        ),
        PopupMenuItem(
          value: 'github',
          child: Row(
            children: [
              Icon(Icons.open_in_new, size: 16, color: colors.textMuted),
              const SizedBox(width: 8),
              const Text('Open on GitHub'),
            ],
          ),
        ),
      ],
    );
    if (selected == 'review') {
      if (context.mounted) await _aiReview(context);
    } else if (selected == 'github') {
      await _openOnGithub();
    }
  }

  /// AI review from the sidebar — route to a clone-holding worker (default /
  /// picker) and start the review on the active (or a new) chat pane.
  Future<void> _aiReview(BuildContext context) async {
    final clones = pr.cloneSources;
    if (clones.isEmpty) {
      state.showNotification(
        level: NotificationLevel.warning,
        title: 'No local clone',
        body: 'AI review runs in a local checkout — no connected worker has this '
            'repository cloned.',
      );
      return;
    }
    GithubPrInfo target;
    if (clones.length == 1) {
      target = clones.first;
    } else {
      final chosen = await resolvePrActionWorker(
        context,
        state,
        pr,
        clones,
        {for (final s in clones) s.id: s.projectName},
      );
      if (chosen == null) return;
      target = chosen;
    }
    final paneId = state.ensureChatPane().paneId;
    state.startPrAssist(
      paneId,
      target,
      'review',
      projectName: target.projectName,
      projectPath: target.projectPath,
    );
  }

  Future<void> _openOnGithub() async {
    final uri = Uri.tryParse(c.url);
    if (uri == null) return;
    if (await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    }
  }

  bool _isPrViewed() {
    for (final pane in state.panes.values) {
      if (pr.sources.any((s) => pane.githubPrId == s.id)) return true;
    }
    return false;
  }

  bool _isPrActive() {
    if (state.hasNoPanes) return false;
    final active = state.activePane.githubPrId;
    return active != null && pr.sources.any((s) => s.id == active);
  }

  String _subtitle(String badgeLabel) {
    final local = c.updatedAt.toLocal();
    final time =
        '${monthAbbr(local.month)} ${local.day}, '
        '${local.hour.toString().padLeft(2, '0')}:'
        '${local.minute.toString().padLeft(2, '0')}';
    return '${c.repoSlug} · $badgeLabel · $time';
  }
}
