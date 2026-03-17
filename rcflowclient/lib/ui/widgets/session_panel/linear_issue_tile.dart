import 'package:flutter/material.dart';

import '../../../models/linear_issue_info.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import 'helpers.dart';

/// Sidebar tile for a single cached Linear issue.
class LinearIssueTile extends StatelessWidget {
  final LinearIssueInfo issue;
  final AppState state;
  final VoidCallback? onSelected;

  const LinearIssueTile({
    super.key,
    required this.issue,
    required this.state,
    this.onSelected,
  });

  static const _priorityIcons = {
    0: Icons.remove,
    1: Icons.keyboard_double_arrow_up,
    2: Icons.keyboard_arrow_up,
    3: Icons.drag_handle,
    4: Icons.keyboard_arrow_down,
  };

  static const _priorityColors = {
    0: Color(0xFF6B7280),
    1: Color(0xFFEF4444),
    2: Color(0xFFF97316),
    3: Color(0xFFF59E0B),
    4: Color(0xFF6B7280),
  };

  static const _stateTypeColors = {
    'triage': Color(0xFF8B5CF6),
    'backlog': Color(0xFF6B7280),
    'unstarted': Color(0xFF6B7280),
    'started': Color(0xFF3B82F6),
    'completed': Color(0xFF10B981),
    'cancelled': Color(0xFF9CA3AF),
  };

  @override
  Widget build(BuildContext context) {
    final isViewed = _isIssueViewed();
    final isActive = _isIssueActive();
    final priorityIcon = _priorityIcons[issue.priority] ?? Icons.remove;
    final priorityColor =
        _priorityColors[issue.priority] ?? context.appColors.textMuted;
    final stateColor =
        _stateTypeColors[issue.stateType] ?? context.appColors.textMuted;

    return GestureDetector(
      onSecondaryTapUp: (details) =>
          _showContextMenu(context, details.globalPosition),
      child: Container(
        decoration: BoxDecoration(
          color: isActive
              ? context.appColors.accent.withAlpha(25)
              : isViewed
                  ? context.appColors.accent.withAlpha(12)
                  : null,
          border: isActive
              ? Border(
                  left:
                      BorderSide(color: context.appColors.accent, width: 3))
              : isViewed
                  ? Border(
                      left: BorderSide(
                          color: context.appColors.accent.withAlpha(80),
                          width: 2))
                  : null,
        ),
        child: ListTile(
          leading: Container(
            width: 30,
            height: 30,
            decoration: BoxDecoration(
              color: stateColor.withAlpha(30),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Icon(priorityIcon, color: priorityColor, size: 16),
          ),
          title: Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
                decoration: BoxDecoration(
                  color: context.appColors.bgElevated,
                  borderRadius: BorderRadius.circular(4),
                  border: Border.all(
                      color: context.appColors.divider, width: 0.5),
                ),
                child: Text(
                  issue.identifier,
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
                  issue.title,
                  style: TextStyle(
                    color: isActive
                        ? context.appColors.accentLight
                        : context.appColors.textPrimary,
                    fontSize: 12,
                    fontWeight:
                        isActive ? FontWeight.w600 : FontWeight.w400,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          subtitle: Text(
            _subtitle(),
            style:
                TextStyle(color: context.appColors.textMuted, fontSize: 10),
          ),
          trailing: issue.taskId != null
              ? Tooltip(
                  message: 'Linked to task',
                  child: Icon(Icons.link,
                      color: context.appColors.textMuted, size: 14),
                )
              : null,
          dense: true,
          visualDensity: const VisualDensity(vertical: -4),
          contentPadding: const EdgeInsets.only(left: 16, right: 8),
          onTap: () {
            state.openLinearIssueInPane(issue.id);
            onSelected?.call();
          },
        ),
      ),
    );
  }

  bool _isIssueViewed() {
    for (final pane in state.panes.values) {
      if (pane.linearIssueId == issue.id) return true;
    }
    return false;
  }

  bool _isIssueActive() {
    if (state.hasNoPanes) return false;
    return state.activePane.linearIssueId == issue.id;
  }

  String _subtitle() {
    final local = issue.updatedAt.toLocal();
    final time = '${monthAbbr(local.month)} ${local.day}, '
        '${local.hour.toString().padLeft(2, '0')}:'
        '${local.minute.toString().padLeft(2, '0')}';
    return '$time \u00B7 ${issue.stateName}';
  }

  void _showContextMenu(BuildContext context, Offset position) {
    final overlay =
        Overlay.of(context).context.findRenderObject() as RenderBox;
    showMenu<String>(
      context: context,
      position: RelativeRect.fromRect(
        position & const Size(1, 1),
        Offset.zero & overlay.size,
      ),
      color: context.appColors.bgSurface,
      shape:
          RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      items: [
        PopupMenuItem(
          value: 'open',
          child: Row(
            children: [
              Icon(Icons.open_in_new,
                  color: context.appColors.textSecondary, size: 18),
              const SizedBox(width: 8),
              Text('Open in Linear',
                  style: TextStyle(color: context.appColors.textPrimary)),
            ],
          ),
        ),
      ],
    ).then((value) {
      if (!context.mounted) return;
      // url_launcher handled at the pane level; context menu is minimal here
    });
  }
}
