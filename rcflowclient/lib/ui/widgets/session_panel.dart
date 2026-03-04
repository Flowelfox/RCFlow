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
            padding: const EdgeInsets.fromLTRB(20, 0, 4, 0),
            child: Row(
              children: [
                const Text('Workers',
                    style: TextStyle(
                        color: kTextPrimary,
                        fontSize: 18,
                        fontWeight: FontWeight.w600)),
                const Spacer(),
                Consumer<AppState>(
                  builder: (context, state, _) {
                    final hiding = state.hideTerminalSessions;
                    return IconButton(
                      icon: Icon(
                        hiding ? Icons.filter_alt : Icons.filter_alt_outlined,
                        color: hiding ? kAccentLight : kTextSecondary,
                        size: 20,
                      ),
                      onPressed: () => state.toggleHideTerminalSessions(),
                      mouseCursor: SystemMouseCursors.click,
                      tooltip: hiding
                          ? 'Show all sessions'
                          : 'Hide finished sessions',
                      constraints: const BoxConstraints(
                          maxWidth: 36, maxHeight: 36),
                      padding: EdgeInsets.zero,
                    );
                  },
                ),
                IconButton(
                  icon: const Icon(Icons.add_rounded,
                      color: kTextSecondary, size: 20),
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
                return const Center(
                  child: Text('No workers configured',
                      style: TextStyle(color: kTextMuted, fontSize: 14)),
                );
              }

              final grouped = state.sessionsByWorker;

              return ListView.builder(
                padding: const EdgeInsets.symmetric(vertical: 8),
                itemCount: configs.length,
                itemBuilder: (context, index) {
                  final config = configs[index];
                  final worker = state.getWorker(config.id);
                  final sessions = grouped[config.id] ?? [];
                  final expanded = _expandedWorkers.contains(config.id);
                  return _WorkerGroup(
                    config: config,
                    worker: worker,
                    sessions: sessions,
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
                      state.activePane.switchSession(sessionId);
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
              child: const Padding(
                padding: EdgeInsets.symmetric(horizontal: 8, vertical: 10),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.settings_outlined,
                        color: kTextMuted, size: 20),
                    SizedBox(width: 10),
                    Text('Settings',
                        style:
                            TextStyle(color: kTextSecondary, fontSize: 14)),
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
  final bool expanded;
  final VoidCallback onToggleExpand;
  final void Function(String sessionId) onSessionTap;
  final AppState state;
  final VoidCallback? onSessionSelected;

  const _WorkerGroup({
    required this.config,
    required this.worker,
    required this.sessions,
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
      WorkerConnectionStatus.connected => kSuccessText,
      WorkerConnectionStatus.connecting => kToolAccent,
      WorkerConnectionStatus.reconnecting => kToolAccent,
      WorkerConnectionStatus.disconnected => kErrorText,
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
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
              child: Row(
                children: [
                  Icon(
                    expanded
                        ? Icons.expand_more_rounded
                        : Icons.chevron_right_rounded,
                    color: kTextSecondary,
                    size: 18,
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      '${config.name} (${sessions.length})',
                      style: const TextStyle(
                        color: kTextPrimary,
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
                        icon: const Icon(Icons.refresh_rounded,
                            color: kTextSecondary, size: 14),
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
                        icon: const Icon(Icons.add_rounded,
                            color: kTextSecondary, size: 14),
                        onPressed: () {
                          state.activePane.setTargetWorker(config.id);
                          state.activePane.startNewChat();
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
        // Sessions
        if (expanded)
          ...sessions.map((s) {
            final isActiveSession =
                s.sessionId == state.activePane.sessionId;
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
                child: ListTile(
                  leading: _SessionLeadingIcon(session: s),
                  title: Text(
                    s.title ?? s.shortId,
                    style: TextStyle(
                      color: isActiveSession ? kAccentLight : kTextPrimary,
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
                    style: const TextStyle(color: kTextMuted, fontSize: 10),
                  ),
                  trailing: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      if (isActiveSession)
                        Container(
                          width: 8,
                          height: 8,
                          margin: const EdgeInsets.only(right: 8),
                          decoration: const BoxDecoration(
                            color: kAccent,
                            shape: BoxShape.circle,
                          ),
                        )
                      else if (isViewedByAnyPane)
                        Container(
                          width: 6,
                          height: 6,
                          margin: const EdgeInsets.only(right: 8),
                          decoration: BoxDecoration(
                            color: kAccent.withAlpha(100),
                            shape: BoxShape.circle,
                          ),
                        ),
                      if (_isTerminal(s.status))
                        SizedBox(
                          width: 26,
                          height: 26,
                          child: IconButton(
                            padding: EdgeInsets.zero,
                            icon: const Icon(Icons.restore_rounded,
                                color: kAccentLight, size: 16),
                            tooltip: 'Restore session',
                            onPressed: dimmed
                                ? null
                                : () => state.activePane
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
                              icon: const Icon(Icons.play_arrow_rounded,
                                  color: kAccentLight, size: 16),
                              tooltip: 'Resume session',
                              onPressed: dimmed
                                  ? null
                                  : () => state.activePane
                                      .resumeSession(s.sessionId),
                            ),
                          )
                        else
                          SizedBox(
                            width: 26,
                            height: 26,
                            child: IconButton(
                              padding: EdgeInsets.zero,
                              icon: const Icon(Icons.pause_rounded,
                                  color: kTextSecondary, size: 16),
                              tooltip: 'Pause session',
                              onPressed: dimmed
                                  ? null
                                  : () => state.activePane
                                      .pauseSession(s.sessionId),
                            ),
                          ),
                        SizedBox(
                          width: 26,
                          height: 26,
                          child: IconButton(
                            padding: EdgeInsets.zero,
                            icon: const Icon(Icons.stop_circle_outlined,
                                color: kErrorText, size: 16),
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
            );
            return Draggable<SessionDragData>(
              data: dragData,
              feedback: Material(
                color: Colors.transparent,
                child: Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                  decoration: BoxDecoration(
                    color: kBgElevated,
                    borderRadius: BorderRadius.circular(16),
                    border: Border.all(color: kAccent.withAlpha(120)),
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
                      const Icon(Icons.drag_indicator_rounded,
                          color: kTextSecondary, size: 14),
                      const SizedBox(width: 6),
                      ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 180),
                        child: Text(
                          dragData.label,
                          style: const TextStyle(
                            color: kTextPrimary,
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
          }),
        if (expanded && sessions.isEmpty)
          Padding(
            padding: const EdgeInsets.only(left: 44, bottom: 4),
            child: Text(
              isConnected ? 'No sessions' : 'Disconnected',
              style: const TextStyle(color: kTextMuted, fontSize: 11),
            ),
          ),
      ],
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
        position & const Size(1, 1),
        Offset.zero & overlay.size,
      ),
      color: kBgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      items: [
        if (!isConnected && !isConnecting)
          const PopupMenuItem(
            value: 'connect',
            child: Row(
              children: [
                Icon(Icons.link_rounded, color: kAccentLight, size: 18),
                SizedBox(width: 8),
                Text('Connect', style: TextStyle(color: kTextPrimary)),
              ],
            ),
          ),
        if (isConnected)
          const PopupMenuItem(
            value: 'disconnect',
            child: Row(
              children: [
                Icon(Icons.link_off_rounded,
                    color: kTextSecondary, size: 18),
                SizedBox(width: 8),
                Text('Disconnect', style: TextStyle(color: kTextPrimary)),
              ],
            ),
          ),
        PopupMenuItem(
          value: 'edit',
          child: Row(
            children: [
              Icon(
                Icons.edit_outlined,
                color: kTextSecondary,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Edit Worker',
                style: const TextStyle(color: kTextPrimary),
              ),
            ],
          ),
        ),
        const PopupMenuItem(
          value: 'remove',
          child: Row(
            children: [
              Icon(Icons.delete_outline, color: kErrorText, size: 18),
              SizedBox(width: 8),
              Text('Remove', style: TextStyle(color: kErrorText)),
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
        backgroundColor: kBgSurface,
        shape:
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Remove worker?',
            style: TextStyle(color: kTextPrimary, fontSize: 18)),
        content: Text(
          'This will disconnect and remove "${config.name}".',
          style: const TextStyle(color: kTextSecondary, fontSize: 14),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Cancel',
                style: TextStyle(color: kTextSecondary)),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: kErrorText),
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
        position & const Size(1, 1),
        Offset.zero & overlay.size,
      ),
      color: kBgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      items: [
        const PopupMenuItem(
          value: 'rename',
          child: Row(
            children: [
              Icon(Icons.edit_outlined, color: kTextSecondary, size: 18),
              SizedBox(width: 8),
              Text('Rename', style: TextStyle(color: kTextPrimary)),
            ],
          ),
        ),
        if (!_isTerminal(session.status) && session.status != 'paused')
          const PopupMenuItem(
            value: 'pause',
            child: Row(
              children: [
                Icon(Icons.pause_rounded, color: kAccentLight, size: 18),
                SizedBox(width: 8),
                Text('Pause session',
                    style: TextStyle(color: kTextPrimary)),
              ],
            ),
          ),
        if (session.status == 'paused')
          const PopupMenuItem(
            value: 'resume',
            child: Row(
              children: [
                Icon(Icons.play_arrow_rounded,
                    color: kAccentLight, size: 18),
                SizedBox(width: 8),
                Text('Resume session',
                    style: TextStyle(color: kTextPrimary)),
              ],
            ),
          ),
        if (_isTerminal(session.status))
          const PopupMenuItem(
            value: 'restore',
            child: Row(
              children: [
                Icon(Icons.restore_rounded, color: kAccentLight, size: 18),
                SizedBox(width: 8),
                Text('Restore session',
                    style: TextStyle(color: kTextPrimary)),
              ],
            ),
          ),
        if (!_isTerminal(session.status))
          const PopupMenuItem(
            value: 'cancel',
            child: Row(
              children: [
                Icon(Icons.stop_circle_outlined,
                    color: kErrorText, size: 18),
                SizedBox(width: 8),
                Text('End session',
                    style: TextStyle(color: kErrorText)),
              ],
            ),
          ),
      ],
    ).then((value) {
      if (!context.mounted) return;
      if (value == 'rename') {
        _showRenameDialog(context, state, session);
      } else if (value == 'pause') {
        state.activePane.pauseSession(session.sessionId);
      } else if (value == 'resume') {
        state.activePane.resumeSession(session.sessionId);
      } else if (value == 'restore') {
        state.activePane.restoreSession(session.sessionId);
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
        backgroundColor: kBgSurface,
        shape:
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('End session?',
            style: TextStyle(color: kTextPrimary, fontSize: 18)),
        content: Text(
          'This will end session ${session.shortId} on the server.',
          style: const TextStyle(color: kTextSecondary, fontSize: 14),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Keep',
                style: TextStyle(color: kTextSecondary)),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: kErrorText),
            onPressed: () {
              Navigator.of(ctx).pop();
              onSessionSelected?.call();
              state.activePane.cancelSession(session.sessionId);
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
      state.activePane.renameSession(session.sessionId, controller.text);
    }

    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: kBgSurface,
        shape:
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Rename session',
            style: TextStyle(color: kTextPrimary, fontSize: 18)),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLength: 200,
          style: const TextStyle(color: kTextPrimary, fontSize: 14),
          decoration: const InputDecoration(
            hintText: 'Session title',
            hintStyle: TextStyle(color: kTextMuted),
          ),
          onSubmitted: (_) => submit(ctx),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Cancel',
                style: TextStyle(color: kTextSecondary)),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: kAccent),
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
// Session leading icon
// ---------------------------------------------------------------------------

class _SessionLeadingIcon extends StatelessWidget {
  final SessionInfo session;

  const _SessionLeadingIcon({required this.session});

  @override
  Widget build(BuildContext context) {
    final icon = _sessionIcon(session);
    final color = _sessionIconColor(session);
    final bgColor = _sessionIconBg(session);
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

  static Color _sessionIconColor(SessionInfo s) {
    if (s.activityState == 'awaiting_permission' &&
        (s.status == 'active' || s.status == 'executing')) {
      return kToolAccent;
    }
    if ((s.status == 'active' || s.status == 'executing') &&
        !s.isProcessing) {
      return kAccentLight;
    }
    return switch (s.status) {
      'active' || 'executing' => kToolAccent,
      'paused' => kAccentLight,
      'completed' => kSuccessText,
      'failed' => kErrorText,
      'cancelled' => kTextMuted,
      _ => kTextMuted,
    };
  }

  static Color _sessionIconBg(SessionInfo s) {
    if (s.activityState == 'awaiting_permission' &&
        (s.status == 'active' || s.status == 'executing')) {
      return const Color(0xFF2A2000);
    }
    if ((s.status == 'active' || s.status == 'executing') &&
        !s.isProcessing) {
      return const Color(0xFF112233);
    }
    return switch (s.status) {
      'active' || 'executing' => const Color(0xFF2A2311),
      'paused' => const Color(0xFF112233),
      'completed' => kSuccessBg,
      'failed' => kErrorBg,
      'cancelled' => kBgElevated,
      _ => kBgElevated,
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
                  const SizedBox(height: 12),
                  Center(
                    child: Container(
                      width: 40,
                      height: 4,
                      decoration: BoxDecoration(
                        color: kTextMuted.withAlpha(100),
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
