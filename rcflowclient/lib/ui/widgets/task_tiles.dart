part of 'task_pane.dart';

class _SessionRefTile extends StatelessWidget {
  final TaskSessionRef ref;
  final AppState appState;
  final String taskId;
  final String workerId;

  const _SessionRefTile({
    required this.ref,
    required this.appState,
    required this.taskId,
    required this.workerId,
  });

  @override
  Widget build(BuildContext context) {
    final title = ref.title ?? _shortId(ref.sessionId);
    final isTerminal = {
      'completed',
      'failed',
      'cancelled',
    }.contains(ref.status);

    return GestureDetector(
      onSecondaryTapUp: (details) =>
          _showContextMenu(context, details.globalPosition),
      child: Container(
        margin: const EdgeInsets.only(bottom: 4),
        decoration: BoxDecoration(
          color: context.appColors.bgElevated,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Material(
          type: MaterialType.transparency,
          child: ListTile(
          dense: true,
          visualDensity: const VisualDensity(vertical: -3),
          leading: Icon(
            isTerminal ? Icons.check_circle_outline : Icons.play_circle_outline,
            color: isTerminal
                ? context.appColors.textMuted
                : context.appColors.accentLight,
            size: 18,
          ),
          title: Text(
            title,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 12,
              fontWeight: FontWeight.w500,
            ),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          subtitle: Text(
            ref.status,
            style: TextStyle(color: context.appColors.textMuted, fontSize: 10),
          ),
          onTap: () {
            appState.ensureChatPane().switchSession(ref.sessionId);
          },
        ),
        ),
      ),
    );
  }

  void _showContextMenu(BuildContext context, Offset position) {
    final overlay = Overlay.of(context).context.findRenderObject() as RenderBox;
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
          value: 'open',
          child: Row(
            children: [
              Icon(
                Icons.open_in_new_rounded,
                color: context.appColors.textSecondary,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Open',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
        PopupMenuItem(
          value: 'open_split',
          child: Row(
            children: [
              Icon(
                Icons.vertical_split_outlined,
                color: context.appColors.textSecondary,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Open in Split',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
        PopupMenuItem(
          value: 'detach',
          child: Row(
            children: [
              Icon(
                Icons.link_off_rounded,
                color: context.appColors.errorText,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Detach session',
                style: TextStyle(color: context.appColors.errorText),
              ),
            ],
          ),
        ),
      ],
    ).then((value) {
      if (!context.mounted) return;
      if (value == 'open') {
        appState.ensureChatPane().switchSession(ref.sessionId);
      } else if (value == 'open_split') {
        appState.splitPaneWithSession(
          appState.activePaneId,
          DropZone.right,
          ref.sessionId,
        );
      } else if (value == 'detach') {
        _detachSession(context);
      }
    });
  }

  void _detachSession(BuildContext context) async {
    final worker = appState.getWorker(workerId);
    if (worker == null) return;
    try {
      await worker.ws.detachSessionFromTask(taskId, ref.sessionId);
    } catch (e) {
      if (context.mounted) {
        appState.addSystemMessage(
          'Failed to detach session: $e',
          isError: true,
        );
      }
    }
  }

  static String _shortId(String id) {
    if (id.length > 8) return id.substring(0, 8);
    return id;
  }
}

// ---------------------------------------------------------------------------
// Linked issue tile (inside task detail)
// ---------------------------------------------------------------------------

class _LinkedIssueTile extends StatelessWidget {
  final LinearIssueInfo issue;
  final AppState appState;
  final String taskId;

  const _LinkedIssueTile({
    required this.issue,
    required this.appState,
    required this.taskId,
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
    final priorityIcon = _priorityIcons[issue.priority] ?? Icons.remove;
    final priorityColor =
        _priorityColors[issue.priority] ?? context.appColors.textMuted;
    final stateColor =
        _stateTypeColors[issue.stateType] ?? context.appColors.textMuted;

    return GestureDetector(
      onSecondaryTapUp: (details) =>
          _showContextMenu(context, details.globalPosition),
      child: Container(
        margin: const EdgeInsets.only(bottom: 4),
        decoration: BoxDecoration(
          color: context.appColors.bgElevated,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Material(
          type: MaterialType.transparency,
          child: ListTile(
          dense: true,
          visualDensity: const VisualDensity(vertical: -3),
          leading: Container(
            width: 28,
            height: 28,
            decoration: BoxDecoration(
              color: stateColor.withAlpha(30),
              borderRadius: BorderRadius.circular(kRadiusSmall),
            ),
            child: Icon(priorityIcon, color: priorityColor, size: 14),
          ),
          title: Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: kSpace1, vertical: 1),
                decoration: BoxDecoration(
                  color: context.appColors.bgBase,
                  borderRadius: BorderRadius.circular(4),
                  border: Border.all(
                    color: context.appColors.divider,
                    width: 0.5,
                  ),
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
                    color: context.appColors.textPrimary,
                    fontSize: 12,
                    fontWeight: FontWeight.w500,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          subtitle: Text(
            issue.stateName,
            style: TextStyle(color: context.appColors.textMuted, fontSize: 10),
          ),
          onTap: () => appState.openLinearIssueInPane(issue.id),
        ),
        ),
      ),
    );
  }

  void _showContextMenu(BuildContext context, Offset position) {
    final overlay = Overlay.of(context).context.findRenderObject() as RenderBox;
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
          value: 'open',
          child: Row(
            children: [
              Icon(
                Icons.open_in_new_rounded,
                color: context.appColors.textSecondary,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Open issue',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
        PopupMenuItem(
          value: 'unlink',
          child: Row(
            children: [
              Icon(
                Icons.link_off_rounded,
                color: context.appColors.errorText,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Unlink from task',
                style: TextStyle(color: context.appColors.errorText),
              ),
            ],
          ),
        ),
      ],
    ).then((value) {
      if (!context.mounted) return;
      if (value == 'open') {
        appState.openLinearIssueInPane(issue.id);
      } else if (value == 'unlink') {
        _unlinkIssue(context);
      }
    });
  }

  void _unlinkIssue(BuildContext context) async {
    final worker = appState.getWorker(issue.workerId);
    if (worker == null) return;
    try {
      await worker.ws.unlinkLinearIssueFromTask(issue.id);
    } catch (e) {
      if (context.mounted) {
        appState.addSystemMessage('Failed to unlink issue: $e', isError: true);
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Link issue picker dialog
// ---------------------------------------------------------------------------
