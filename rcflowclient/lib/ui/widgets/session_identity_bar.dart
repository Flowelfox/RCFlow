import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/session_info.dart';
import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';
import '../badges/badge_bar.dart';
import '../badges/badge_chip.dart';

/// A thin strip displayed above the chat output that shows which session is
/// currently open — title, status badge, and worker badge.
///
/// Must be placed inside a [ChangeNotifierProvider<PaneState>] subtree.
/// Tapping opens a bottom sheet with quick session actions (pause, resume,
/// rename, cancel).
class SessionIdentityBar extends StatelessWidget {
  const SessionIdentityBar({super.key});

  @override
  Widget build(BuildContext context) {
    final pane = context.watch<PaneState>();
    final appState = context.watch<AppState>();
    final sessionId = pane.sessionId;

    if (sessionId == null) {
      // New-chat or blank pane — show guidance with interactive worker badge.
      return _NewChatBar(pane: pane, appState: appState);
    }

    final session = appState.getSession(sessionId);
    if (session == null) {
      // Session not yet in the list (may be loading) — show ID only.
      return _GuidanceBar(message: sessionId.substring(0, 8));
    }

    return _SessionBar(session: session, appState: appState);
  }
}

// ---------------------------------------------------------------------------
// Guidance bar (loading / fallback)
// ---------------------------------------------------------------------------

class _GuidanceBar extends StatelessWidget {
  final String message;

  const _GuidanceBar({required this.message});

  @override
  Widget build(BuildContext context) {
    final appColors = context.appColors;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      color: appColors.bgElevated,
      child: Text(
        message,
        style: TextStyle(
          color: appColors.textMuted,
          fontSize: 12,
        ),
        overflow: TextOverflow.ellipsis,
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// New-chat bar with interactive worker badge
// ---------------------------------------------------------------------------

class _NewChatBar extends StatelessWidget {
  final PaneState pane;
  final AppState appState;

  const _NewChatBar({required this.pane, required this.appState});

  @override
  Widget build(BuildContext context) {
    final appColors = context.appColors;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      color: appColors.bgElevated,
      child: Row(
        children: [
          Text(
            'New chat',
            style: TextStyle(
              color: appColors.textMuted,
              fontSize: 12,
            ),
          ),
          const Spacer(),
          Text(
            'send a message to start',
            style: TextStyle(
              color: appColors.textMuted,
              fontSize: 11,
            ),
          ),
        ],
      ),
    );
  }

}

// ---------------------------------------------------------------------------
// Caveman badge preview chip — shown in new-session pane when caveman is active
// ---------------------------------------------------------------------------

class CavemanPreviewBadge extends StatelessWidget {
  final VoidCallback onDismiss;

  const CavemanPreviewBadge({super.key, required this.onDismiss});

  static const _color = Color(0xFF92400E); // amber-800

  @override
  Widget build(BuildContext context) {
    return BadgeChip(
      color: _color,
      label: 'Caveman',
      icon: Icons.warning_amber_rounded,
      trailing: GestureDetector(
        onTap: onDismiss,
        child: Icon(Icons.close, size: 14, color: _color.withAlpha(180)),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Session info bar
// ---------------------------------------------------------------------------

class _SessionBar extends StatelessWidget {
  final SessionInfo session;
  final AppState appState;

  const _SessionBar({required this.session, required this.appState});

  @override
  Widget build(BuildContext context) {
    final appColors = context.appColors;
    // Prefer the live workerConfigs name (user-configured) over the badge
    // label, which may carry the server's internal backend_id. Fall back to
    return GestureDetector(
      onTap: () => _showSessionActions(context),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        decoration: BoxDecoration(
          color: appColors.bgElevated,
          border: Border(
            bottom: BorderSide(color: appColors.divider, width: 0.5),
          ),
        ),
        child: Row(
          children: [
            // Status-only badges from unified registry. Caveman, worker and
            // agent chips are rendered above the input field instead so they
            // appear alongside the other selectable chips.
            BadgeBar(
              badges: session.badges,
              slotFilter: const {'status'},
            ),
            const SizedBox(width: 8),
            // Session title
            Expanded(
              child: Text(
                session.title ?? session.shortId,
                style: TextStyle(
                  color: appColors.textPrimary,
                  fontSize: 13,
                  fontWeight: FontWeight.w500,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ),
            // Chevron hint
            const SizedBox(width: 4),
            Icon(
              Icons.keyboard_arrow_down_rounded,
              color: appColors.textMuted,
              size: 16,
            ),
          ],
        ),
      ),
    );
  }

  void _showSessionActions(BuildContext context) {
    final appColors = context.appColors;
    final isRunning = !_isTerminalStatus(session.status) &&
        session.status != 'paused';
    final isPaused = session.status == 'paused';
    final isTerminal = _isTerminalStatus(session.status);

    showModalBottomSheet(
      context: context,
      backgroundColor: appColors.bgSurface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) {
        return SafeArea(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              // Handle
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 12),
                child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(
                    color: appColors.textMuted.withAlpha(100),
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              // Session title header
              Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 20, vertical: 4),
                child: Row(
                  children: [
                    BadgeBar(
                      badges: session.badges,
                      slotFilter: {'status', 'caveman', 'agent'},
                    ),
                    const SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        session.title ?? session.shortId,
                        style: TextStyle(
                          color: appColors.textPrimary,
                          fontSize: 16,
                          fontWeight: FontWeight.w600,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
              ),
              const Divider(height: 16),
              // Actions
              if (isRunning)
                ListTile(
                  leading: Icon(
                    Icons.pause_rounded,
                    color: appColors.textSecondary,
                  ),
                  title: Text(
                    'Pause session',
                    style: TextStyle(color: appColors.textPrimary),
                  ),
                  onTap: () {
                    Navigator.of(ctx).pop();
                    appState.pauseSessionDirect(
                      session.sessionId,
                      session.workerId,
                    );
                  },
                ),
              if (isPaused)
                ListTile(
                  leading: Icon(
                    Icons.play_arrow_rounded,
                    color: appColors.textSecondary,
                  ),
                  title: Text(
                    'Resume session',
                    style: TextStyle(color: appColors.textPrimary),
                  ),
                  onTap: () {
                    Navigator.of(ctx).pop();
                    appState.resumeSessionDirect(
                      session.sessionId,
                      session.workerId,
                    );
                  },
                ),
              ListTile(
                leading: Icon(
                  Icons.edit_outlined,
                  color: appColors.textSecondary,
                ),
                title: Text(
                  'Rename session',
                  style: TextStyle(color: appColors.textPrimary),
                ),
                onTap: () {
                  Navigator.of(ctx).pop();
                  _showRenameDialog(context);
                },
              ),
              if (!isTerminal)
                ListTile(
                  leading: Icon(
                    Icons.stop_circle_outlined,
                    color: appColors.errorText,
                  ),
                  title: Text(
                    'End session',
                    style: TextStyle(color: appColors.errorText),
                  ),
                  onTap: () {
                    Navigator.of(ctx).pop();
                    appState.cancelSessionDirect(
                      session.sessionId,
                      session.workerId,
                    );
                  },
                ),
              const SizedBox(height: 8),
            ],
          ),
        );
      },
    );
  }

  void _showRenameDialog(BuildContext context) {
    final appColors = context.appColors;
    final controller =
        TextEditingController(text: session.title ?? '');
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: appColors.bgSurface,
        shape:
            RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        title: Text(
          'Rename session',
          style: TextStyle(
            color: appColors.textPrimary,
            fontSize: 16,
          ),
        ),
        content: TextField(
          controller: controller,
          autofocus: true,
          style: TextStyle(color: appColors.textPrimary),
          decoration: InputDecoration(
            hintText: 'Session title (leave blank to auto-generate)',
            hintStyle: TextStyle(color: appColors.textMuted, fontSize: 13),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text(
              'Cancel',
              style: TextStyle(color: appColors.textSecondary),
            ),
          ),
          FilledButton(
            onPressed: () {
              Navigator.of(ctx).pop();
              appState.renameSessionDirect(
                session.sessionId,
                session.workerId,
                controller.text,
              );
            },
            child: const Text('Rename'),
          ),
        ],
      ),
    );
  }

  static bool _isTerminalStatus(String status) =>
      status == 'completed' || status == 'failed' || status == 'cancelled';
}

// ---------------------------------------------------------------------------
// Worker badge chip — read-only or interactive
// ---------------------------------------------------------------------------

class WorkerBadge extends StatelessWidget {
  final String name;
  final bool interactive;
  final VoidCallback? onTap;

  const WorkerBadge({
    super.key,
    required this.name,
    required this.interactive,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final badge = BadgeChip(
      label: name,
      icon: Icons.dns_outlined,
      trailing:
          interactive ? BadgeChip.neutralDropdownCaret(context) : null,
    );

    if (interactive && onTap != null) {
      return GestureDetector(onTap: onTap, child: badge);
    }
    return badge;
  }
}

