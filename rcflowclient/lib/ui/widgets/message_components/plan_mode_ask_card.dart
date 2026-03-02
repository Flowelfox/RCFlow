import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/ws_messages.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';

class PlanModeAskCard extends StatelessWidget {
  final DisplayMessage message;
  const PlanModeAskCard({super.key, required this.message});

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
        const Row(
          children: [
            Icon(Icons.edit_note_rounded, color: kAccentLight, size: 18),
            SizedBox(width: 8),
            Expanded(
              child: Text(
                'Claude Code wants to enter plan mode',
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
                  message.accepted = false;
                  context.read<PaneState>().sendPrompt('no');
                },
                style: OutlinedButton.styleFrom(
                  foregroundColor: kTextSecondary,
                  side: const BorderSide(color: kDivider),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                ),
                child: const Text('Deny',
                    style:
                        TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
              ),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: FilledButton(
                onPressed: () {
                  message.accepted = true;
                  context.read<PaneState>().sendPrompt('yes');
                },
                style: FilledButton.styleFrom(
                  backgroundColor: kAccent,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                ),
                child: const Text('Allow',
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
    final allowed = message.accepted!;
    return Row(
      children: [
        Icon(
          allowed ? Icons.edit_note_rounded : Icons.block_rounded,
          color: allowed ? kAccentLight : kTextSecondary,
          size: 18,
        ),
        const SizedBox(width: 8),
        Expanded(
          child: Text(
            allowed ? 'Plan mode allowed' : 'Plan mode denied',
            style: TextStyle(
              color: allowed ? kTextPrimary : kTextSecondary,
              fontSize: 14,
              fontWeight: FontWeight.w500,
            ),
          ),
        ),
      ],
    );
  }
}
