import 'package:flutter/material.dart';

import '../../../services/websocket_service.dart';
import '../../../theme.dart';
import '../../../theme/spacing.dart';
import '../diff/diff_viewer.dart';

/// Renders a single GitHub review thread: its comments, a resolved badge, an
/// inline reply composer, and a resolve/unresolve toggle.
///
/// All mutations go through [ws]; after a successful reply or resolve the
/// [onChanged] callback is invoked so the parent can refetch threads.
class CommentThread extends StatefulWidget {
  final WebSocketService ws;
  final String prId;
  final DiffThread thread;

  /// Called after a successful reply or resolve so the parent can refresh.
  final Future<void> Function() onChanged;

  /// When non-null, renders a "Fix with agent" action that hands this thread
  /// off to a full-perms agent session (see [AppState.startPrAssist]).
  final void Function()? onFix;

  const CommentThread({
    super.key,
    required this.ws,
    required this.prId,
    required this.thread,
    required this.onChanged,
    this.onFix,
  });

  @override
  State<CommentThread> createState() => _CommentThreadState();
}

class _CommentThreadState extends State<CommentThread> {
  final _replyController = TextEditingController();
  bool _replying = false;
  bool _submittingReply = false;
  bool _togglingResolve = false;
  String? _error;

  @override
  void dispose() {
    _replyController.dispose();
    super.dispose();
  }

  Future<void> _sendReply() async {
    final text = _replyController.text.trim();
    if (text.isEmpty) return;
    final comments = widget.thread.comments;
    if (comments.isEmpty) return;
    setState(() {
      _submittingReply = true;
      _error = null;
    });
    try {
      // Reply targets the thread's first comment by its database_id.
      await widget.ws.replyGithubPrComment(
        widget.prId,
        comments.first.databaseId,
        text,
      );
      _replyController.clear();
      if (mounted) setState(() => _replying = false);
      await widget.onChanged();
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _submittingReply = false);
    }
  }

  Future<void> _toggleResolve() async {
    setState(() {
      _togglingResolve = true;
      _error = null;
    });
    try {
      await widget.ws.resolveGithubPrThread(
        widget.prId,
        widget.thread.threadId,
        !widget.thread.isResolved,
      );
      await widget.onChanged();
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _togglingResolve = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final thread = widget.thread;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.symmetric(
        horizontal: kSpace2,
        vertical: kSpace1,
      ),
      padding: const EdgeInsets.all(kSpace3),
      decoration: BoxDecoration(
        color: colors.bgElevated,
        borderRadius: BorderRadius.circular(kRadiusMedium),
        border: Border.all(color: colors.divider),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.forum_outlined, size: 13, color: colors.textMuted),
              const SizedBox(width: kGapInline),
              Expanded(
                child: Text(
                  '${thread.path}'
                  '${thread.line != null ? ':${thread.line}' : ''}',
                  style: TextStyle(
                    color: colors.textMuted,
                    fontSize: 11,
                    fontFamily: 'monospace',
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
              if (thread.isOutdated)
                _badge(context, 'Outdated', colors.textMuted),
              if (thread.isResolved) ...[
                const SizedBox(width: kGapInline),
                _badge(context, 'Resolved', colors.successText),
              ],
            ],
          ),
          const SizedBox(height: kGapTight),
          for (final c in thread.comments) _buildComment(context, c),
          const SizedBox(height: kGapInline),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: kGapInline),
              child: Text(
                _error!,
                style: TextStyle(color: colors.errorText, fontSize: 11),
              ),
            ),
          Row(
            children: [
              TextButton.icon(
                onPressed: _submittingReply
                    ? null
                    : () => setState(() => _replying = !_replying),
                icon: Icon(Icons.reply, size: 14, color: colors.accentLight),
                label: Text(
                  'Reply',
                  style: TextStyle(color: colors.accentLight, fontSize: 12),
                ),
                style: TextButton.styleFrom(
                  padding: const EdgeInsets.symmetric(
                    horizontal: kSpace2,
                    vertical: kSpace1,
                  ),
                  minimumSize: Size.zero,
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
              ),
              const SizedBox(width: kGapTight),
              TextButton.icon(
                onPressed: _togglingResolve ? null : _toggleResolve,
                icon: _togglingResolve
                    ? SizedBox(
                        width: 12,
                        height: 12,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: colors.textMuted,
                        ),
                      )
                    : Icon(
                        thread.isResolved
                            ? Icons.replay_outlined
                            : Icons.check_circle_outline,
                        size: 14,
                        color: colors.textSecondary,
                      ),
                label: Text(
                  thread.isResolved ? 'Unresolve' : 'Resolve',
                  style: TextStyle(color: colors.textSecondary, fontSize: 12),
                ),
                style: TextButton.styleFrom(
                  padding: const EdgeInsets.symmetric(
                    horizontal: kSpace2,
                    vertical: kSpace1,
                  ),
                  minimumSize: Size.zero,
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
              ),
              if (widget.onFix != null) ...[
                const SizedBox(width: kGapTight),
                TextButton.icon(
                  onPressed: widget.onFix,
                  icon: Icon(
                    Icons.auto_fix_high,
                    size: 14,
                    color: colors.accentLight,
                  ),
                  label: Text(
                    'Fix with agent',
                    style: TextStyle(color: colors.accentLight, fontSize: 12),
                  ),
                  style: TextButton.styleFrom(
                    padding: const EdgeInsets.symmetric(
                      horizontal: kSpace2,
                      vertical: kSpace1,
                    ),
                    minimumSize: Size.zero,
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  ),
                ),
              ],
            ],
          ),
          if (_replying) ...[
            const SizedBox(height: kGapTight),
            TextField(
              controller: _replyController,
              enabled: !_submittingReply,
              minLines: 1,
              maxLines: 4,
              style: TextStyle(color: colors.textPrimary, fontSize: 13),
              decoration: InputDecoration(
                hintText: 'Reply to this thread…',
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
            Align(
              alignment: Alignment.centerRight,
              child: FilledButton(
                onPressed: _submittingReply ? null : _sendReply,
                style: FilledButton.styleFrom(
                  backgroundColor: colors.accent,
                  padding: const EdgeInsets.symmetric(
                    horizontal: kSpace4,
                    vertical: kSpace2,
                  ),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(kRadiusSmall),
                  ),
                ),
                child: _submittingReply
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: Colors.white,
                        ),
                      )
                    : const Text('Send', style: TextStyle(fontSize: 12)),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildComment(BuildContext context, DiffThreadComment c) {
    final colors = context.appColors;
    return Padding(
      padding: const EdgeInsets.only(bottom: kGapTight),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                c.author,
                style: TextStyle(
                  color: colors.textPrimary,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(width: kGapTight),
              Expanded(
                child: Text(
                  c.createdAt,
                  style: TextStyle(color: colors.textMuted, fontSize: 10),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          const SizedBox(height: 2),
          SelectableText(
            c.body,
            style: TextStyle(color: colors.textSecondary, fontSize: 12),
          ),
        ],
      ),
    );
  }

  Widget _badge(BuildContext context, String label, Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 1),
      decoration: BoxDecoration(
        color: color.withAlpha(30),
        borderRadius: BorderRadius.circular(kRadiusSmall),
        border: Border.all(color: color.withAlpha(80), width: 0.5),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontSize: 9,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}
