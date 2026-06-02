part of 'input_area.dart';

class _SubprocessStatusBar extends StatefulWidget {
  final SubprocessInfo subprocess;
  final VoidCallback onKill;

  const _SubprocessStatusBar({required this.subprocess, required this.onKill});

  @override
  State<_SubprocessStatusBar> createState() => _SubprocessStatusBarState();
}

class _SubprocessStatusBarState extends State<_SubprocessStatusBar> {
  bool _killing = false;

  void _onKill() {
    setState(() => _killing = true);
    widget.onKill();
    // Reset after a short timeout in case the server doesn't respond
    Future.delayed(const Duration(seconds: 5), () {
      if (mounted) setState(() => _killing = false);
    });
  }

  @override
  Widget build(BuildContext context) {
    final sub = widget.subprocess;
    final dirName = sub.workingDirectory.isNotEmpty
        ? sub.workingDirectory.split('/').last
        : '';
    final label = sub.currentTool != null
        ? '${sub.displayName} · ${sub.currentTool}'
        : sub.displayName;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: context.appColors.bgElevated,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: context.appColors.divider),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(
            width: 10,
            height: 10,
            child: CircularProgressIndicator(
              strokeWidth: 1.5,
              color: context.appColors.accentLight,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  label,
                  style: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 12,
                    fontWeight: FontWeight.w500,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
                if (dirName.isNotEmpty)
                  Text(
                    dirName,
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 11,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          Tooltip(
            message: 'Kill subprocess',
            child: GestureDetector(
              onTap: _killing ? null : _onKill,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: _killing
                      ? context.appColors.bgElevated
                      : context.appColors.errorBg,
                  borderRadius: BorderRadius.circular(5),
                  border: Border.all(
                    color: _killing
                        ? context.appColors.divider
                        : context.appColors.errorText.withValues(alpha: 0.4),
                  ),
                ),
                child: _killing
                    ? SizedBox(
                        width: 10,
                        height: 10,
                        child: CircularProgressIndicator(
                          strokeWidth: 1.5,
                          color: context.appColors.textMuted,
                        ),
                      )
                    : Text(
                        'Kill',
                        style: TextStyle(
                          color: context.appColors.errorText,
                          fontSize: 11,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

/// Compact strip listing live ``Monitor`` watches and pending
/// ``ScheduleWakeup`` timers for the current pane.
///
/// Renders nothing when neither is active.  Monitor entries show a live
/// elapsed-time counter (counting up); wake entries show an alarm icon and a
/// countdown to their fire time.  Both tick every second independently of the
/// rest of the UI.
class _MonitorStatusStrip extends StatefulWidget {
  const _MonitorStatusStrip();

  @override
  State<_MonitorStatusStrip> createState() => _MonitorStatusStripState();
}

class _MonitorStatusStripState extends State<_MonitorStatusStrip> {
  Timer? _ticker;

  @override
  void initState() {
    super.initState();
    _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }

  String _fmt(Duration d) {
    final h = d.inHours;
    final m = d.inMinutes.remainder(60);
    final s = d.inSeconds.remainder(60);
    if (h > 0) {
      return '${h.toString()}:${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
    }
    return '${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
  }

  /// Format a remaining duration as a countdown.  Wakes that are due (or
  /// overdue) collapse to ``now`` rather than showing a negative timer.
  String _fmtRemaining(Duration d) {
    if (d.isNegative || d.inSeconds <= 0) return 'now';
    return _fmt(d);
  }

  /// One activity entry: a monospace timer plus a muted description, with an
  /// optional leading [icon] used to distinguish wake entries from monitors.
  Widget _entry(
    AppColors colors, {
    IconData? icon,
    required String time,
    required String desc,
  }) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        if (icon != null) ...[
          Icon(icon, size: 11, color: colors.accentLight),
          const SizedBox(width: 3),
        ],
        Text(
          time,
          style: TextStyle(
            color: colors.accentLight,
            fontSize: 11,
            fontFamily: 'monospace',
          ),
        ),
        const SizedBox(width: 4),
        ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 220),
          child: Text(
            desc,
            style: TextStyle(color: colors.textMuted, fontSize: 11),
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final monitors = context.select<PaneState, List<DisplayMessage>>(
      (s) => s.liveMonitors,
    );
    final wakes = context.select<PaneState, List<ScheduledWake>>(
      (s) => s.currentScheduledWakes,
    );
    if (monitors.isEmpty && wakes.isEmpty) return const SizedBox.shrink();
    final colors = context.appColors;
    final now = DateTime.now();

    // Leading indicator reflects whichever activity is live; monitors take
    // precedence for the icon/count when both are present (wake entries carry
    // their own alarm icon inline so they stay distinguishable either way).
    final IconData leadIcon;
    final String leadLabel;
    if (monitors.isNotEmpty) {
      leadIcon = Icons.podcasts_rounded;
      leadLabel =
          monitors.length == 1 ? '1 monitor' : '${monitors.length} monitors';
    } else {
      leadIcon = Icons.alarm_rounded;
      leadLabel = wakes.length == 1 ? '1 wake' : '${wakes.length} wakes';
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: colors.bgElevated,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: colors.divider),
        ),
        child: Row(
          children: [
            Icon(leadIcon, size: 12, color: colors.accentLight),
            const SizedBox(width: 6),
            Text(
              leadLabel,
              style: TextStyle(
                color: colors.textPrimary,
                fontSize: 11,
                fontWeight: FontWeight.w500,
              ),
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Wrap(
                spacing: 8,
                runSpacing: 2,
                children: [
                  for (final m in monitors)
                    _entry(
                      colors,
                      time: _fmt(
                        m.monitorStartedAt != null
                            ? now.difference(m.monitorStartedAt!)
                            : Duration.zero,
                      ),
                      desc: m.displayName ?? 'Monitor',
                    ),
                  for (final w in wakes)
                    _entry(
                      colors,
                      icon: Icons.alarm_rounded,
                      time: _fmtRemaining(w.fireAt.difference(now)),
                      desc: w.reason.isNotEmpty ? w.reason : 'Wake',
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
