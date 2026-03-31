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
      padding: EdgeInsets.symmetric(vertical: 8),
      child: Container(
        width: double.infinity,
        padding: EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: context.appColors.accentDim.withAlpha(60),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: context.appColors.accent.withAlpha(80)),
        ),
        child: message.accepted == null
            ? _buildPending(context)
            : _buildResolved(context),
      ),
    );
  }

  Widget _buildPending(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(
              Icons.edit_note_rounded,
              color: context.appColors.accentLight,
              size: 18,
            ),
            SizedBox(width: 8),
            Expanded(
              child: Text(
                'Claude Code wants to enter plan mode',
                style: TextStyle(
                  color: context.appColors.textPrimary,
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
                  context.read<PaneState>().sendInteractiveResponse(
                    message,
                    'no',
                    accepted: false,
                  );
                },
                style: OutlinedButton.styleFrom(
                  foregroundColor: context.appColors.textSecondary,
                  side: BorderSide(color: context.appColors.divider),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                ),
                child: const Text(
                  'Deny',
                  style: TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
                ),
              ),
            ),
            SizedBox(width: 10),
            Expanded(
              child: FilledButton(
                onPressed: () {
                  context.read<PaneState>().sendInteractiveResponse(
                    message,
                    'yes',
                  );
                },
                style: FilledButton.styleFrom(
                  backgroundColor: context.appColors.accent,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                ),
                child: const Text(
                  'Allow',
                  style: TextStyle(fontSize: 14, fontWeight: FontWeight.w600),
                ),
              ),
            ),
          ],
        ),
      ],
    );
  }

  Widget _buildResolved(BuildContext context) {
    final allowed = message.accepted!;
    return Row(
      children: [
        Icon(
          allowed ? Icons.edit_note_rounded : Icons.block_rounded,
          color: allowed
              ? context.appColors.accentLight
              : context.appColors.textSecondary,
          size: 18,
        ),
        SizedBox(width: 8),
        Expanded(
          child: Text(
            allowed ? 'Plan mode allowed' : 'Plan mode denied',
            style: TextStyle(
              color: allowed
                  ? context.appColors.textPrimary
                  : context.appColors.textSecondary,
              fontSize: 14,
              fontWeight: FontWeight.w500,
            ),
          ),
        ),
      ],
    );
  }
}
