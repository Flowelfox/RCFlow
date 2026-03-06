import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/session_info.dart';
import '../../models/worker_config.dart';
import '../../services/worker_connection.dart';
import '../../state/app_state.dart';
import '../../theme.dart';
import '../dialogs/worker_edit_dialog.dart';
import 'session_pane.dart';
import 'settings_menu.dart';
import 'terminal_pane.dart';

/// Shows sessions as a modal bottom sheet (mobile).
void showSessionSheet(BuildContext context) {
  context.read<AppState>().refreshSessions();
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    builder: (_) => ChangeNotifierProvider.value(
      value: context.read<AppState>(),
      child: const _SessionSheetContent(),
    ),
  );
}

/// Reusable session list that works both inline (sidebar) and inside a sheet.
///
/// Sessions are grouped by worker in expandable sections.
class SessionListPanel extends StatefulWidget {
  final VoidCallback? onSessionSelected;

  const SessionListPanel({super.key, this.onSessionSelected});

  @override
  State<SessionListPanel> createState() => _SessionListPanelState();
}

class _SessionListPanelState extends State<SessionListPanel> {
  final Set<String> _expandedWorkers = {};
  bool _initialized = false;

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // Header – 39px + 1px divider = 40px to match CustomTitleBar
        SizedBox(
          height: 39,
          child: Padding(
            padding: EdgeInsets.fromLTRB(20, 0, 4, 0),
            child: Row(
              children: [
                Text('Workers',
                    style: TextStyle(
                        color: context.appColors.textPrimary,
                        fontSize: 18,
                        fontWeight: FontWeight.w600)),
                Spacer(),
                Consumer<AppState>(
                  builder: (context, state, _) {
                    final hiding = state.hideTerminalSessions;
                    return IconButton(
                      icon: Icon(
                        hiding ? Icons.filter_alt : Icons.filter_alt_outlined,
                        color: hiding ? context.appColors.accentLight : context.appColors.textSecondary,
                        size: 20,
                      ),
                      onPressed: () => state.toggleHideTerminalSessions(),
                      mouseCursor: SystemMouseCursors.click,
                      tooltip: hiding
                          ? 'Show all sessions'
                          : 'Hide finished sessions',
                      constraints: BoxConstraints(
                          maxWidth: 36, maxHeight: 36),
                      padding: EdgeInsets.zero,
                    );
                  },
                ),
                IconButton(
                  icon: Icon(Icons.add_rounded,
                      color: context.appColors.textSecondary, size: 20),
                  onPressed: () async {
                    final state = context.read<AppState>();
                    final config = await showWorkerEditDialog(
                      context,
                      sortOrder: state.workerConfigs.length,
                    );
                    if (config != null && context.mounted) {
                      state.addWorker(config);
                    }
                  },
                  mouseCursor: SystemMouseCursors.click,
                  tooltip: 'Add worker',
                  constraints: const BoxConstraints(
                      maxWidth: 36, maxHeight: 36),
                  padding: EdgeInsets.zero,
                ),
              ],
            ),
          ),
        ),
        const Divider(height: 1),
        // Tree view
        Expanded(
          child: Consumer<AppState>(
            builder: (context, state, _) {
              final configs = state.workerConfigs;

              // Auto-expand all workers on first build
              if (!_initialized) {
                _initialized = true;
                for (final c in configs) {
                  _expandedWorkers.add(c.id);
                }
              }

              if (configs.isEmpty) {
                return Center(
                  child: Text('No workers configured',
                      style: TextStyle(color: context.appColors.textMuted, fontSize: 14)),
                );
              }

              final grouped = state.sessionsByWorker;
              final terminalsByWorker = state.terminalsByWorker;

              return ListView.builder(
                padding: const EdgeInsets.symmetric(vertical: 8),
                itemCount: configs.length,
                itemBuilder: (context, index) {
                  final config = configs[index];
                  final worker = state.getWorker(config.id);
                  final sessions = grouped[config.id] ?? [];
                  final terminals = terminalsByWorker[config.id] ?? [];
                  final expanded = _expandedWorkers.contains(config.id);
                  return _WorkerGroup(
                    config: config,
                    worker: worker,
                    sessions: sessions,
                    terminals: terminals,
                    expanded: expanded,
                    onToggleExpand: () {
                      setState(() {
                        if (expanded) {
                          _expandedWorkers.remove(config.id);
                        } else {
                          _expandedWorkers.add(config.id);
                        }
                      });
                    },
                    onSessionTap: (sessionId) {
                      state.ensureChatPane().switchSession(sessionId);
                      widget.onSessionSelected?.call();
                    },
                    state: state,
                    onSessionSelected: widget.onSessionSelected,
                  );
                },
              );
            },
          ),
        ),
        if (defaultTargetPlatform != TargetPlatform.android) ...[
          const Divider(height: 1),
          // Bottom bar: Settings
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: InkWell(
              borderRadius: BorderRadius.circular(10),
              onTap: () => showSettingsMenu(context),
              child: Padding(
                padding: EdgeInsets.symmetric(horizontal: 8, vertical: 10),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.settings_outlined,
                        color: context.appColors.textMuted, size: 20),
                    SizedBox(width: 10),
                    Text('Settings',
                        style:
                            TextStyle(color: context.appColors.textSecondary, fontSize: 14)),
                  ],
                ),
              ),
            ),
          ),
        ],
      ],
    );
  }

}

// ---------------------------------------------------------------------------
// Worker group (expandable section)
// ---------------------------------------------------------------------------

class _WorkerGroup extends StatelessWidget {
  final WorkerConfig config;
  final WorkerConnection? worker;
  final List<SessionInfo> sessions;
  final List<TerminalSessionInfo> terminals;
  final bool expanded;
  final VoidCallback onToggleExpand;
  final void Function(String sessionId) onSessionTap;
  final AppState state;
  final VoidCallback? onSessionSelected;

  const _WorkerGroup({
    required this.config,
    required this.worker,
    required this.sessions,
    required this.terminals,
    required this.expanded,
    required this.onToggleExpand,
    required this.onSessionTap,
    required this.state,
    required this.onSessionSelected,
  });

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
        GestureDetector(
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
                    SizedBox(
                      width: 24,
                      height: 24,
                      child: IconButton(
                        padding: EdgeInsets.zero,
                        icon: Icon(Icons.refresh_rounded,
                            color: context.appColors.textSecondary, size: 14),
                        onPressed: () => worker?.refreshSessions(),
                        tooltip: 'Refresh sessions',
                        constraints: const BoxConstraints(
                            maxWidth: 24, maxHeight: 24),
                      ),
                    ),
                    SizedBox(
                      width: 24,
                      height: 24,
                      child: IconButton(
                        padding: EdgeInsets.zero,
                        icon: Icon(Icons.terminal_rounded,
                            color: context.appColors.textSecondary, size: 14),
                        onPressed: () {
                          state.openTerminal(config.id);
                          onSessionSelected?.call();
                        },
                        tooltip: 'Open terminal',
                        constraints: const BoxConstraints(
                            maxWidth: 24, maxHeight: 24),
                      ),
                    ),
                    SizedBox(
                      width: 24,
                      height: 24,
                      child: IconButton(
                        padding: EdgeInsets.zero,
                        icon: Icon(Icons.add_rounded,
                            color: context.appColors.textSecondary, size: 14),
                        onPressed: () {
                          final pane = state.ensureChatPane();
                          pane.setTargetWorker(config.id);
                          pane.startNewChat();
                          onSessionSelected?.call();
                        },
                        tooltip: 'New session',
                        constraints: const BoxConstraints(
                            maxWidth: 24, maxHeight: 24),
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
        ),
        // Merged sessions and terminals, sorted by date
        if (expanded)
          ..._buildMergedSessionList(context, isConnected),
        if (expanded && sessions.isEmpty && terminals.isEmpty)
          Padding(
            padding: EdgeInsets.only(left: 44, bottom: 4),
            child: Text(
              isConnected ? 'No sessions' : 'Disconnected',
              style: TextStyle(color: context.appColors.textMuted, fontSize: 11),
            ),
          ),
      ],
    );
  }

  /// Build a merged list of chat session tiles and terminal session tiles,
  /// sorted by creation date (newest first).
  List<Widget> _buildMergedSessionList(BuildContext context, bool isConnected) {
    // Create unified entries with a common sort key
    final entries = <({DateTime time, bool isTerminal, dynamic data})>[];
    for (final s in sessions) {
      entries.add((
        time: s.createdAt ?? DateTime(2000),
        isTerminal: false,
        data: s,
      ));
    }
    for (final t in terminals) {
      entries.add((
        time: t.createdAt,
        isTerminal: true,
        data: t,
      ));
    }
    // Sort newest first (matching the existing session sort order)
    entries.sort((a, b) => b.time.compareTo(a.time));

    return entries.map((entry) {
      if (entry.isTerminal) {
        final t = entry.data as TerminalSessionInfo;
        return _TerminalSessionTile(
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

  Widget _buildSessionTile(BuildContext context, SessionInfo s, bool isConnected) {
    final isActiveSession =
        !state.hasNoPanes && s.sessionId == state.activePane.sessionId;
    final isViewedByAnyPane = state.isSessionViewed(s.sessionId);
    final dimmed = !isConnected;
    final dragData = SessionDragData(
      sessionId: s.sessionId,
      workerId: config.id,
      label: s.title ?? s.shortId,
    );
    final tile = GestureDetector(
      onSecondaryTapUp: (details) => _showContextMenu(
          context, details.globalPosition, state, s),
      child: Opacity(
        opacity: dimmed ? 0.5 : 1.0,
        child: Container(
          decoration: BoxDecoration(
            color: isActiveSession
                ? context.appColors.accent.withAlpha(25)
                : isViewedByAnyPane
                    ? context.appColors.accent.withAlpha(12)
                    : null,
            border: isActiveSession
                ? Border(
                    left: BorderSide(color: context.appColors.accent, width: 3))
                : isViewedByAnyPane
                    ? Border(
                        left: BorderSide(
                            color: context.appColors.accent.withAlpha(80), width: 2))
                    : null,
          ),
          child: ListTile(
          leading: _SessionLeadingIcon(session: s),
          title: Text(
            s.title ?? s.shortId,
            style: TextStyle(
              color: isActiveSession ? context.appColors.accentLight : context.appColors.textPrimary,
              fontSize: 12,
              fontWeight:
                  isActiveSession ? FontWeight.w600 : FontWeight.w400,
              fontFamily: s.title != null ? null : 'monospace',
            ),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          subtitle: Text(
            _subtitleText(s),
            style: TextStyle(color: context.appColors.textMuted, fontSize: 10),
          ),
          trailing: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (_isTerminal(s.status))
                SizedBox(
                  width: 26,
                  height: 26,
                  child: IconButton(
                    padding: EdgeInsets.zero,
                    icon: Icon(Icons.restore_rounded,
                        color: context.appColors.accentLight, size: 16),
                    tooltip: 'Restore session',
                    onPressed: dimmed
                        ? null
                        : () => state.ensureChatPane()
                            .restoreSession(s.sessionId),
                  ),
                )
              else ...[
                if (s.status == 'paused')
                  SizedBox(
                    width: 26,
                    height: 26,
                    child: IconButton(
                      padding: EdgeInsets.zero,
                      icon: Icon(Icons.play_arrow_rounded,
                          color: context.appColors.accentLight, size: 16),
                      tooltip: 'Resume session',
                      onPressed: dimmed
                          ? null
                          : () => state.ensureChatPane()
                              .resumeSession(s.sessionId),
                    ),
                  )
                else
                  SizedBox(
                    width: 26,
                    height: 26,
                    child: IconButton(
                      padding: EdgeInsets.zero,
                      icon: Icon(Icons.pause_rounded,
                          color: context.appColors.textSecondary, size: 16),
                      tooltip: 'Pause session',
                      onPressed: dimmed
                          ? null
                          : () => state.ensureChatPane()
                              .pauseSession(s.sessionId),
                    ),
                  ),
                SizedBox(
                  width: 26,
                  height: 26,
                  child: IconButton(
                    padding: EdgeInsets.zero,
                    icon: Icon(Icons.stop_circle_outlined,
                        color: context.appColors.errorText, size: 16),
                    tooltip: 'End session',
                    onPressed: dimmed
                        ? null
                        : () => _confirmCancelSession(
                            context, state, s),
                  ),
                ),
              ],
            ],
          ),
          dense: true,
          visualDensity: const VisualDensity(vertical: -4),
          contentPadding: const EdgeInsets.only(left: 36, right: 8),
          onTap: () => onSessionTap(s.sessionId),
          onLongPress: () =>
              _showRenameDialog(context, state, s),
        ),
        ),
      ),
    );
    return Draggable<SessionDragData>(
      data: dragData,
      feedback: Material(
        color: Colors.transparent,
        child: Container(
          padding:
              EdgeInsets.symmetric(horizontal: 12, vertical: 6),
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
              Icon(Icons.drag_indicator_rounded,
                  color: context.appColors.textSecondary, size: 14),
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

  static String _subtitleText(SessionInfo s) {
    final parts = <String>[];
    if (s.createdAt != null) {
      final local = s.createdAt!.toLocal();
      parts.add('${_monthAbbr(local.month)} ${local.day}, '
          '${local.hour.toString().padLeft(2, '0')}:'
          '${local.minute.toString().padLeft(2, '0')}');
    }
    parts.add(s.shortId);
    return parts.join(' \u00B7 ');
  }

  static const _months = [
    '',
    'Jan',
    'Feb',
    'Mar',
    'Apr',
    'May',
    'Jun',
    'Jul',
    'Aug',
    'Sep',
    'Oct',
    'Nov',
    'Dec',
  ];

  static String _monthAbbr(int month) =>
      (month >= 1 && month <= 12) ? _months[month] : '???';

  void _showWorkerContextMenu(BuildContext context, Offset position) {
    final status = worker?.status ?? WorkerConnectionStatus.disconnected;
    final isConnected = status == WorkerConnectionStatus.connected;
    final isConnecting = status == WorkerConnectionStatus.connecting ||
        status == WorkerConnectionStatus.reconnecting;

    final overlay =
        Overlay.of(context).context.findRenderObject() as RenderBox;
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
                Icon(Icons.link_rounded, color: context.appColors.accentLight, size: 18),
                SizedBox(width: 8),
                Text('Connect', style: TextStyle(color: context.appColors.textPrimary)),
              ],
            ),
          ),
        if (isConnected)
          PopupMenuItem(
            value: 'disconnect',
            child: Row(
              children: [
                Icon(Icons.link_off_rounded,
                    color: context.appColors.textSecondary, size: 18),
                SizedBox(width: 8),
                Text('Disconnect', style: TextStyle(color: context.appColors.textPrimary)),
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
              Icon(Icons.delete_outline, color: context.appColors.errorText, size: 18),
              SizedBox(width: 8),
              Text('Remove', style: TextStyle(color: context.appColors.errorText)),
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
        case 'edit':
          final updated = await showWorkerEditDialog(context,
              existing: config, worker: worker);
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
        shape:
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text('Remove worker?',
            style: TextStyle(color: context.appColors.textPrimary, fontSize: 18)),
        content: Text(
          'This will disconnect and remove "${config.name}".',
          style: TextStyle(color: context.appColors.textSecondary, fontSize: 14),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text('Cancel',
                style: TextStyle(color: context.appColors.textSecondary)),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: context.appColors.errorText),
            onPressed: () {
              Navigator.of(ctx).pop();
              state.removeWorker(config.id);
            },
            child: const Text('Remove',
                style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }

  void _showContextMenu(BuildContext context, Offset position,
      AppState state, SessionInfo session) {
    final overlay =
        Overlay.of(context).context.findRenderObject() as RenderBox;
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
              Icon(Icons.edit_outlined, color: context.appColors.textSecondary, size: 18),
              SizedBox(width: 8),
              Text('Rename', style: TextStyle(color: context.appColors.textPrimary)),
            ],
          ),
        ),
        if (!_isTerminal(session.status) && session.status != 'paused')
          PopupMenuItem(
            value: 'pause',
            child: Row(
              children: [
                Icon(Icons.pause_rounded, color: context.appColors.accentLight, size: 18),
                SizedBox(width: 8),
                Text('Pause session',
                    style: TextStyle(color: context.appColors.textPrimary)),
              ],
            ),
          ),
        if (session.status == 'paused')
          PopupMenuItem(
            value: 'resume',
            child: Row(
              children: [
                Icon(Icons.play_arrow_rounded,
                    color: context.appColors.accentLight, size: 18),
                SizedBox(width: 8),
                Text('Resume session',
                    style: TextStyle(color: context.appColors.textPrimary)),
              ],
            ),
          ),
        if (_isTerminal(session.status))
          PopupMenuItem(
            value: 'restore',
            child: Row(
              children: [
                Icon(Icons.restore_rounded, color: context.appColors.accentLight, size: 18),
                SizedBox(width: 8),
                Text('Restore session',
                    style: TextStyle(color: context.appColors.textPrimary)),
              ],
            ),
          ),
        if (!_isTerminal(session.status))
          PopupMenuItem(
            value: 'cancel',
            child: Row(
              children: [
                Icon(Icons.stop_circle_outlined,
                    color: context.appColors.errorText, size: 18),
                SizedBox(width: 8),
                Text('End session',
                    style: TextStyle(color: context.appColors.errorText)),
              ],
            ),
          ),
      ],
    ).then((value) {
      if (!context.mounted) return;
      if (value == 'rename') {
        _showRenameDialog(context, state, session);
      } else if (value == 'pause') {
        state.ensureChatPane().pauseSession(session.sessionId);
      } else if (value == 'resume') {
        state.ensureChatPane().resumeSession(session.sessionId);
      } else if (value == 'restore') {
        state.ensureChatPane().restoreSession(session.sessionId);
      } else if (value == 'cancel') {
        _confirmCancelSession(context, state, session);
      }
    });
  }

  void _confirmCancelSession(
      BuildContext context, AppState state, SessionInfo session) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape:
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text('End session?',
            style: TextStyle(color: context.appColors.textPrimary, fontSize: 18)),
        content: Text(
          'This will end session ${session.shortId} on the server.',
          style: TextStyle(color: context.appColors.textSecondary, fontSize: 14),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text('Keep',
                style: TextStyle(color: context.appColors.textSecondary)),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: context.appColors.errorText),
            onPressed: () {
              Navigator.of(ctx).pop();
              onSessionSelected?.call();
              state.ensureChatPane().cancelSession(session.sessionId);
            },
            child: const Text('End session',
                style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }

  void _showRenameDialog(
      BuildContext context, AppState state, SessionInfo session) {
    final controller = TextEditingController(text: session.title ?? '');
    void submit(BuildContext ctx) {
      Navigator.of(ctx).pop();
      state.ensureChatPane().renameSession(session.sessionId, controller.text);
    }

    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape:
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text('Rename session',
            style: TextStyle(color: context.appColors.textPrimary, fontSize: 18)),
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
            child: Text('Cancel',
                style: TextStyle(color: context.appColors.textSecondary)),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: context.appColors.accent),
            onPressed: () => submit(ctx),
            child: const Text('Rename',
                style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }

  static const _terminalStatuses = {'completed', 'failed', 'cancelled'};

  static bool _isTerminal(String status) =>
      _terminalStatuses.contains(status);
}

// ---------------------------------------------------------------------------
// Terminal session tile (sidebar entry for a terminal)
// ---------------------------------------------------------------------------

class _TerminalSessionTile extends StatelessWidget {
  final TerminalSessionInfo info;
  final AppState state;
  final bool isConnected;
  final VoidCallback? onSessionSelected;

  const _TerminalSessionTile({
    required this.info,
    required this.state,
    required this.isConnected,
    required this.onSessionSelected,
  });

  @override
  Widget build(BuildContext context) {
    final isShownInPane = info.paneId != null && state.panes.containsKey(info.paneId);
    final isActivePane = isShownInPane &&
        !state.hasNoPanes &&
        state.activePaneId == info.paneId;
    final dimmed = !isConnected;

    final tile = GestureDetector(
      onSecondaryTapUp: (details) =>
          _showTerminalContextMenu(context, details.globalPosition),
      child: Opacity(
        opacity: dimmed ? 0.5 : 1.0,
        child: Container(
          decoration: BoxDecoration(
            color: isActivePane
                ? context.appColors.accent.withAlpha(25)
                : isShownInPane
                    ? context.appColors.accent.withAlpha(12)
                    : null,
            border: isActivePane
                ? Border(
                    left: BorderSide(
                        color: context.appColors.accent, width: 3))
                : isShownInPane
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
                color: info.ended
                    ? context.appColors.bgElevated
                    : const Color(0xFF1A2A1A),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Icon(
                Icons.terminal_rounded,
                color: info.ended
                    ? context.appColors.textMuted
                    : context.appColors.successText,
                size: 16,
              ),
            ),
            title: Text(
              info.title,
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
              _terminalSubtitle(info),
              style: TextStyle(
                  color: context.appColors.textMuted, fontSize: 10),
            ),
            trailing: SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(Icons.close_rounded,
                    color: context.appColors.textSecondary, size: 16),
                tooltip: 'Close terminal',
                onPressed: dimmed
                    ? null
                    : () => _confirmCloseTerminal(context),
              ),
            ),
            dense: true,
            visualDensity: const VisualDensity(vertical: -4),
            contentPadding: const EdgeInsets.only(left: 36, right: 8),
            onTap: dimmed
                ? null
                : () {
                    state.showTerminalInPane(info.terminalId);
                    onSessionSelected?.call();
                  },
            onLongPress: () =>
                _showTerminalRenameDialog(context),
          ),
        ),
      ),
    );

    return Draggable<TerminalDragData>(
      data: TerminalDragData(
        terminalId: info.terminalId,
        workerId: info.workerId,
        label: info.title,
      ),
      feedback: Material(
        color: Colors.transparent,
        child: Container(
          padding: EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          decoration: BoxDecoration(
            color: context.appColors.bgElevated,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(
                color: context.appColors.accent.withAlpha(120)),
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
              Icon(Icons.terminal_rounded,
                  color: context.appColors.textSecondary, size: 14),
              SizedBox(width: 6),
              ConstrainedBox(
                constraints: BoxConstraints(maxWidth: 180),
                child: Text(
                  info.title,
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

  static String _terminalSubtitle(TerminalSessionInfo info) {
    final local = info.createdAt.toLocal();
    final time = '${_monthAbbr(local.month)} ${local.day}, '
        '${local.hour.toString().padLeft(2, '0')}:'
        '${local.minute.toString().padLeft(2, '0')}';
    return '$time \u00B7 ${info.shortId}';
  }

  static const _months = [
    '', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
    'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
  ];

  static String _monthAbbr(int month) =>
      (month >= 1 && month <= 12) ? _months[month] : '???';

  void _showTerminalContextMenu(BuildContext context, Offset position) {
    final overlay =
        Overlay.of(context).context.findRenderObject() as RenderBox;
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
              Icon(Icons.edit_outlined,
                  color: context.appColors.textSecondary, size: 18),
              SizedBox(width: 8),
              Text('Rename',
                  style: TextStyle(color: context.appColors.textPrimary)),
            ],
          ),
        ),
        PopupMenuItem(
          value: 'close',
          child: Row(
            children: [
              Icon(Icons.close_rounded,
                  color: context.appColors.errorText, size: 18),
              SizedBox(width: 8),
              Text('Close terminal',
                  style: TextStyle(color: context.appColors.errorText)),
            ],
          ),
        ),
      ],
    ).then((value) {
      if (!context.mounted) return;
      if (value == 'rename') {
        _showTerminalRenameDialog(context);
      } else if (value == 'close') {
        _confirmCloseTerminal(context);
      }
    });
  }

  void _confirmCloseTerminal(BuildContext context) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape:
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text('Close terminal?',
            style: TextStyle(
                color: context.appColors.textPrimary, fontSize: 18)),
        content: Text(
          'This will kill the terminal session on the server.',
          style: TextStyle(
              color: context.appColors.textSecondary, fontSize: 14),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text('Keep',
                style: TextStyle(color: context.appColors.textSecondary)),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
                backgroundColor: context.appColors.errorText),
            onPressed: () {
              Navigator.of(ctx).pop();
              state.closeTerminalSession(info.terminalId);
            },
            child: const Text('Close',
                style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }

  void _showTerminalRenameDialog(BuildContext context) {
    final controller = TextEditingController(text: info.title);
    void submit(BuildContext ctx) {
      Navigator.of(ctx).pop();
      state.renameTerminal(info.terminalId, controller.text);
    }

    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape:
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text('Rename terminal',
            style: TextStyle(
                color: context.appColors.textPrimary, fontSize: 18)),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLength: 200,
          style: TextStyle(
              color: context.appColors.textPrimary, fontSize: 14),
          decoration: InputDecoration(
            hintText: 'Terminal name',
            hintStyle: TextStyle(color: context.appColors.textMuted),
          ),
          onSubmitted: (_) => submit(ctx),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text('Cancel',
                style: TextStyle(color: context.appColors.textSecondary)),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
                backgroundColor: context.appColors.accent),
            onPressed: () => submit(ctx),
            child: const Text('Rename',
                style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }
}

/// Data carried during a terminal drag from the sidebar.
class TerminalDragData {
  final String terminalId;
  final String workerId;
  final String label;

  const TerminalDragData({
    required this.terminalId,
    required this.workerId,
    required this.label,
  });
}

// ---------------------------------------------------------------------------
// Session leading icon
// ---------------------------------------------------------------------------

class _SessionLeadingIcon extends StatelessWidget {
  final SessionInfo session;

  const _SessionLeadingIcon({required this.session});

  @override
  Widget build(BuildContext context) {
    final icon = _sessionIcon(session);
    final color = _sessionIconColor(context, session);
    final bgColor = _sessionIconBg(context, session);
    final isAwaitingPermission =
        session.activityState == 'awaiting_permission' &&
            (session.status == 'active' || session.status == 'executing');
    final isProcessing = session.isProcessing &&
        !isAwaitingPermission &&
        (session.status == 'active' || session.status == 'executing');

    return Container(
      width: 30,
      height: 30,
      decoration: BoxDecoration(
        color: bgColor,
        borderRadius: BorderRadius.circular(8),
      ),
      child: isProcessing
          ? _SpinningIcon(icon: icon, color: color, size: 16)
          : Icon(icon, color: color, size: 16),
    );
  }

  static IconData _sessionIcon(SessionInfo s) {
    if (s.activityState == 'awaiting_permission' &&
        (s.status == 'active' || s.status == 'executing')) {
      return Icons.shield_outlined;
    }
    if ((s.status == 'active' || s.status == 'executing') &&
        !s.isProcessing) {
      return Icons.chat_bubble_outline_rounded;
    }
    return switch (s.status) {
      'active' || 'executing' => Icons.sync_rounded,
      'paused' => Icons.pause_circle_outline_rounded,
      'completed' => Icons.check_circle_outline_rounded,
      'failed' => Icons.cancel_outlined,
      'cancelled' => Icons.stop_circle_outlined,
      _ => Icons.circle_outlined,
    };
  }

  static Color _sessionIconColor(BuildContext context, SessionInfo s) {
    if (s.activityState == 'awaiting_permission' &&
        (s.status == 'active' || s.status == 'executing')) {
      return context.appColors.toolAccent;
    }
    if ((s.status == 'active' || s.status == 'executing') &&
        !s.isProcessing) {
      return context.appColors.accentLight;
    }
    return switch (s.status) {
      'active' || 'executing' => context.appColors.toolAccent,
      'paused' => context.appColors.accentLight,
      'completed' => context.appColors.successText,
      'failed' => context.appColors.errorText,
      'cancelled' => context.appColors.textMuted,
      _ => context.appColors.textMuted,
    };
  }

  static Color _sessionIconBg(BuildContext context, SessionInfo s) {
    if (s.activityState == 'awaiting_permission' &&
        (s.status == 'active' || s.status == 'executing')) {
      return Color(0xFF2A2000);
    }
    if ((s.status == 'active' || s.status == 'executing') &&
        !s.isProcessing) {
      return Color(0xFF112233);
    }
    return switch (s.status) {
      'active' || 'executing' => Color(0xFF2A2311),
      'paused' => Color(0xFF112233),
      'completed' => context.appColors.successBg,
      'failed' => context.appColors.errorBg,
      'cancelled' => context.appColors.bgElevated,
      _ => context.appColors.bgElevated,
    };
  }
}

// ---------------------------------------------------------------------------
// Spinning icon
// ---------------------------------------------------------------------------

class _SpinningIcon extends StatefulWidget {
  final IconData icon;
  final Color color;
  final double size;

  const _SpinningIcon({
    required this.icon,
    required this.color,
    required this.size,
  });

  @override
  State<_SpinningIcon> createState() => _SpinningIconState();
}

class _SpinningIconState extends State<_SpinningIcon>
    with SingleTickerProviderStateMixin {
  late final AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      duration: const Duration(milliseconds: 1200),
      vsync: this,
    )..repeat();
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return RotationTransition(
      turns: _controller,
      child: Icon(widget.icon, color: widget.color, size: widget.size),
    );
  }
}

/// Bottom-sheet wrapper with draggable support.
class _SessionSheetContent extends StatelessWidget {
  const _SessionSheetContent();

  @override
  Widget build(BuildContext context) {
    final screenHeight = MediaQuery.of(context).size.height;
    final statusBarHeight = MediaQuery.of(context).padding.top;
    final maxSize = (screenHeight - statusBarHeight) / screenHeight;

    return DraggableScrollableSheet(
      initialChildSize: 0.6,
      minChildSize: 0.3,
      maxChildSize: maxSize,
      expand: false,
      builder: (context, scrollController) {
        return CustomScrollView(
          controller: scrollController,
          slivers: [
            // Drag handle
            SliverToBoxAdapter(
              child: Column(
                children: [
                  SizedBox(height: 12),
                  Center(
                    child: Container(
                      width: 40,
                      height: 4,
                      decoration: BoxDecoration(
                        color: context.appColors.textMuted.withAlpha(100),
                        borderRadius: BorderRadius.circular(2),
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                ],
              ),
            ),
            // Session list as a sliver that fills remaining space
            SliverFillRemaining(
              child: SessionListPanel(
                onSessionSelected: () => Navigator.of(context).pop(),
              ),
            ),
          ],
        );
      },
    );
  }
}
