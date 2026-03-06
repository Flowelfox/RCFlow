import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/ws_messages.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';

class PlanReviewAskCard extends StatefulWidget {
  final DisplayMessage message;
  const PlanReviewAskCard({super.key, required this.message});

  @override
  State<PlanReviewAskCard> createState() => _PlanReviewAskCardState();
}

class _PlanReviewAskCardState extends State<PlanReviewAskCard> {
  bool _editing = false;
  final _controller = TextEditingController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

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
        child: widget.message.accepted == null
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
            Icon(Icons.assignment_rounded, color: context.appColors.accentLight, size: 18),
            SizedBox(width: 8),
            Expanded(
              child: Text(
                'Plan ready for review',
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 14,
                  fontWeight: FontWeight.w500,
                ),
              ),
            ),
          ],
        ),
        SizedBox(height: 12),
        if (_editing) ...[
          TextField(
            controller: _controller,
            autofocus: true,
            style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
            maxLines: 3,
            minLines: 1,
            decoration: InputDecoration(
              hintText: 'Describe what to change...',
              isDense: true,
              contentPadding:
                  EdgeInsets.symmetric(horizontal: 12, vertical: 10),
              fillColor: context.appColors.bgOverlay,
              border: OutlineInputBorder(
                borderSide: BorderSide.none,
                borderRadius: BorderRadius.circular(8),
              ),
            ),
            onChanged: (_) => setState(() {}),
          ),
          SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: () => setState(() => _editing = false),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: context.appColors.textSecondary,
                    side: BorderSide(color: context.appColors.divider),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                    padding: const EdgeInsets.symmetric(vertical: 12),
                  ),
                  child: const Text('Cancel',
                      style: TextStyle(
                          fontSize: 14, fontWeight: FontWeight.w600)),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: FilledButton(
                  onPressed: _controller.text.trim().isEmpty
                      ? null
                      : () {
                          widget.message.accepted = false;
                          context
                              .read<PaneState>()
                              .sendPrompt(_controller.text.trim());
                        },
                  style: FilledButton.styleFrom(
                    backgroundColor: context.appColors.accent,
                    disabledBackgroundColor: context.appColors.bgElevated,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                    padding: const EdgeInsets.symmetric(vertical: 12),
                  ),
                  child: const Text('Send Feedback',
                      style: TextStyle(
                          fontSize: 14, fontWeight: FontWeight.w600)),
                ),
              ),
            ],
          ),
        ] else
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: () => setState(() => _editing = true),
                  style: OutlinedButton.styleFrom(
                    foregroundColor: context.appColors.textSecondary,
                    side: BorderSide(color: context.appColors.divider),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                    padding: const EdgeInsets.symmetric(vertical: 12),
                  ),
                  child: const Text('Edit',
                      style: TextStyle(
                          fontSize: 14, fontWeight: FontWeight.w600)),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: FilledButton(
                  onPressed: () {
                    widget.message.accepted = true;
                    context
                        .read<PaneState>()
                        .sendPrompt('Looks good, proceed with the plan.');
                  },
                  style: FilledButton.styleFrom(
                    backgroundColor: context.appColors.accent,
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(10),
                    ),
                    padding: const EdgeInsets.symmetric(vertical: 12),
                  ),
                  child: const Text('Approve',
                      style: TextStyle(
                          fontSize: 14, fontWeight: FontWeight.w600)),
                ),
              ),
            ],
          ),
      ],
    );
  }

  Widget _buildResolved() {
    final approved = widget.message.accepted!;
    return Row(
      children: [
        Icon(
          approved ? Icons.check_circle_rounded : Icons.rate_review_rounded,
          color: approved ? context.appColors.successText : context.appColors.accentLight,
          size: 18,
        ),
        SizedBox(width: 8),
        Expanded(
          child: Text(
            approved ? 'Plan approved' : 'Plan feedback sent',
            style: TextStyle(
              color: approved ? context.appColors.textPrimary : context.appColors.textSecondary,
              fontSize: 14,
              fontWeight: FontWeight.w500,
            ),
          ),
        ),
      ],
    );
  }
}
