import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/ws_messages.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';
import '../../../theme/spacing.dart';

/// Card for a live Claude Code ``Monitor`` watch.
///
/// Header shows the description, an elapsed-time counter that advances each
/// second while the monitor is live, and a Stop button.  The body is a
/// collapsible list of stdout-line events with relative timestamps from the
/// start of the watch.
class MonitorBlock extends StatefulWidget {
  final DisplayMessage message;
  const MonitorBlock({super.key, required this.message});

  @override
  State<MonitorBlock> createState() => _MonitorBlockState();
}

class _MonitorBlockState extends State<MonitorBlock> {
  Timer? _ticker;
  bool _stopping = false;

  @override
  void initState() {
    super.initState();
    if (!widget.message.finished) {
      _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
        if (mounted) setState(() {});
      });
    }
  }

  @override
  void didUpdateWidget(covariant MonitorBlock old) {
    super.didUpdateWidget(old);
    if (widget.message.finished && _ticker != null) {
      _ticker?.cancel();
      _ticker = null;
    } else if (!widget.message.finished && _ticker == null) {
      _ticker = Timer.periodic(const Duration(seconds: 1), (_) {
        if (mounted) setState(() {});
      });
    }
  }

  @override
  void dispose() {
    _ticker?.cancel();
    super.dispose();
  }

  String _formatElapsed(Duration d) {
    final h = d.inHours;
    final m = d.inMinutes.remainder(60);
    final s = d.inSeconds.remainder(60);
    if (h > 0) {
      return '${h.toString()}:${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
    }
    return '${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
  }

  String _formatRelative(Duration d) {
    if (d.isNegative) return '+0s';
    if (d.inSeconds < 60) return '+${d.inSeconds}s';
    return '+${_formatElapsed(d)}';
  }

  Future<void> _onStop() async {
    final id = widget.message.monitorId;
    if (id == null) return;
    setState(() => _stopping = true);
    try {
      await context.read<PaneState>().cancelMonitor(id);
    } finally {
      // The MONITOR_END event will flip ``finished`` and cancel the ticker.
      // Reset _stopping after a short window in case the server is slow.
      Future.delayed(const Duration(seconds: 5), () {
        if (mounted) setState(() => _stopping = false);
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final m = widget.message;
    final colors = context.appColors;
    final startedAt = m.monitorStartedAt;
    final now = DateTime.now();
    final elapsed = startedAt != null
        ? now.difference(startedAt)
        : Duration.zero;
    final timeoutMs = m.monitorTimeoutMs ?? 0;
    final persistent = m.monitorPersistent ?? false;
    final hasDeadline = !persistent && timeoutMs > 0;
    final deadlineFraction = hasDeadline
        ? (elapsed.inMilliseconds / timeoutMs).clamp(0.0, 1.0)
        : 0.0;
    final approachingTimeout =
        hasDeadline && deadlineFraction >= 0.8 && !m.finished;

    final description = m.displayName ?? 'Monitor';
    final command =
        (m.toolInput?['command'] as String?)?.trim() ?? '';
    final events = m.monitorEvents ?? const <MonitorEvent>[];
    final reason = m.monitorTerminationReason;
    final exitCode = m.monitorExitCode;

    Color statusColor;
    IconData statusIcon;
    String statusLabel;
    if (!m.finished) {
      statusColor = approachingTimeout ? colors.errorText : colors.accentLight;
      statusIcon = Icons.podcasts_rounded;
      statusLabel = 'watching';
    } else if (reason == 'exit' && (exitCode == null || exitCode == 0)) {
      statusColor = colors.successText;
      statusIcon = Icons.check_circle_outline_rounded;
      statusLabel = exitCode == null
          ? 'finished'
          : 'exit $exitCode';
    } else if (reason == 'timeout') {
      statusColor = colors.errorText;
      statusIcon = Icons.timer_off_outlined;
      statusLabel = 'timed out';
    } else if (reason == 'cancelled' || reason == 'session_end') {
      statusColor = colors.textMuted;
      statusIcon = Icons.stop_circle_outlined;
      statusLabel = reason == 'cancelled' ? 'stopped' : 'session ended';
    } else {
      statusColor = colors.errorText;
      statusIcon = Icons.error_outline_rounded;
      statusLabel = exitCode != null ? 'exit $exitCode' : 'error';
    }

    final timerText = hasDeadline
        ? '${_formatElapsed(elapsed)} / ${_formatElapsed(Duration(milliseconds: timeoutMs))}'
        : _formatElapsed(elapsed);

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: kSpace1),
      child: Container(
        decoration: BoxDecoration(
          color: colors.toolBg,
          borderRadius: BorderRadius.circular(kRadiusMedium),
          border: Border.all(
            color: approachingTimeout
                ? colors.errorText.withValues(alpha: 0.5)
                : colors.divider,
          ),
        ),
        clipBehavior: Clip.antiAlias,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Header
            GestureDetector(
              onTap: events.isEmpty
                  ? null
                  : () {
                      m.expanded = !m.expanded;
                      context.read<PaneState>().refresh();
                    },
              child: Container(
                color: Colors.transparent,
                padding:
                    const EdgeInsets.symmetric(horizontal: kSpace3, vertical: 10),
                child: Row(
                  children: [
                    if (events.isNotEmpty) ...[
                      Icon(
                        m.expanded
                            ? Icons.expand_less_rounded
                            : Icons.expand_more_rounded,
                        color: colors.toolAccent,
                        size: 18,
                      ),
                      const SizedBox(width: 6),
                    ],
                    Icon(statusIcon, color: statusColor, size: 14),
                    const SizedBox(width: 6),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Row(
                            children: [
                              Text(
                                'Monitor',
                                style: TextStyle(
                                  color: colors.toolAccent,
                                  fontSize: 13,
                                  fontFamily: 'monospace',
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              const SizedBox(width: 6),
                              if (persistent)
                                _Pill(
                                  label: 'persistent',
                                  color: colors.accentLight,
                                ),
                              if (persistent) const SizedBox(width: 4),
                              Expanded(
                                child: Text(
                                  description,
                                  style: TextStyle(
                                    color: colors.toolOutputText,
                                    fontSize: 12,
                                  ),
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                            ],
                          ),
                          if (command.isNotEmpty)
                            Text(
                              command,
                              style: TextStyle(
                                color: colors.textMuted,
                                fontSize: 11,
                                fontFamily: 'monospace',
                              ),
                              overflow: TextOverflow.ellipsis,
                            ),
                          Padding(
                            padding: const EdgeInsets.only(top: 2),
                            child: Row(
                              children: [
                                Icon(
                                  Icons.schedule_rounded,
                                  color: statusColor,
                                  size: 11,
                                ),
                                const SizedBox(width: 4),
                                Text(
                                  timerText,
                                  style: TextStyle(
                                    color: statusColor,
                                    fontSize: 11,
                                    fontFamily: 'monospace',
                                  ),
                                ),
                                const SizedBox(width: 8),
                                Text(
                                  statusLabel,
                                  style: TextStyle(
                                    color: statusColor,
                                    fontSize: 11,
                                  ),
                                ),
                                const SizedBox(width: 8),
                                if (m.monitorTotalEvents > 0)
                                  Text(
                                    m.monitorTotalEvents > events.length
                                        ? 'showing ${events.length} of ${m.monitorTotalEvents}'
                                        : '${m.monitorTotalEvents} '
                                            '${m.monitorTotalEvents == 1 ? "event" : "events"}',
                                    style: TextStyle(
                                      color: colors.textMuted,
                                      fontSize: 11,
                                    ),
                                  ),
                              ],
                            ),
                          ),
                        ],
                      ),
                    ),
                    const SizedBox(width: 8),
                    if (!m.finished)
                      _StopButton(
                        stopping: _stopping,
                        onTap: _onStop,
                      ),
                  ],
                ),
              ),
            ),
            // Body — last N events
            if (m.expanded && events.isNotEmpty)
              Container(
                width: double.infinity,
                padding: const EdgeInsets.fromLTRB(12, 0, 12, 10),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    for (final ev in events)
                      Padding(
                        padding: const EdgeInsets.only(top: 2),
                        child: Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            SizedBox(
                              width: 56,
                              child: Text(
                                startedAt != null
                                    ? _formatRelative(
                                        ev.receivedAt.difference(startedAt),
                                      )
                                    : '',
                                style: TextStyle(
                                  color: colors.textMuted,
                                  fontSize: 11,
                                  fontFamily: 'monospace',
                                ),
                              ),
                            ),
                            Expanded(
                              child: Text(
                                ev.content.trimRight(),
                                style: TextStyle(
                                  color: ev.isError
                                      ? colors.errorText
                                      : colors.toolOutputText,
                                  fontSize: 11,
                                  fontFamily: 'monospace',
                                  height: 1.3,
                                ),
                              ),
                            ),
                          ],
                        ),
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

class _Pill extends StatelessWidget {
  final String label;
  final Color color;
  const _Pill({required this.label, required this.color});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontSize: 10,
          fontWeight: FontWeight.w500,
        ),
      ),
    );
  }
}

class _StopButton extends StatelessWidget {
  final bool stopping;
  final Future<void> Function() onTap;
  const _StopButton({required this.stopping, required this.onTap});

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    return Tooltip(
      message: 'Stop monitor',
      child: GestureDetector(
        onTap: stopping ? null : onTap,
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: kSpace2, vertical: 3),
          decoration: BoxDecoration(
            color: stopping ? colors.bgElevated : colors.errorBg,
            borderRadius: BorderRadius.circular(5),
            border: Border.all(
              color: stopping
                  ? colors.divider
                  : colors.errorText.withValues(alpha: 0.4),
            ),
          ),
          child: stopping
              ? SizedBox(
                  width: 10,
                  height: 10,
                  child: CircularProgressIndicator(
                    strokeWidth: 1.5,
                    color: colors.textMuted,
                  ),
                )
              : Text(
                  'Stop',
                  style: TextStyle(
                    color: colors.errorText,
                    fontSize: 11,
                    fontWeight: FontWeight.w500,
                  ),
                ),
        ),
      ),
    );
  }
}
