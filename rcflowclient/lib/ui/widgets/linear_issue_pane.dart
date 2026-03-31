import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../models/linear_issue_info.dart';
import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';

/// Full-pane detail view for a cached Linear issue.
///
/// Shows issue metadata, priority, state, description, assignee, labels,
/// and a button to open the issue in Linear's web app.
class LinearIssuePane extends StatelessWidget {
  final String paneId;
  final PaneState pane;

  const LinearIssuePane({super.key, required this.paneId, required this.pane});

  @override
  Widget build(BuildContext context) {
    final appState = context.watch<AppState>();
    final issueId = pane.linearIssueId;
    if (issueId == null) return _emptyState(context, appState);

    final issue = appState.getLinearIssue(issueId);
    if (issue == null) return _emptyState(context, appState);

    return ChangeNotifierProvider<PaneState>.value(
      value: pane,
      child: Column(
        children: [
          _LinearIssuePaneHeader(
            paneId: paneId,
            issue: issue,
            appState: appState,
          ),
          Expanded(
            child: _LinearIssueContent(issue: issue, appState: appState),
          ),
        ],
      ),
    );
  }

  Widget _emptyState(BuildContext context, AppState appState) {
    return Column(
      children: [
        _LinearIssuePaneHeader(paneId: paneId, issue: null, appState: appState),
        Expanded(
          child: Center(
            child: Text(
              'Issue not found',
              style: TextStyle(color: context.appColors.textMuted),
            ),
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

class _LinearIssuePaneHeader extends StatelessWidget {
  final String paneId;
  final LinearIssueInfo? issue;
  final AppState appState;

  const _LinearIssuePaneHeader({
    required this.paneId,
    required this.issue,
    required this.appState,
  });

  @override
  Widget build(BuildContext context) {
    final isActive = appState.activePaneId == paneId;

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
          const Icon(Icons.linear_scale, size: 14, color: Color(0xFF6366F1)),
          const SizedBox(width: 6),
          if (issue != null) ...[
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
                issue!.identifier,
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 10,
                  fontWeight: FontWeight.w600,
                  fontFamily: 'monospace',
                ),
              ),
            ),
            const SizedBox(width: 6),
          ],
          Expanded(
            child: Text(
              issue?.title ?? 'Linear Issue',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 12,
                fontWeight: FontWeight.w500,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (appState.paneCount > 1)
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
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Content
// ---------------------------------------------------------------------------

class _LinearIssueContent extends StatefulWidget {
  final LinearIssueInfo issue;
  final AppState appState;

  const _LinearIssueContent({required this.issue, required this.appState});

  @override
  State<_LinearIssueContent> createState() => _LinearIssueContentState();
}

class _LinearIssueContentState extends State<_LinearIssueContent> {
  bool _creatingTask = false;

  static const _priorityColors = {
    0: Color(0xFF6B7280),
    1: Color(0xFFEF4444),
    2: Color(0xFFF97316),
    3: Color(0xFFF59E0B),
    4: Color(0xFF6B7280),
  };

  static const _priorityIcons = {
    0: Icons.remove,
    1: Icons.keyboard_double_arrow_up,
    2: Icons.keyboard_arrow_up,
    3: Icons.drag_handle,
    4: Icons.keyboard_arrow_down,
  };

  static const _stateTypeColors = {
    'triage': Color(0xFF8B5CF6),
    'backlog': Color(0xFF6B7280),
    'unstarted': Color(0xFF6B7280),
    'started': Color(0xFF3B82F6),
    'completed': Color(0xFF10B981),
    'cancelled': Color(0xFF9CA3AF),
  };

  LinearIssueInfo get issue => widget.issue;
  AppState get appState => widget.appState;

  @override
  Widget build(BuildContext context) {
    final priorityColor =
        _priorityColors[issue.priority] ?? context.appColors.textMuted;
    final priorityIcon = _priorityIcons[issue.priority] ?? Icons.remove;
    final stateColor =
        _stateTypeColors[issue.stateType] ?? context.appColors.textMuted;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Title
          Text(
            issue.title,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 18,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 16),

          // Metadata row
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _MetadataChip(
                icon: Icon(priorityIcon, color: priorityColor, size: 14),
                label: issue.priorityLabel,
                color: priorityColor,
                context: context,
              ),
              _MetadataChip(
                icon: Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: stateColor,
                    shape: BoxShape.circle,
                  ),
                ),
                label: issue.stateName,
                color: stateColor,
                context: context,
              ),
              if (issue.assigneeName != null)
                _MetadataChip(
                  icon: Icon(
                    Icons.person_outline,
                    color: context.appColors.textMuted,
                    size: 14,
                  ),
                  label: issue.assigneeName!,
                  color: context.appColors.textMuted,
                  context: context,
                ),
              if (issue.teamName != null)
                _MetadataChip(
                  icon: Icon(
                    Icons.group_outlined,
                    color: context.appColors.textMuted,
                    size: 14,
                  ),
                  label: issue.teamName!,
                  color: context.appColors.textMuted,
                  context: context,
                ),
            ],
          ),

          if (issue.labels.isNotEmpty) ...[
            const SizedBox(height: 12),
            Wrap(
              spacing: 6,
              runSpacing: 6,
              children: issue.labels
                  .map(
                    (label) => Container(
                      padding: const EdgeInsets.symmetric(
                        horizontal: 8,
                        vertical: 3,
                      ),
                      decoration: BoxDecoration(
                        color: context.appColors.bgElevated,
                        borderRadius: BorderRadius.circular(10),
                        border: Border.all(
                          color: context.appColors.divider,
                          width: 0.5,
                        ),
                      ),
                      child: Text(
                        label,
                        style: TextStyle(
                          color: context.appColors.textSecondary,
                          fontSize: 11,
                        ),
                      ),
                    ),
                  )
                  .toList(),
            ),
          ],

          const SizedBox(height: 20),
          const Divider(height: 1),
          const SizedBox(height: 20),

          // Description
          if (issue.description != null && issue.description!.isNotEmpty) ...[
            Text(
              'Description',
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 13,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: 8),
            SelectableText(
              issue.description!,
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
                height: 1.5,
              ),
            ),
            const SizedBox(height: 20),
            const Divider(height: 1),
            const SizedBox(height: 20),
          ],

          // Timestamps
          _TimestampRow(
            label: 'Created',
            time: issue.createdAt,
            context: context,
          ),
          const SizedBox(height: 4),
          _TimestampRow(
            label: 'Updated',
            time: issue.updatedAt,
            context: context,
          ),
          const SizedBox(height: 4),
          _TimestampRow(
            label: 'Synced',
            time: issue.syncedAt,
            context: context,
          ),

          const SizedBox(height: 24),

          // Action buttons
          Row(
            children: [
              OutlinedButton.icon(
                onPressed: () => _copyUrl(context),
                icon: Icon(
                  Icons.copy_outlined,
                  size: 16,
                  color: context.appColors.textSecondary,
                ),
                label: Text(
                  'Copy URL',
                  style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 13,
                  ),
                ),
                style: OutlinedButton.styleFrom(
                  side: BorderSide(color: context.appColors.divider),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(8),
                  ),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 8,
                  ),
                ),
              ),
              const SizedBox(width: 8),
              if (issue.taskId == null)
                OutlinedButton.icon(
                  onPressed: _creatingTask
                      ? null
                      : () => _createTaskFromIssue(context),
                  icon: _creatingTask
                      ? SizedBox(
                          width: 14,
                          height: 14,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: context.appColors.accent,
                          ),
                        )
                      : Icon(
                          Icons.add_task,
                          size: 16,
                          color: context.appColors.accent,
                        ),
                  label: Text(
                    _creatingTask ? 'Creating…' : 'Create Task',
                    style: TextStyle(
                      color: context.appColors.accent,
                      fontSize: 13,
                    ),
                  ),
                  style: OutlinedButton.styleFrom(
                    side: BorderSide(
                      color: context.appColors.accent.withAlpha(120),
                    ),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(8),
                    ),
                    padding: const EdgeInsets.symmetric(
                      horizontal: 12,
                      vertical: 8,
                    ),
                  ),
                )
              else
                Tooltip(
                  message: 'Unlink from task',
                  child: OutlinedButton.icon(
                    onPressed: () => _unlinkTask(context),
                    icon: Icon(
                      Icons.link_off,
                      size: 16,
                      color: context.appColors.textMuted,
                    ),
                    label: Text(
                      'Unlink Task',
                      style: TextStyle(
                        color: context.appColors.textMuted,
                        fontSize: 13,
                      ),
                    ),
                    style: OutlinedButton.styleFrom(
                      side: BorderSide(color: context.appColors.divider),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(8),
                      ),
                      padding: const EdgeInsets.symmetric(
                        horizontal: 12,
                        vertical: 8,
                      ),
                    ),
                  ),
                ),
            ],
          ),
        ],
      ),
    );
  }

  void _copyUrl(BuildContext context) {
    Clipboard.setData(ClipboardData(text: issue.url));
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('URL copied'),
        duration: Duration(seconds: 2),
      ),
    );
  }

  Future<void> _createTaskFromIssue(BuildContext context) async {
    final worker = appState.getWorker(issue.workerId);
    if (worker == null || !worker.isConnected) {
      if (context.mounted) {
        appState.addSystemMessage(
          'No connected worker to create task on.',
          isError: true,
        );
      }
      return;
    }

    setState(() => _creatingTask = true);
    try {
      await worker.ws.createTaskFromLinearIssue(issue.id);
      // The WS broadcast from the server will update AppState automatically,
      // so no manual state update is needed here.
    } catch (e) {
      if (context.mounted) {
        appState.addSystemMessage(
          'Failed to create task from issue: $e',
          isError: true,
        );
      }
    } finally {
      if (mounted) setState(() => _creatingTask = false);
    }
  }

  void _unlinkTask(BuildContext context) async {
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
// Small helper widgets
// ---------------------------------------------------------------------------

class _MetadataChip extends StatelessWidget {
  final Widget icon;
  final String label;
  final Color color;
  final BuildContext context;

  const _MetadataChip({
    required this.icon,
    required this.label,
    required this.color,
    required this.context,
  });

  @override
  Widget build(BuildContext ctx) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: color.withAlpha(20),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withAlpha(60), width: 0.5),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          icon,
          const SizedBox(width: 5),
          Text(
            label,
            style: TextStyle(
              color: color,
              fontSize: 12,
              fontWeight: FontWeight.w500,
            ),
          ),
        ],
      ),
    );
  }
}

class _TimestampRow extends StatelessWidget {
  final String label;
  final DateTime time;
  final BuildContext context;

  const _TimestampRow({
    required this.label,
    required this.time,
    required this.context,
  });

  @override
  Widget build(BuildContext ctx) {
    final local = time.toLocal();
    final formatted =
        '${local.year}-${local.month.toString().padLeft(2, '0')}-${local.day.toString().padLeft(2, '0')} '
        '${local.hour.toString().padLeft(2, '0')}:${local.minute.toString().padLeft(2, '0')}';
    return Row(
      children: [
        SizedBox(
          width: 60,
          child: Text(
            label,
            style: TextStyle(color: context.appColors.textMuted, fontSize: 11),
          ),
        ),
        Text(
          formatted,
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 11,
          ),
        ),
      ],
    );
  }
}
