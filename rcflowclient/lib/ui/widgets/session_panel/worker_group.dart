import 'package:flutter/material.dart';

import '../../../models/session_info.dart';
import '../../../models/split_tree.dart';
import '../../../models/worker_config.dart';
import '../../../services/worker_connection.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import '../../dialogs/worker_edit_dialog.dart';
import '../session_pane.dart';
import '../terminal_pane.dart';
import '../worker_stats_pane.dart';
import 'helpers.dart';
import 'session_leading_icon.dart';
import 'terminal_session_tile.dart';

class WorkerGroup extends StatefulWidget {
  final WorkerConfig config;
  final WorkerConnection? worker;
  final List<SessionInfo> sessions;
  final List<TerminalSessionInfo> terminals;
  final bool expanded;
  final bool groupByProject;
  final VoidCallback onToggleExpand;
  final void Function(String sessionId) onSessionTap;
  final AppState state;
  final VoidCallback? onSessionSelected;

  /// Sessions currently selected in the workers tab. Used for highlight and
  /// routing secondary taps to the bulk context menu.
  final Set<String> selectedSessionIds;

  /// The global flat visible list of sessions used for Shift+click index
  /// resolution. Owned by the parent; passed by reference.
  final List<SessionInfo> currentFlatList;

  /// Callback invoked when a session tile is tapped. The parent handles all
  /// modifier-key logic (Shift, Ctrl/Meta) and selection state updates.
  final void Function(String sessionId, int flatIndex) onSessionSelectTap;

  /// Callback invoked on a secondary (right-click) tap when the selection
  /// set is non-empty. The parent shows the bulk context menu. When the
  /// selection is empty the normal per-session context menu is shown instead.
  final void Function(String sessionId, Offset globalPosition)?
  onBulkSecondaryTap;

  /// Collapsed project sub-groups for this worker (keyed by project name or
  /// '\x00other'). Owned by the parent and passed by reference.
  final Set<String> collapsedProjects;

  /// Callback to toggle a project sub-group's collapsed state.
  final void Function(String collapseKey) onProjectToggle;

  const WorkerGroup({
    super.key,
    required this.config,
    required this.worker,
    required this.sessions,
    required this.terminals,
    required this.expanded,
    required this.onToggleExpand,
    required this.onSessionTap,
    required this.state,
    required this.onSessionSelected,
    required this.selectedSessionIds,
    required this.currentFlatList,
    required this.onSessionSelectTap,
    required this.collapsedProjects,
    required this.onProjectToggle,
    this.onBulkSecondaryTap,
    this.groupByProject = false,
  });

  @override
  State<WorkerGroup> createState() => _WorkerGroupState();
}

class _WorkerGroupState extends State<WorkerGroup> {
  WorkerConfig get config => widget.config;
  WorkerConnection? get worker => widget.worker;
  List<SessionInfo> get sessions => widget.sessions;
  List<TerminalSessionInfo> get terminals => widget.terminals;
  bool get expanded => widget.expanded;
  bool get groupByProject => widget.groupByProject;
  VoidCallback get onToggleExpand => widget.onToggleExpand;
  void Function(String) get onSessionTap => widget.onSessionTap;
  AppState get state => widget.state;
  VoidCallback? get onSessionSelected => widget.onSessionSelected;

  @override
  Widget build(BuildContext context) {
    final status = worker?.status ?? WorkerConnectionStatus.disconnected;
    final isConnected = status == WorkerConnectionStatus.connected;
    final statusColor = switch (status) {
      WorkerConnectionStatus.connected => context.appColors.successText,
      WorkerConnectionStatus.connecting => context.appColors.toolAccent,
      WorkerConnectionStatus.reconnecting => context.appColors.toolAccent,
      WorkerConnectionStatus.disconnected => context.appColors.errorText,
    };

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Header
        LayoutBuilder(
          builder: (context, constraints) {
            // At narrow sidebar widths, collapse the refresh + terminal buttons
            // into the right-click context menu to prevent the worker name text
            // from being squeezed into an unreadable sliver.
            final isNarrow = constraints.maxWidth < 200;
            return GestureDetector(
              onSecondaryTapUp: (details) =>
                  _showWorkerContextMenu(context, details.globalPosition),
              child: InkWell(
                onTap: onToggleExpand,
                child: Padding(
                  padding: EdgeInsets.symmetric(horizontal: 16, vertical: 6),
                  child: Row(
                    children: [
                      Icon(
                        expanded
                            ? Icons.expand_more_rounded
                            : Icons.chevron_right_rounded,
                        color: context.appColors.textSecondary,
                        size: 18,
                      ),
                      SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          '${config.name} (${sessions.length + terminals.length})',
                          style: TextStyle(
                            color: context.appColors.textPrimary,
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                          ),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      if (isConnected) ...[
                        if (!isNarrow) ...[
                          SizedBox(
                            width: 24,
                            height: 24,
                            child: IconButton(
                              padding: EdgeInsets.zero,
                              icon: Icon(
                                Icons.refresh_rounded,
                                color: context.appColors.textSecondary,
                                size: 14,
                              ),
                              onPressed: () => worker?.refreshSessions(),
                              tooltip: 'Refresh sessions',
                              constraints: const BoxConstraints(
                                maxWidth: 24,
                                maxHeight: 24,
                              ),
                            ),
                          ),
                          SizedBox(
                            width: 24,
                            height: 24,
                            child: IconButton(
                              padding: EdgeInsets.zero,
                              icon: Icon(
                                Icons.terminal_rounded,
                                color: context.appColors.textSecondary,
                                size: 14,
                              ),
                              onPressed: () {
                                state.openTerminal(config.id);
                                onSessionSelected?.call();
                              },
                              tooltip: 'Open terminal',
                              constraints: const BoxConstraints(
                                maxWidth: 24,
                                maxHeight: 24,
                              ),
                            ),
                          ),
                        ],
                        SizedBox(
                          width: 24,
                          height: 24,
                          child: IconButton(
                            padding: EdgeInsets.zero,
                            icon: Icon(
                              Icons.add_rounded,
                              color: context.appColors.textSecondary,
                              size: 14,
                            ),
                            onPressed: () {
                              final pane = state.ensureChatPane();
                              pane.setTargetWorker(config.id);
                              pane.startNewChat();
                              onSessionSelected?.call();
                            },
                            tooltip: 'New session',
                            constraints: const BoxConstraints(
                              maxWidth: 24,
                              maxHeight: 24,
                            ),
                          ),
                        ),
                      ],
                      const SizedBox(width: 4),
                      Container(
                        width: 8,
                        height: 8,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: statusColor,
                          boxShadow: [
                            BoxShadow(
                              color: statusColor.withAlpha(80),
                              blurRadius: 4,
                              spreadRadius: 1,
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            );
          },
        ),
        // Merged sessions and terminals, sorted by date
        if (expanded) ..._buildMergedSessionList(context, isConnected),
        if (expanded && sessions.isEmpty && terminals.isEmpty)
          Padding(
            padding: EdgeInsets.only(left: 44, bottom: 4),
            child: Text(
              isConnected ? 'No sessions' : 'Disconnected',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 11,
              ),
            ),
          ),
      ],
    );
  }

  List<Widget> _buildMergedSessionList(BuildContext context, bool isConnected) {
    if (groupByProject) {
      return _buildProjectGroupedSessionList(context, isConnected);
    }

    final entries = <({DateTime time, bool isTerminal, dynamic data})>[];
    for (final s in sessions) {
      entries.add((
        time: s.createdAt ?? DateTime(2000),
        isTerminal: false,
        data: s,
      ));
    }
    for (final t in terminals) {
      entries.add((time: t.createdAt, isTerminal: true, data: t));
    }
    entries.sort((a, b) => b.time.compareTo(a.time));

    return entries.map((entry) {
      if (entry.isTerminal) {
        final t = entry.data as TerminalSessionInfo;
        return TerminalSessionTile(
          info: t,
          state: state,
          isConnected: isConnected,
          onSessionSelected: onSessionSelected,
        );
      }
      final s = entry.data as SessionInfo;
      return _buildSessionTile(context, s, isConnected);
    }).toList();
  }

  /// Builds session list grouped by project when [groupByProject] is true.
  /// Terminals are shown at the top (ungrouped). Sessions are organized into
  /// collapsible project sub-categories below.
  List<Widget> _buildProjectGroupedSessionList(
    BuildContext context,
    bool isConnected,
  ) {
    final result = <Widget>[];

    // Terminals first, sorted by date
    final sortedTerminals = [...terminals]
      ..sort((a, b) => b.createdAt.compareTo(a.createdAt));
    for (final t in sortedTerminals) {
      result.add(
        TerminalSessionTile(
          info: t,
          state: state,
          isConnected: isConnected,
          onSessionSelected: onSessionSelected,
        ),
      );
    }

    // Group sessions by project name (last path segment of mainProjectPath)
    final byProject = <String?, List<SessionInfo>>{};
    for (final s in sessions) {
      final projectName = s.mainProjectPath
          ?.split('/')
          .where((p) => p.isNotEmpty)
          .lastOrNull;
      byProject.putIfAbsent(projectName, () => []).add(s);
    }

    // Sort project names: named projects alphabetically, null ("Other") last
    final projectNames = byProject.keys.toList()
      ..sort((a, b) {
        if (a == null && b == null) return 0;
        if (a == null) return 1;
        if (b == null) return -1;
        return a.toLowerCase().compareTo(b.toLowerCase());
      });

    for (final projectName in projectNames) {
      final projectSessions = byProject[projectName]!
        ..sort(
          (a, b) => (b.createdAt ?? DateTime(2000)).compareTo(
            a.createdAt ?? DateTime(2000),
          ),
        );
      final collapseKey = projectName ?? '\x00other';
      final collapsed = widget.collapsedProjects.contains(collapseKey);

      result.add(
        _buildProjectSubHeader(
          context,
          projectName: projectName,
          count: projectSessions.length,
          collapsed: collapsed,
          onToggle: () => widget.onProjectToggle(collapseKey),
        ),
      );

      if (!collapsed) {
        for (final s in projectSessions) {
          result.add(
            _buildSessionTile(context, s, isConnected, extraIndent: true),
          );
        }
      }
    }

    return result;
  }

  Widget _buildProjectSubHeader(
    BuildContext context, {
    required String? projectName,
    required int count,
    required bool collapsed,
    required VoidCallback onToggle,
  }) {
    return InkWell(
      onTap: onToggle,
      child: Padding(
        padding: const EdgeInsets.only(left: 20, right: 16, top: 4, bottom: 4),
        child: Row(
          children: [
            Icon(
              collapsed
                  ? Icons.chevron_right_rounded
                  : Icons.expand_more_rounded,
              color: context.appColors.textMuted,
              size: 14,
            ),
            const SizedBox(width: 4),
            Icon(
              projectName != null
                  ? Icons.folder_outlined
                  : Icons.folder_off_outlined,
              color: context.appColors.textMuted,
              size: 12,
            ),
            const SizedBox(width: 4),
            Expanded(
              child: Text(
                '${projectName ?? 'Other'} ($count)',
                style: TextStyle(
                  color: context.appColors.textSecondary,
                  fontSize: 10,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 0.4,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildSessionTile(
    BuildContext context,
    SessionInfo s,
    bool isConnected, {
    bool extraIndent = false,
  }) {
    final isActiveSession =
        !state.hasNoPanes && s.sessionId == state.activePane.sessionId;
    final isViewedByAnyPane = state.isSessionViewed(s.sessionId);
    final isSelected = widget.selectedSessionIds.contains(s.sessionId);
    final dimmed = !isConnected;
    final dragData = SessionDragData(
      sessionId: s.sessionId,
      workerId: config.id,
      label: s.title ?? s.shortId,
    );
    final tile = GestureDetector(
      onSecondaryTapUp: (details) {
        if (widget.selectedSessionIds.isNotEmpty &&
            widget.onBulkSecondaryTap != null) {
          // Add this session to the selection if not already selected, then
          // show the bulk context menu (mirrors tasks tab behaviour).
          if (!widget.selectedSessionIds.contains(s.sessionId)) {
            final flatIdx = widget.currentFlatList.indexOf(s);
            widget.onSessionSelectTap(s.sessionId, flatIdx);
          }
          widget.onBulkSecondaryTap!(s.sessionId, details.globalPosition);
        } else {
          _showContextMenu(context, details.globalPosition, state, s);
        }
      },
      child: Opacity(
        opacity: dimmed ? 0.5 : 1.0,
        child: LayoutBuilder(
          builder: (context, constraints) {
            // At narrow sidebar widths show only the primary action button so
            // the session title text is not squeezed against the trailing row.
            // Secondary actions remain reachable via the right-click context menu.
            final isNarrow = constraints.maxWidth < 180;
            return Container(
              decoration: BoxDecoration(
                color: isSelected
                    ? context.appColors.accent.withAlpha(35)
                    : isActiveSession
                    ? context.appColors.accent.withAlpha(25)
                    : isViewedByAnyPane
                    ? context.appColors.accent.withAlpha(12)
                    : null,
                border: isSelected
                    ? Border(
                        left: BorderSide(
                          color: context.appColors.accent.withAlpha(160),
                          width: 3,
                        ),
                      )
                    : isActiveSession
                    ? Border(
                        left: BorderSide(
                          color: context.appColors.accent,
                          width: 3,
                        ),
                      )
                    : isViewedByAnyPane
                    ? Border(
                        left: BorderSide(
                          color: context.appColors.accent.withAlpha(80),
                          width: 2,
                        ),
                      )
                    : null,
              ),
              child: ListTile(
                leading: SessionLeadingIcon(session: s),
                title: Text(
                  s.title ?? s.shortId,
                  style: TextStyle(
                    color: isActiveSession
                        ? context.appColors.accentLight
                        : context.appColors.textPrimary,
                    fontSize: 12,
                    fontWeight: isActiveSession
                        ? FontWeight.w600
                        : FontWeight.w400,
                    fontFamily: s.title != null ? null : 'monospace',
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                subtitle: _buildSubtitle(context, s),
                trailing: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (!isNarrow && state.isSessionAttachedToTask(s.sessionId))
                      Padding(
                        padding: const EdgeInsets.only(right: 2),
                        child: Icon(
                          Icons.link_rounded,
                          color: context.appColors.accent.withAlpha(120),
                          size: 14,
                        ),
                      ),
                    if (isTerminalStatus(s.status))
                      SizedBox(
                        width: 26,
                        height: 26,
                        child: IconButton(
                          padding: EdgeInsets.zero,
                          icon: Icon(
                            Icons.restore_rounded,
                            color: context.appColors.accentLight,
                            size: 16,
                          ),
                          tooltip: 'Restore session',
                          onPressed: dimmed
                              ? null
                              : () => state.restoreSessionDirect(
                                  s.sessionId,
                                  s.workerId,
                                ),
                        ),
                      )
                    else ...[
                      // At narrow widths keep only the primary action (play/pause).
                      // Stop is always shown; use right-click to access the other.
                      if (!isNarrow) ...[
                        if (s.status == 'paused')
                          SizedBox(
                            width: 26,
                            height: 26,
                            child: IconButton(
                              padding: EdgeInsets.zero,
                              icon: Icon(
                                Icons.play_arrow_rounded,
                                color: context.appColors.accentLight,
                                size: 16,
                              ),
                              tooltip: 'Resume session',
                              onPressed: dimmed
                                  ? null
                                  : () => state.resumeSessionDirect(
                                      s.sessionId,
                                      s.workerId,
                                    ),
                            ),
                          )
                        else
                          SizedBox(
                            width: 26,
                            height: 26,
                            child: IconButton(
                              padding: EdgeInsets.zero,
                              icon: Icon(
                                Icons.pause_rounded,
                                color: context.appColors.textSecondary,
                                size: 16,
                              ),
                              tooltip: 'Pause session',
                              onPressed: dimmed
                                  ? null
                                  : () => state.pauseSessionDirect(
                                      s.sessionId,
                                      s.workerId,
                                    ),
                            ),
                          ),
                      ],
                      SizedBox(
                        width: 26,
                        height: 26,
                        child: IconButton(
                          padding: EdgeInsets.zero,
                          icon: Icon(
                            Icons.stop_circle_outlined,
                            color: context.appColors.errorText,
                            size: 16,
                          ),
                          tooltip: 'End session',
                          onPressed: dimmed
                              ? null
                              : () => _confirmCancelSession(context, state, s),
                        ),
                      ),
                    ],
                  ],
                ),
                dense: true,
                visualDensity: const VisualDensity(vertical: -4),
                contentPadding: EdgeInsets.only(
                  left: extraIndent ? 48 : 36,
                  right: 8,
                ),
                onTap: () {
                  final flatIdx = widget.currentFlatList.indexOf(s);
                  widget.onSessionSelectTap(s.sessionId, flatIdx);
                },
                onLongPress: () => _showRenameDialog(context, state, s),
              ),
            );
          },
        ),
      ),
    );
    return Draggable<SessionDragData>(
      data: dragData,
      feedback: Material(
        color: Colors.transparent,
        child: Container(
          padding: EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          decoration: BoxDecoration(
            color: context.appColors.bgElevated,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: context.appColors.accent.withAlpha(120)),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withAlpha(100),
                blurRadius: 8,
                offset: Offset(0, 2),
              ),
            ],
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                Icons.drag_indicator_rounded,
                color: context.appColors.textSecondary,
                size: 14,
              ),
              SizedBox(width: 6),
              ConstrainedBox(
                constraints: BoxConstraints(maxWidth: 180),
                child: Text(
                  dragData.label,
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

  Widget _buildSubtitle(BuildContext context, SessionInfo s) {
    final dateStr = s.createdAt != null
        ? () {
            final local = s.createdAt!.toLocal();
            return '${monthAbbr(local.month)} ${local.day}, '
                '${local.hour.toString().padLeft(2, '0')}:'
                '${local.minute.toString().padLeft(2, '0')}';
          }()
        : null;

    final projectName = s.mainProjectPath
        ?.split('/')
        .where((p) => p.isNotEmpty)
        .lastOrNull;

    final mutedStyle = TextStyle(
      color: context.appColors.textMuted,
      fontSize: 10,
    );

    if (projectName == null) {
      return Text(dateStr ?? '', style: mutedStyle);
    }

    return Row(
      children: [
        if (dateStr != null) ...[
          Text(dateStr, style: mutedStyle),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 4),
            child: Text('\u00B7', style: mutedStyle),
          ),
        ],
        Icon(
          Icons.folder_outlined,
          size: 9,
          color: context.appColors.textMuted,
        ),
        const SizedBox(width: 2),
        Flexible(
          child: Text(
            projectName,
            style: mutedStyle,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }

  void _showWorkerContextMenu(BuildContext context, Offset position) {
    final status = worker?.status ?? WorkerConnectionStatus.disconnected;
    final isConnected = status == WorkerConnectionStatus.connected;
    final isConnecting =
        status == WorkerConnectionStatus.connecting ||
        status == WorkerConnectionStatus.reconnecting;

    final overlay = Overlay.of(context).context.findRenderObject() as RenderBox;
    showMenu<String>(
      context: context,
      position: RelativeRect.fromRect(
        position & Size(1, 1),
        Offset.zero & overlay.size,
      ),
      color: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      items: [
        if (!isConnected && !isConnecting)
          PopupMenuItem(
            value: 'connect',
            child: Row(
              children: [
                Icon(
                  Icons.link_rounded,
                  color: context.appColors.accentLight,
                  size: 18,
                ),
                SizedBox(width: 8),
                Text(
                  'Connect',
                  style: TextStyle(color: context.appColors.textPrimary),
                ),
              ],
            ),
          ),
        if (isConnected)
          PopupMenuItem(
            value: 'disconnect',
            child: Row(
              children: [
                Icon(
                  Icons.link_off_rounded,
                  color: context.appColors.textSecondary,
                  size: 18,
                ),
                SizedBox(width: 8),
                Text(
                  'Disconnect',
                  style: TextStyle(color: context.appColors.textPrimary),
                ),
              ],
            ),
          ),
        if (isConnected)
          PopupMenuItem(
            value: 'stats',
            child: Row(
              children: [
                Icon(Icons.bar_chart_rounded, color: Colors.teal, size: 18),
                SizedBox(width: 8),
                Text(
                  'Stats',
                  style: TextStyle(color: context.appColors.textPrimary),
                ),
              ],
            ),
          ),
        PopupMenuItem(
          value: 'edit',
          child: Row(
            children: [
              Icon(
                Icons.edit_outlined,
                color: context.appColors.textSecondary,
                size: 18,
              ),
              SizedBox(width: 8),
              Text(
                'Edit Worker',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
        PopupMenuItem(
          value: 'remove',
          child: Row(
            children: [
              Icon(
                Icons.delete_outline,
                color: context.appColors.errorText,
                size: 18,
              ),
              SizedBox(width: 8),
              Text(
                'Remove',
                style: TextStyle(color: context.appColors.errorText),
              ),
            ],
          ),
        ),
      ],
    ).then((value) async {
      if (!context.mounted) return;
      switch (value) {
        case 'connect':
          state.connectWorker(config.id);
        case 'disconnect':
          state.disconnectWorker(config.id);
        case 'stats':
          if (worker != null) {
            await showWorkerStatsDialog(
              context,
              worker: worker!,
              workerName: config.name,
            );
          }
        case 'edit':
          final updated = await showWorkerEditDialog(
            context,
            existing: config,
            worker: worker,
          );
          if (updated != null && context.mounted) {
            state.updateWorker(updated);
          }
        case 'remove':
          _confirmRemoveWorker(context);
      }
    });
  }

  void _confirmRemoveWorker(BuildContext context) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text(
          'Remove worker?',
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 18),
        ),
        content: Text(
          'This will disconnect and remove "${config.name}".',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 14,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text(
              'Cancel',
              style: TextStyle(color: context.appColors.textSecondary),
            ),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: context.appColors.errorText,
            ),
            onPressed: () {
              Navigator.of(ctx).pop();
              state.removeWorker(config.id);
            },
            child: const Text('Remove', style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }

  void _showContextMenu(
    BuildContext context,
    Offset position,
    AppState state,
    SessionInfo session,
  ) {
    final overlay = Overlay.of(context).context.findRenderObject() as RenderBox;
    final linkedTasks = state.tasksForSession(session.sessionId);
    showMenu<String>(
      context: context,
      position: RelativeRect.fromRect(
        position & Size(1, 1),
        Offset.zero & overlay.size,
      ),
      color: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      items: [
        PopupMenuItem(
          value: 'rename',
          child: Row(
            children: [
              Icon(
                Icons.edit_outlined,
                color: context.appColors.textSecondary,
                size: 18,
              ),
              SizedBox(width: 8),
              Text(
                'Rename',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
        // Task view options for sessions linked to tasks
        for (final task in linkedTasks) ...[
          PopupMenuItem(
            value: 'open_task:${task.taskId}',
            child: Row(
              children: [
                Icon(
                  Icons.task_outlined,
                  color: context.appColors.accentLight,
                  size: 18,
                ),
                SizedBox(width: 8),
                Expanded(
                  child: Text(
                    linkedTasks.length == 1
                        ? 'Open Task'
                        : 'Open Task: ${task.title}',
                    style: TextStyle(color: context.appColors.textPrimary),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
          ),
          PopupMenuItem(
            value: 'open_task_split:${task.taskId}',
            child: Row(
              children: [
                Icon(
                  Icons.vertical_split_outlined,
                  color: context.appColors.accentLight,
                  size: 18,
                ),
                SizedBox(width: 8),
                Expanded(
                  child: Text(
                    linkedTasks.length == 1
                        ? 'Open Task in Split'
                        : 'Open Task in Split: ${task.title}',
                    style: TextStyle(color: context.appColors.textPrimary),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
          ),
        ],
        if (!isTerminalStatus(session.status) && session.status != 'paused')
          PopupMenuItem(
            value: 'pause',
            child: Row(
              children: [
                Icon(
                  Icons.pause_rounded,
                  color: context.appColors.accentLight,
                  size: 18,
                ),
                SizedBox(width: 8),
                Text(
                  'Pause session',
                  style: TextStyle(color: context.appColors.textPrimary),
                ),
              ],
            ),
          ),
        if (session.status == 'paused')
          PopupMenuItem(
            value: 'resume',
            child: Row(
              children: [
                Icon(
                  Icons.play_arrow_rounded,
                  color: context.appColors.accentLight,
                  size: 18,
                ),
                SizedBox(width: 8),
                Text(
                  'Resume session',
                  style: TextStyle(color: context.appColors.textPrimary),
                ),
              ],
            ),
          ),
        if (isTerminalStatus(session.status))
          PopupMenuItem(
            value: 'restore',
            child: Row(
              children: [
                Icon(
                  Icons.restore_rounded,
                  color: context.appColors.accentLight,
                  size: 18,
                ),
                SizedBox(width: 8),
                Text(
                  'Restore session',
                  style: TextStyle(color: context.appColors.textPrimary),
                ),
              ],
            ),
          ),
        if (!isTerminalStatus(session.status))
          PopupMenuItem(
            value: 'cancel',
            child: Row(
              children: [
                Icon(
                  Icons.stop_circle_outlined,
                  color: context.appColors.errorText,
                  size: 18,
                ),
                SizedBox(width: 8),
                Text(
                  'End session',
                  style: TextStyle(color: context.appColors.errorText),
                ),
              ],
            ),
          ),
      ],
    ).then((value) {
      if (!context.mounted || value == null) return;
      if (value == 'rename') {
        _showRenameDialog(context, state, session);
      } else if (value == 'pause') {
        state.pauseSessionDirect(session.sessionId, session.workerId);
      } else if (value == 'resume') {
        state.resumeSessionDirect(session.sessionId, session.workerId);
      } else if (value == 'restore') {
        state.restoreSessionDirect(session.sessionId, session.workerId);
      } else if (value == 'cancel') {
        _confirmCancelSession(context, state, session);
      } else if (value.startsWith('open_task_split:')) {
        final taskId = value.substring('open_task_split:'.length);
        state.splitPaneWithTask(state.activePaneId, DropZone.right, taskId);
      } else if (value.startsWith('open_task:')) {
        final taskId = value.substring('open_task:'.length);
        state.openTaskInPane(taskId);
      }
    });
  }

  void _confirmCancelSession(
    BuildContext context,
    AppState state,
    SessionInfo session,
  ) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text(
          'End session?',
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 18),
        ),
        content: Text(
          'This will end session ${session.shortId} on the server.',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 14,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text(
              'Keep',
              style: TextStyle(color: context.appColors.textSecondary),
            ),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: context.appColors.errorText,
            ),
            onPressed: () {
              Navigator.of(ctx).pop();
              onSessionSelected?.call();
              state.cancelSessionDirect(session.sessionId, session.workerId);
            },
            child: const Text(
              'End session',
              style: TextStyle(color: Colors.white),
            ),
          ),
        ],
      ),
    );
  }

  void _showRenameDialog(
    BuildContext context,
    AppState state,
    SessionInfo session,
  ) {
    final controller = TextEditingController(text: session.title ?? '');
    void submit(BuildContext ctx) {
      Navigator.of(ctx).pop();
      state.renameSessionDirect(
        session.sessionId,
        session.workerId,
        controller.text,
      );
    }

    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text(
          'Rename session',
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 18),
        ),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLength: 200,
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 14),
          decoration: InputDecoration(
            hintText: 'Session title',
            hintStyle: TextStyle(color: context.appColors.textMuted),
          ),
          onSubmitted: (_) => submit(ctx),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text(
              'Cancel',
              style: TextStyle(color: context.appColors.textSecondary),
            ),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: context.appColors.accent,
            ),
            onPressed: () => submit(ctx),
            child: const Text('Rename', style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }
}
