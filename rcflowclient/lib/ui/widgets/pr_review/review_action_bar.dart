import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/app_notification.dart';
import '../../../models/github_pr_info.dart';
import '../../../services/websocket_service.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import '../../../theme/spacing.dart';
import '../diff/diff_viewer.dart';

/// The three review verdicts GitHub accepts.
enum ReviewVerdict { approve, requestChanges, comment }

extension _ReviewVerdictApi on ReviewVerdict {
  /// The backend/GitHub event name for this verdict.
  String get event => switch (this) {
    ReviewVerdict.approve => 'APPROVE',
    ReviewVerdict.requestChanges => 'REQUEST_CHANGES',
    ReviewVerdict.comment => 'COMMENT',
  };

  static ReviewVerdict fromEvent(String? event) => switch (event) {
    'APPROVE' => ReviewVerdict.approve,
    'REQUEST_CHANGES' => ReviewVerdict.requestChanges,
    _ => ReviewVerdict.comment,
  };
}

/// Bottom review panel: verdict picker, summary box, queued-comments list,
/// "Submit review", and "Merge".
///
/// Holds local UI state (verdict, summary text). Mutations go through [ws];
/// after submit/merge the matching callback refetches PR + threads + draft.
class ReviewActionBar extends StatefulWidget {
  final WebSocketService ws;
  final GithubPrInfo pr;

  /// The current draft's verdict event (defaults to "COMMENT").
  final String draftEvent;

  /// The current draft summary body.
  final String draftBody;

  /// Queued inline comments from the draft.
  final List<DraftComment> draftComments;

  /// Called after a successful review submission. Receives the result map from
  /// `POST /review` so the caller can surface a snackbar + refresh.
  final Future<void> Function(Map<String, dynamic> result) onSubmitted;

  /// Called after a successful merge with the result map from `POST /merge`.
  final Future<void> Function(Map<String, dynamic> result) onMerged;

  /// Called to remove a queued draft comment (delegates to the pane, which
  /// hits the delete endpoint and refreshes the draft).
  final Future<void> Function(DraftComment comment) onRemoveDraftComment;

  const ReviewActionBar({
    super.key,
    required this.ws,
    required this.pr,
    required this.draftEvent,
    required this.draftBody,
    required this.draftComments,
    required this.onSubmitted,
    required this.onMerged,
    required this.onRemoveDraftComment,
  });

  @override
  State<ReviewActionBar> createState() => _ReviewActionBarState();
}

class _ReviewActionBarState extends State<ReviewActionBar> {
  late ReviewVerdict _verdict;
  late final TextEditingController _summaryController;
  bool _submitting = false;
  bool _merging = false;

  @override
  void initState() {
    super.initState();
    _verdict = _ReviewVerdictApi.fromEvent(widget.draftEvent);
    _summaryController = TextEditingController(text: widget.draftBody);
    // Re-evaluate the Submit button's enabled state as the summary is typed.
    _summaryController.addListener(_onSummaryChanged);
  }

  void _onSummaryChanged() {
    if (mounted) setState(() {});
  }

  /// A review can be submitted only when GitHub will accept it: an Approve
  /// needs nothing, but Request-changes and Comment require a summary body or at
  /// least one inline comment (GitHub returns 422 otherwise).
  bool get _canSubmit =>
      _verdict == ReviewVerdict.approve ||
      _summaryController.text.trim().isNotEmpty ||
      widget.draftComments.isNotEmpty;

  @override
  void didUpdateWidget(ReviewActionBar oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Re-sync from the draft when the pane refetches it (e.g. after a queued
    // comment changes), but don't clobber what the user is mid-edit on.
    if (oldWidget.draftEvent != widget.draftEvent) {
      _verdict = _ReviewVerdictApi.fromEvent(widget.draftEvent);
    }
    if (oldWidget.draftBody != widget.draftBody &&
        _summaryController.text == oldWidget.draftBody) {
      _summaryController.text = widget.draftBody;
    }
  }

  @override
  void dispose() {
    _summaryController.removeListener(_onSummaryChanged);
    _summaryController.dispose();
    super.dispose();
  }

  bool get _canMerge =>
      widget.pr.state == 'open' && !widget.pr.draft && !widget.pr.isMerged;

  Future<void> _submit() async {
    setState(() => _submitting = true);
    final appState = context.read<AppState>();
    try {
      // Persist the chosen verdict + summary, then post the review.
      await widget.ws.patchGithubPrDraft(
        widget.pr.id,
        event: _verdict.event,
        body: _summaryController.text,
      );
      final result = await widget.ws.submitGithubPrReview(
        widget.pr.id,
        event: _verdict.event,
        body: _summaryController.text,
      );
      final state =
          (result['review'] as Map<String, dynamic>?)?['state'] as String?;
      appState.showNotification(
        level: NotificationLevel.success,
        title: 'Review submitted${state != null ? ' ($state)' : ''}',
      );
      await widget.onSubmitted(result);
    } catch (e) {
      appState.showNotification(
        level: NotificationLevel.error,
        title: 'Failed to submit review',
        body: '$e',
      );
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  Future<void> _merge() async {
    final appState = context.read<AppState>();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: ctx.appColors.bgElevated,
        title: Text(
          'Merge pull request?',
          style: TextStyle(color: ctx.appColors.textPrimary, fontSize: 16),
        ),
        content: Text(
          'Squash-merge #${widget.pr.number} "${widget.pr.title}" into '
          '${widget.pr.baseRef}? This cannot be undone.',
          style: TextStyle(color: ctx.appColors.textSecondary, fontSize: 13),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text(
              'Cancel',
              style: TextStyle(color: ctx.appColors.textMuted),
            ),
          ),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: FilledButton.styleFrom(
              backgroundColor: ctx.appColors.accent,
            ),
            child: const Text('Merge'),
          ),
        ],
      ),
    );
    if (confirmed != true) return;

    setState(() => _merging = true);
    try {
      final result = await widget.ws.mergeGithubPr(
        widget.pr.id,
        method: 'squash',
      );
      final merged = result['merged'] as bool? ?? false;
      final message = result['message'] as String?;
      appState.showNotification(
        level: merged ? NotificationLevel.success : NotificationLevel.error,
        title: merged ? 'Pull request merged' : 'Merge failed',
        body: merged ? null : message,
      );
      await widget.onMerged(result);
    } catch (e) {
      appState.showNotification(
        level: NotificationLevel.error,
        title: 'Failed to merge',
        body: '$e',
      );
    } finally {
      if (mounted) setState(() => _merging = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final busy = _submitting || _merging;
    return Container(
      decoration: BoxDecoration(
        color: colors.bgSurface,
        border: Border(top: BorderSide(color: colors.divider)),
      ),
      padding: const EdgeInsets.all(kSpace3),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          Row(
            children: [
              Expanded(child: _buildVerdictPicker(context)),
              const SizedBox(width: kGapRelaxed),
              _buildMergeButton(context),
            ],
          ),
          const SizedBox(height: kGapTight),
          if (widget.draftComments.isNotEmpty) _buildQueuedComments(context),
          TextField(
            controller: _summaryController,
            enabled: !busy,
            minLines: 2,
            maxLines: 5,
            style: TextStyle(color: colors.textPrimary, fontSize: 13),
            decoration: InputDecoration(
              hintText: 'Review summary (optional)…',
              isDense: true,
              contentPadding: const EdgeInsets.symmetric(
                horizontal: kSpace3,
                vertical: kSpace2,
              ),
              filled: true,
              fillColor: colors.bgOverlay,
              border: OutlineInputBorder(
                borderSide: BorderSide.none,
                borderRadius: BorderRadius.circular(kRadiusSmall),
              ),
            ),
          ),
          const SizedBox(height: kGapTight),
          SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              onPressed: (busy || !_canSubmit) ? null : _submit,
              icon: _submitting
                  ? const SizedBox(
                      width: 14,
                      height: 14,
                      child: CircularProgressIndicator(
                        strokeWidth: 2,
                        color: Colors.white,
                      ),
                    )
                  : const Icon(Icons.rate_review_outlined, size: 16),
              label: Text(
                'Submit review'
                '${widget.draftComments.isNotEmpty ? ' (${widget.draftComments.length})' : ''}',
              ),
              style: FilledButton.styleFrom(
                backgroundColor: colors.accent,
                disabledBackgroundColor: colors.bgElevated,
                padding: const EdgeInsets.symmetric(vertical: kSpace3),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(kRadiusSmall),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildVerdictPicker(BuildContext context) {
    return SegmentedButton<ReviewVerdict>(
      segments: const [
        ButtonSegment(
          value: ReviewVerdict.approve,
          label: Text('Approve', style: TextStyle(fontSize: 12)),
          icon: Icon(Icons.check_circle_outline, size: 14),
        ),
        ButtonSegment(
          value: ReviewVerdict.requestChanges,
          label: Text('Request changes', style: TextStyle(fontSize: 12)),
          icon: Icon(Icons.error_outline, size: 14),
        ),
        ButtonSegment(
          value: ReviewVerdict.comment,
          label: Text('Comment', style: TextStyle(fontSize: 12)),
          icon: Icon(Icons.chat_bubble_outline, size: 14),
        ),
      ],
      selected: {_verdict},
      showSelectedIcon: false,
      onSelectionChanged: (_submitting || _merging)
          ? null
          : (s) => setState(() => _verdict = s.first),
    );
  }

  Widget _buildMergeButton(BuildContext context) {
    final colors = context.appColors;
    return OutlinedButton.icon(
      onPressed: (_canMerge && !_merging && !_submitting) ? _merge : null,
      icon: _merging
          ? SizedBox(
              width: 14,
              height: 14,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: colors.textMuted,
              ),
            )
          : const Icon(Icons.merge, size: 16),
      label: const Text('Merge', style: TextStyle(fontSize: 12)),
      style: OutlinedButton.styleFrom(
        foregroundColor: colors.textSecondary,
        side: BorderSide(color: colors.divider),
        padding: const EdgeInsets.symmetric(
          horizontal: kSpace3,
          vertical: kSpace2,
        ),
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(kRadiusSmall),
        ),
      ),
    );
  }

  Widget _buildQueuedComments(BuildContext context) {
    final colors = context.appColors;
    return Padding(
      padding: const EdgeInsets.only(bottom: kGapTight),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Queued comments (${widget.draftComments.length})',
            style: TextStyle(
              color: colors.textMuted,
              fontSize: 11,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: kGapInline),
          for (final c in widget.draftComments)
            Container(
              margin: const EdgeInsets.only(bottom: kSpace1),
              padding: const EdgeInsets.symmetric(
                horizontal: kSpace2,
                vertical: kSpace1,
              ),
              decoration: BoxDecoration(
                color: colors.bgElevated,
                borderRadius: BorderRadius.circular(kRadiusSmall),
                border: Border.all(color: colors.divider),
              ),
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '${c.path}:${c.line} (${c.side})',
                          style: TextStyle(
                            color: colors.textMuted,
                            fontSize: 10,
                            fontFamily: 'monospace',
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                        Text(
                          c.body,
                          style: TextStyle(
                            color: colors.textSecondary,
                            fontSize: 12,
                          ),
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ],
                    ),
                  ),
                  IconButton(
                    padding: EdgeInsets.zero,
                    constraints: const BoxConstraints(),
                    iconSize: 15,
                    splashRadius: 14,
                    tooltip: 'Remove queued comment',
                    icon: Icon(Icons.close, color: colors.textMuted),
                    onPressed: (_submitting || _merging)
                        ? null
                        : () => widget.onRemoveDraftComment(c),
                  ),
                ],
              ),
            ),
        ],
      ),
    );
  }
}
