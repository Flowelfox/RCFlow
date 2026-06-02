part of 'task_pane.dart';

class _TaskPaneHeader extends StatelessWidget {
  final String paneId;
  final TaskInfo? task;
  final AppState appState;
  final bool isActive;
  final bool multiPane;
  final VoidCallback? onEditPressed;

  const _TaskPaneHeader({
    required this.paneId,
    required this.task,
    required this.appState,
    required this.isActive,
    required this.multiPane,
    required this.onEditPressed,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 32,
      decoration: BoxDecoration(
        color: isActive
            ? context.appColors.accent.withAlpha(20)
            : context.appColors.bgSurface,
        border: Border(bottom: BorderSide(color: context.appColors.divider)),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: Row(
        children: [
          if (appState.panes[paneId]?.canGoBack ?? false)
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.arrow_back_rounded,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Back',
                onPressed: () => appState.goBack(paneId),
              ),
            ),
          if (isActive)
            Container(
              width: 6,
              height: 6,
              margin: const EdgeInsets.only(right: 6),
              decoration: BoxDecoration(
                color: context.appColors.accent,
                shape: BoxShape.circle,
              ),
            ),
          Icon(
            Icons.task_outlined,
            color: context.appColors.textMuted,
            size: 14,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              task?.title ?? 'Task',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 12,
                fontWeight: FontWeight.w500,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (task case final t?) ...[
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.edit_outlined,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Edit',
                onPressed: onEditPressed,
              ),
            ),
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.delete_outline,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Delete',
                onPressed: () => _confirmDeleteTask(context, t, appState),
              ),
            ),
            if (t.planArtifactId != null)
              SizedBox(
                width: 26,
                height: 26,
                child: IconButton(
                  padding: EdgeInsets.zero,
                  icon: const Icon(
                    Icons.description_outlined,
                    color: Color(0xFF10B981),
                    size: 14,
                  ),
                  tooltip: 'Open plan',
                  onPressed: () =>
                      appState.openArtifactInPane(t.planArtifactId!),
                ),
              ),
          ],
          if (multiPane) ...[
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.vertical_split_outlined,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Split',
                onPressed: () =>
                    appState.splitPane(paneId, SplitAxis.horizontal),
              ),
            ),
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.close_rounded,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Close',
                onPressed: () => appState.closePane(paneId),
              ),
            ),
          ] else
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.close_rounded,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Close task view',
                onPressed: () => appState.closeTaskView(paneId),
              ),
            ),
        ],
      ),
    );
  }

  void _confirmDeleteTask(
    BuildContext context,
    TaskInfo task,
    AppState appState,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(kRadiusLarge)),
        title: Text(
          'Delete Task',
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 16),
        ),
        content: Text(
          'Delete "${task.title}"? This cannot be undone.',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 14,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text(
              'Cancel',
              style: TextStyle(color: context.appColors.textSecondary),
            ),
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
    if (confirmed != true) return;
    final worker = appState.getWorker(task.workerId);
    if (worker == null) return;
    try {
      await worker.ws.deleteTask(task.taskId);
    } catch (e) {
      if (context.mounted) {
        appState.addSystemMessage('Failed to delete task: $e', isError: true);
      }
    }
  }
}
