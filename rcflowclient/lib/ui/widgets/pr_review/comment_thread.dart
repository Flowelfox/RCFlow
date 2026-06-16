import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/app_notification.dart';
import '../../../services/websocket_service.dart';
import '../../../state/app_state.dart';
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

  /// The current GitHub user's login. Comments authored by this login show a
  /// delete action; null disables deletion entirely.
  final String? currentUserLogin;

  const CommentThread({
    super.key,
    required this.ws,
    required this.prId,
    required this.thread,
    required this.onChanged,
    this.onFix,
    this.currentUserLogin,
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

  /// Confirm then delete [c] (the user's own comment) via the backend, then
  /// refresh the threads. Surfaces an app notification on failure.
  Future<void> _deleteComment(DiffThreadComment c) async {
    // Hoist context-derived objects before any await.
    final appState = context.read<AppState>();
    final navigator = Navigator.of(context);

    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) {
        final colors = ctx.appColors;
        return AlertDialog(
          backgroundColor: colors.bgElevated,
          title: Text(
            'Delete this comment?',
            style: TextStyle(color: colors.textPrimary, fontSize: 15),
          ),
          content: Text(
            'This permanently deletes your review comment on GitHub.',
            style: TextStyle(color: colors.textSecondary, fontSize: 13),
          ),
          actions: [
            TextButton(
              onPressed: () => navigator.pop(false),
              child: Text('Cancel', style: TextStyle(color: colors.textMuted)),
            ),
            FilledButton(
              onPressed: () => navigator.pop(true),
              style: FilledButton.styleFrom(backgroundColor: colors.errorText),
              child: const Text('Delete'),
            ),
          ],
        );
      },
    );
    if (confirmed != true) return;

    try {
      await widget.ws.deleteGithubPrComment(widget.prId, c.databaseId);
      await widget.onChanged();
    } catch (e) {
      appState.showNotification(
        level: NotificationLevel.error,
        title: 'Failed to delete comment',
        body: '$e',
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final thread = widget.thread;
    // The user's own comment in this thread (if any) — drives the Delete action
    // in the button row.
    DiffThreadComment? ownComment;
    if (widget.currentUserLogin != null) {
      for (final c in thread.comments) {
        if (c.author == widget.currentUserLogin) {
          ownComment = c;
          break;
        }
      }
    }
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
              if (ownComment != null) ...[
                const SizedBox(width: kGapTight),
                TextButton.icon(
                  onPressed: () => _deleteComment(ownComment!),
                  icon: Icon(
                    Icons.delete_outline,
                    size: 14,
                    color: colors.errorText,
                  ),
                  label: Text(
                    'Delete',
                    style: TextStyle(color: colors.errorText, fontSize: 12),
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
              // Not Expanded: keep the hover area (and its tooltip) tight to the
              // time text instead of spanning the whole row.
              Flexible(child: _timeLabel(context, c.createdAt)),
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

  /// Relative time (e.g. "3h ago") with the exact local datetime on hover.
  /// Falls back to the raw string when it can't be parsed.
  Widget _timeLabel(BuildContext context, String iso) {
    final colors = context.appColors;
    final style = TextStyle(color: colors.textMuted, fontSize: 10);
    final dt = DateTime.tryParse(iso)?.toLocal();
    if (dt == null) {
      return Text(
        iso,
        style: style,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      );
    }
    return Tooltip(
      message: _absoluteLocal(dt),
      child: Text(
        _relativeTime(dt),
        style: style,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
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

String _relativeTime(DateTime dt) {
  final diff = DateTime.now().difference(dt);
  if (diff.isNegative) return 'just now';
  if (diff.inSeconds < 30) return 'just now';
  if (diff.inMinutes < 1) return '${diff.inSeconds}s ago';
  if (diff.inHours < 1) return '${diff.inMinutes}m ago';
  if (diff.inDays < 1) return '${diff.inHours}h ago';
  if (diff.inDays < 30) return '${diff.inDays}d ago';
  if (diff.inDays < 365) return '${(diff.inDays / 30).floor()}mo ago';
  return '${(diff.inDays / 365).floor()}y ago';
}

String _absoluteLocal(DateTime dt) {
  String two(int n) => n.toString().padLeft(2, '0');
  return '${dt.year}-${two(dt.month)}-${two(dt.day)} '
      '${two(dt.hour)}:${two(dt.minute)}:${two(dt.second)}';
}
