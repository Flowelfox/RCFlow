import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/ws_messages.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';

/// Shown in the message stream when Claude Code automatically paused the
/// session because it reached its configured --max-turns limit.
class MaxTurnsPauseCard extends StatelessWidget {
  final DisplayMessage message;
  const MaxTurnsPauseCard({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: const Color(0xFF291D00).withAlpha(180),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: const Color(0xFFFBBF24).withAlpha(100)),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            const Icon(
              Icons.hourglass_bottom_rounded,
              color: Color(0xFFFBBF24),
              size: 18,
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Turn limit reached',
                    style: TextStyle(
                      color: context.appColors.textPrimary,
                      fontSize: 14,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    'Claude Code hit its configured turn limit and paused. '
                    'Send a message to continue.',
                    style: TextStyle(
                      color: context.appColors.textSecondary,
                      fontSize: 13,
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(width: 10),
            _ResumeButton(sessionId: message.sessionId),
          ],
        ),
      ),
    );
  }
}

class _ResumeButton extends StatelessWidget {
  final String? sessionId;
  const _ResumeButton({this.sessionId});

  @override
  Widget build(BuildContext context) {
    final pane = context.watch<PaneState>();
    final sid = sessionId ?? pane.sessionId;
    if (sid == null || !pane.sessionPaused) return const SizedBox.shrink();

    return OutlinedButton.icon(
      onPressed: () => pane.resumeSession(sid),
      icon: const Icon(Icons.play_arrow_rounded, size: 16),
      label: const Text('Resume'),
      style: OutlinedButton.styleFrom(
        foregroundColor: const Color(0xFFFBBF24),
        side: const BorderSide(color: Color(0xFFFBBF24), width: 1),
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        textStyle: const TextStyle(fontSize: 13, fontWeight: FontWeight.w600),
      ),
    );
  }
}
