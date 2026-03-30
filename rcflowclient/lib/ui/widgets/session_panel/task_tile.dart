import 'package:flutter/material.dart';

import '../../../models/task_info.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import 'helpers.dart';
import 'task_drag_data.dart';

class TaskTile extends StatelessWidget {
  final TaskInfo task;
  final AppState state;
  final VoidCallback? onTaskSelected;

  /// When `true`, renders the tile with a selection highlight and a checkbox
  /// icon in place of the status icon.
  final bool isSelected;

  /// When non-null, replaces the default open-in-pane tap behavior. The parent
  /// is responsible for calling [AppState.openTaskInPane] if appropriate.
  final VoidCallback? onTapOverride;

  /// When non-null, replaces the built-in single-task context menu. The parent
  /// receives the global tap position and can show its own menu (e.g. bulk).
  final void Function(Offset globalPosition)? onSecondaryTapOverride;

  const TaskTile({
    super.key,
    required this.task,
    required this.state,
    this.onTaskSelected,
    this.isSelected = false,
    this.onTapOverride,
    this.onSecondaryTapOverride,
  });

  static const _statusIcons = {
    'todo': Icons.radio_button_unchecked,
    'in_progress': Icons.play_circle_outline,
    'review': Icons.rate_review_outlined,
    'done': Icons.check_circle_outline,
  };

  static const _statusColors = {
    'todo': Color(0xFF6B7280),
    'in_progress': Color(0xFF3B82F6),
    'review': Color(0xFFF59E0B),
    'done': Color(0xFF10B981),
  };

  @override
  Widget build(BuildContext context) {
    final isViewed = _isTaskViewed();
    final isActivePane = _isTaskActive();
    final statusIcon = _statusIcons[task.status] ?? Icons.help_outline;
    final statusColor = _statusColors[task.status] ?? context.appColors.textMuted;
    final sessionCount = task.sessions.length;
    final issueCount = state.linearIssuesForTask(task.taskId).length;

    final tile = GestureDetector(
      onSecondaryTapUp: (details) => onSecondaryTapOverride != null
          ? onSecondaryTapOverride!(details.globalPosition)
          : _showContextMenu(context, details.globalPosition),
      child: Container(
        decoration: BoxDecoration(
          color: isSelected
              ? context.appColors.accent.withAlpha(20)
              : isActivePane
                  ? context.appColors.accent.withAlpha(25)
                  : isViewed
                      ? context.appColors.accent.withAlpha(12)
                      : null,
          border: isActivePane
              ? Border(
                  left: BorderSide(
                      color: context.appColors.accent, width: 3))
              : isViewed
                  ? Border(
                      left: BorderSide(
                          color: context.appColors.accent.withAlpha(80),
                          width: 2))
                  : null,
        ),
        child: ListTile(
          leading: isSelected
              ? Container(
                  width: 30,
                  height: 30,
                  decoration: BoxDecoration(
                    color: context.appColors.accent.withAlpha(40),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Icon(Icons.check_box_rounded,
                      color: context.appColors.accent, size: 16),
                )
              : Container(
                  width: 30,
                  height: 30,
                  decoration: BoxDecoration(
                    color: statusColor.withAlpha(30),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Icon(statusIcon, color: statusColor, size: 16),
                ),
          title: Text(
            task.title,
            style: TextStyle(
              color: isActivePane
                  ? context.appColors.accentLight
                  : context.appColors.textPrimary,
              fontSize: 12,
              fontWeight: isActivePane ? FontWeight.w600 : FontWeight.w400,
            ),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          subtitle: Text(
            _subtitle(),
            style: TextStyle(
                color: context.appColors.textMuted, fontSize: 10),
          ),
          trailing: (sessionCount > 0 || issueCount > 0)
              ? Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (issueCount > 0)
                      Tooltip(
                        message: '$issueCount linked issue${issueCount == 1 ? '' : 's'}',
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 5, vertical: 2),
                          margin: const EdgeInsets.only(right: 4),
                          decoration: BoxDecoration(
                            color: const Color(0xFF8B5CF6).withAlpha(30),
                            borderRadius: BorderRadius.circular(8),
                          ),
                          child: Row(
                            mainAxisSize: MainAxisSize.min,
                            children: [
                              Icon(Icons.link_rounded,
                                  size: 9,
                                  color: const Color(0xFF8B5CF6)),
                              const SizedBox(width: 2),
                              Text(
                                '$issueCount',
                                style: const TextStyle(
                                  color: Color(0xFF8B5CF6),
                                  fontSize: 10,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ),
                    if (sessionCount > 0)
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 6, vertical: 2),
                        decoration: BoxDecoration(
                          color: context.appColors.accent.withAlpha(30),
                          borderRadius: BorderRadius.circular(8),
                        ),
                        child: Text(
                          '$sessionCount',
                          style: TextStyle(
                            color: context.appColors.accentLight,
                            fontSize: 10,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                      ),
                  ],
                )
              : null,
          dense: true,
          visualDensity: const VisualDensity(vertical: -4),
          contentPadding: const EdgeInsets.only(left: 16, right: 8),
          onTap: onTapOverride ?? () {
            state.openTaskInPane(task.taskId);
            onTaskSelected?.call();
          },
        ),
      ),
    );

    return Draggable<TaskDragData>(
      data: TaskDragData(
        taskId: task.taskId,
        workerId: task.workerId,
        label: task.title,
      ),
      feedback: Material(
        color: Colors.transparent,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          decoration: BoxDecoration(
            color: context.appColors.bgElevated,
            borderRadius: BorderRadius.circular(16),
            border:
                Border.all(color: context.appColors.accent.withAlpha(120)),
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
              Icon(statusIcon, color: statusColor, size: 14),
              const SizedBox(width: 6),
              ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 180),
                child: Text(
                  task.title,
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

  bool _isTaskViewed() {
    for (final pane in state.panes.values) {
      if (pane.taskId == task.taskId) return true;
    }
    return false;
  }

  bool _isTaskActive() {
    if (state.hasNoPanes) return false;
    return state.activePane.taskId == task.taskId;
  }

  String _subtitle() {
    final local = task.updatedAt.toLocal();
    final time = '${monthAbbr(local.month)} ${local.day}, '
        '${local.hour.toString().padLeft(2, '0')}:'
        '${local.minute.toString().padLeft(2, '0')}';
    final src = task.source == 'ai' ? 'AI' : 'User';
    return '$time \u00B7 $src \u00B7 ${task.status}';
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
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      items: [
        PopupMenuItem(
          value: 'start_session',
          child: Row(
            children: [
              Icon(Icons.play_arrow_rounded,
                  color: context.appColors.textSecondary, size: 18),
              const SizedBox(width: 8),
              Text('Start Session',
                  style: TextStyle(color: context.appColors.textPrimary)),
            ],
          ),
        ),
        const PopupMenuDivider(),
        if (task.status != 'done')
          PopupMenuItem(
            value: 'done',
            child: Row(
              children: [
                Icon(Icons.check_circle_outline,
                    color: const Color(0xFF10B981), size: 18),
                const SizedBox(width: 8),
                Text('Mark done',
                    style: TextStyle(color: context.appColors.textPrimary)),
              ],
            ),
          ),
        if (task.status == 'done')
          PopupMenuItem(
            value: 'reopen',
            child: Row(
              children: [
                Icon(Icons.replay_outlined,
                    color: context.appColors.textSecondary, size: 18),
                const SizedBox(width: 8),
                Text('Reopen',
                    style: TextStyle(color: context.appColors.textPrimary)),
              ],
            ),
          ),
        PopupMenuItem(
          value: 'delete',
          child: Row(
            children: [
              Icon(Icons.delete_outline,
                  color: context.appColors.errorText, size: 18),
              const SizedBox(width: 8),
              Text('Delete',
                  style: TextStyle(color: context.appColors.errorText)),
            ],
          ),
        ),
      ],
    ).then((value) {
      if (!context.mounted) return;
      if (value == 'start_session') {
        _startSession(context);
      } else if (value == 'done') {
        _updateStatus(context, 'done');
      } else if (value == 'reopen') {
        _updateStatus(context, 'todo');
      } else if (value == 'delete') {
        _confirmDeleteTask(context);
      }
    });
  }

  void _updateStatus(BuildContext context, String newStatus) async {
    final workerId = task.workerId;
    final worker = state.getWorker(workerId);
    if (worker == null) return;
    try {
      await worker.ws.updateTask(task.taskId, status: newStatus);
    } catch (e) {
      if (context.mounted) {
        state.addSystemMessage('Failed to update task: $e', isError: true);
      }
    }
  }

  void _startSession(BuildContext context) {
    // Open the task in the active pane, then start a session from it.
    final paneId = state.activePaneId;
    state.startSessionFromTask(paneId, task);
    onTaskSelected?.call();
  }

  void _confirmDeleteTask(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        title: Text('Delete Task',
            style: TextStyle(
                color: context.appColors.textPrimary, fontSize: 16)),
        content: Text(
          'Delete "${task.title}"? This cannot be undone.',
          style: TextStyle(
              color: context.appColors.textSecondary, fontSize: 14),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text('Cancel',
                style: TextStyle(color: context.appColors.textSecondary)),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: context.appColors.errorText,
            ),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Delete', style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;
    _deleteTask(context);
  }

  void _deleteTask(BuildContext context) async {
    final workerId = task.workerId;
    final worker = state.getWorker(workerId);
    if (worker == null) return;
    try {
      await worker.ws.deleteTask(task.taskId);
    } catch (e) {
      if (context.mounted) {
        state.addSystemMessage('Failed to delete task: $e', isError: true);
      }
    }
  }
}
