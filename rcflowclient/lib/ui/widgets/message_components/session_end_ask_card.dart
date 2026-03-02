import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/ws_messages.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';

class SessionEndAskCard extends StatelessWidget {
  final DisplayMessage message;
  const SessionEndAskCard({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: kAccentDim.withAlpha(60),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: kAccent.withAlpha(80)),
        ),
        child: message.accepted == null
            ? _buildPending(context)
            : _buildResolved(),
      ),
    );
  }

  Widget _buildPending(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            const Icon(Icons.check_circle_rounded,
                color: kSuccessText, size: 18),
            const SizedBox(width: 8),
            const Expanded(
              child: Text(
                'Task complete. End this chat?',
                style: TextStyle(
                  color: kTextPrimary,
                  fontSize: 14,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        Row(
          children: [
            Expanded(
              child: OutlinedButton(
                onPressed: () {
                  context.read<PaneState>().dismissSessionEndAsk();
                },
                style: OutlinedButton.styleFrom(
                  foregroundColor: kTextSecondary,
                  side: const BorderSide(color: kDivider),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                ),
                child: const Text('Continue',
                    style:
                        TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: FilledButton(
                onPressed: () {
                  final pane = context.read<PaneState>();
                  final sessionId = message.sessionId ?? pane.sessionId;
                  if (sessionId != null) {
                    pane.endSession(sessionId);
                  }
                },
                style: FilledButton.styleFrom(
                  backgroundColor: kAccent,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                ),
                child: const Text('End Session',
                    style:
                        TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildResolved() {
    final ended = message.accepted!;
    return Row(
      children: [
        Icon(
          ended ? Icons.check_circle_rounded : Icons.arrow_forward_rounded,
          color: ended ? kSuccessText : kTextSecondary,
          size: 18,
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            ended ? 'Session ended by user' : 'User continued the session',
            style: TextStyle(
              color: ended ? kTextPrimary : kTextSecondary,
              fontSize: 14,
              fontWeight: FontWeight.w500,
            ),
          ),
        ),
      ],
    );
  }
}
