import 'package:flutter/material.dart';

import '../../../theme.dart';
import '../../../theme/spacing.dart';

/// How a [DiffViewer] lays out a unified diff.
enum DiffViewMode {
  /// Single column with inline +/- gutters (the original tool-block rendering).
  unified,

  /// Two side-by-side columns: deletions on the left, additions on the right.
  split,
}

/// Classification of a single parsed diff line.
enum DiffLineType { hunk, deletion, addition, context }

/// One comment within a [DiffThread].
class DiffThreadComment {
  /// GitHub GraphQL node id of the comment.
  final String id;

  /// GitHub REST `database_id` — used as the target when replying.
  final int databaseId;
  final String author;
  final String body;
  final String createdAt;

  const DiffThreadComment({
    required this.id,
    required this.databaseId,
    required this.author,
    required this.body,
    required this.createdAt,
  });
}

/// An existing inline review thread anchored to a diff line.
///
/// The pane maps the backend JSON into these before handing them to the
/// [DiffViewer]; the viewer itself stays free of REST/JSON concerns.
class DiffThread {
  /// GitHub GraphQL node id of the thread (the resolve target).
  final String threadId;
  final bool isResolved;
  final bool isOutdated;
  final String path;

  /// Diff line the thread anchors to (interpreted with [side]).
  final int? line;

  /// "LEFT" (old/deletion side) or "RIGHT" (new/addition side).
  final String side;
  final List<DiffThreadComment> comments;

  const DiffThread({
    required this.threadId,
    required this.isResolved,
    required this.isOutdated,
    required this.path,
    required this.line,
    required this.side,
    required this.comments,
  });
}

/// Identifies the diff row that currently has an open inline comment composer.
///
/// Anchored exactly like [DraftComment]: side "LEFT" matches the row whose
/// `oldLineNo == line`, side "RIGHT" matches the row whose `newLineNo == line`.
class ComposerAnchor {
  final String path;
  final int line;

  /// "LEFT" or "RIGHT".
  final String side;

  const ComposerAnchor({
    required this.path,
    required this.line,
    required this.side,
  });
}

/// A locally-queued (not-yet-submitted) inline comment.
class DraftComment {
  /// Index of this comment in the backend draft's `comments` list. Used as the
  /// delete target.
  final int index;
  final String path;
  final int line;

  /// "LEFT" or "RIGHT".
  final String side;
  final String body;

  const DraftComment({
    required this.index,
    required this.path,
    required this.line,
    required this.side,
    required this.body,
  });
}

/// One parsed line of a unified diff, with the resolved line numbers.
class DiffLine {
  final DiffLineType type;
  final String text;
  final int? oldLineNo;
  final int? newLineNo;

  const DiffLine({
    required this.type,
    required this.text,
    this.oldLineNo,
    this.newLineNo,
  });
}

/// Renders a unified diff string with line numbers and red/green colouring.
///
/// Extracted from the tool-block diff renderer so the same widget powers both
/// agent tool output and the PR review pane. [DiffViewMode.unified] reproduces
/// the original inline rendering exactly; [DiffViewMode.split] shows old/new in
/// two side-by-side columns.
class DiffViewer extends StatelessWidget {
  final String diff;
  final DiffViewMode mode;

  /// Existing review threads for the file rendered here. Each is anchored to a
  /// diff row by ([DiffThread.line], [DiffThread.side]) and rendered inline
  /// immediately after that row. Threads whose anchor row cannot be found
  /// (typically outdated) are collected and rendered in a list below the diff.
  ///
  /// Defaults to empty so the tool-block use of [DiffViewer] is unaffected.
  final List<DiffThread> threads;

  /// Locally-queued draft comments for this file, rendered inline (in a
  /// distinct "pending" style) after the row they anchor to. Defaults to empty.
  final List<DraftComment> draftComments;

  /// Invoked when the user taps the add-comment affordance on a diff row.
  /// Receives the resolved ([line], [side]) for that row. When null (the
  /// default, e.g. in tool blocks) no affordance is shown.
  final void Function(int line, String side)? onAddComment;

  /// Builds the inline widget for an existing thread. Required whenever
  /// [threads] is non-empty; the pane supplies a [CommentThread]. Kept as a
  /// builder so [DiffViewer] does not depend on PR-review widgets.
  final Widget Function(DiffThread thread)? threadBuilder;

  /// Builds the inline widget for a queued draft comment. Required whenever
  /// [draftComments] is non-empty.
  final Widget Function(DraftComment comment)? draftBuilder;

  /// The diff row that currently has an open inline comment composer, or null
  /// when no composer is open. Anchored with the same row-matching logic as
  /// [draftComments]. Defaults to null (e.g. in tool blocks).
  final ComposerAnchor? composerAnchor;

  /// Builds the inline composer widget rendered immediately after the row that
  /// [composerAnchor] points at. Required whenever [composerAnchor] is set.
  final Widget Function(ComposerAnchor anchor)? composerBuilder;

  const DiffViewer({
    super.key,
    required this.diff,
    this.mode = DiffViewMode.unified,
    this.threads = const [],
    this.draftComments = const [],
    this.onAddComment,
    this.threadBuilder,
    this.draftBuilder,
    this.composerAnchor,
    this.composerBuilder,
  });

  // Diff add/del colours (copied verbatim from the original tool-block renderer).
  static const Color _delBg = Color(0x33F85149); // red tint
  static const Color _delText = Color(0xFFF85149); // bright red
  static const Color _addBg = Color(0x3356D364); // green tint
  static const Color _addText = Color(0xFF56D364); // bright green

  static const TextStyle _monoStyle = TextStyle(
    fontSize: 11,
    fontFamily: 'monospace',
    height: 1.4,
  );

  /// Parse [diff] into typed rows, tracking line numbers from hunk headers.
  static List<DiffLine> _parse(String diff) {
    final lines = diff.split('\n');
    int oldLine = 0;
    int newLine = 0;

    final rows = <DiffLine>[];
    for (final line in lines) {
      if (line.startsWith('---') || line.startsWith('+++')) {
        // File header — skip, info already in tool summary
        continue;
      }
      if (line.startsWith('@@')) {
        // Parse hunk header: @@ -oldStart,oldCount +newStart,newCount @@
        final match = RegExp(
          r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@',
        ).firstMatch(line);
        if (match != null) {
          oldLine = int.parse(match.group(1)!);
          newLine = int.parse(match.group(2)!);
        }
        rows.add(
          DiffLine(
            type: DiffLineType.hunk,
            text: line,
            oldLineNo: oldLine,
            newLineNo: newLine,
          ),
        );
        continue;
      }
      if (line.startsWith('-')) {
        rows.add(
          DiffLine(type: DiffLineType.deletion, text: line, oldLineNo: oldLine),
        );
        oldLine++;
      } else if (line.startsWith('+')) {
        rows.add(
          DiffLine(type: DiffLineType.addition, text: line, newLineNo: newLine),
        );
        newLine++;
      } else {
        rows.add(
          DiffLine(
            type: DiffLineType.context,
            text: line,
            oldLineNo: oldLine,
            newLineNo: newLine,
          ),
        );
        oldLine++;
        newLine++;
      }
    }
    return rows;
  }

  /// Resolve the ([line], [side]) an add-comment affordance / anchor should use
  /// for [row], or null when the row has no concrete line number (e.g. hunk
  /// headers). Additions and context anchor to the RIGHT (new) side; deletions
  /// anchor to the LEFT (old) side.
  static ({int line, String side})? _rowAnchor(DiffLine row) {
    switch (row.type) {
      case DiffLineType.addition:
      case DiffLineType.context:
        if (row.newLineNo != null) return (line: row.newLineNo!, side: 'RIGHT');
        return null;
      case DiffLineType.deletion:
        if (row.oldLineNo != null) return (line: row.oldLineNo!, side: 'LEFT');
        return null;
      case DiffLineType.hunk:
        return null;
    }
  }

  /// True when [row] is the anchor row for a thread/draft with ([line], [side]).
  ///
  /// side=="RIGHT" anchors to the row whose newLineNo == line; side=="LEFT"
  /// anchors to the row whose oldLineNo == line.
  static bool _rowMatches(DiffLine row, int? line, String side) {
    if (line == null) return false;
    if (side == 'LEFT') return row.oldLineNo == line;
    return row.newLineNo == line; // RIGHT (default)
  }

  @override
  Widget build(BuildContext context) {
    final rows = _parse(diff);

    // Gutter width based on max line number
    final maxLineNo = rows.fold<int>(0, (m, r) {
      final n = r.newLineNo ?? r.oldLineNo ?? 0;
      return n > m ? n : m;
    });
    final gutterChars = maxLineNo.toString().length;

    // Track which threads/drafts found an anchor row; anything left over is
    // rendered as "outdated" below the diff so nothing is silently dropped.
    final anchoredThreads = <DiffThread>{};
    final anchoredDrafts = <DraftComment>{};
    // Whether the single inline composer (if any) has been placed yet.
    var composerRendered = false;

    final colors = context.appColors;

    final children = <Widget>[];
    for (final row in rows) {
      children.add(
        mode == DiffViewMode.split
            ? _buildSplitRow(context, row, gutterChars)
            : _buildUnifiedRow(context, row, gutterChars),
      );

      // Inline existing threads anchored to this row.
      if (threadBuilder != null) {
        for (final t in threads) {
          if (anchoredThreads.contains(t)) continue;
          if (_rowMatches(row, t.line, t.side)) {
            anchoredThreads.add(t);
            children.add(threadBuilder!(t));
          }
        }
      }

      // Inline queued draft comments anchored to this row.
      if (draftBuilder != null) {
        for (final d in draftComments) {
          if (anchoredDrafts.contains(d)) continue;
          if (_rowMatches(row, d.line, d.side)) {
            anchoredDrafts.add(d);
            children.add(draftBuilder!(d));
          }
        }
      }

      // Inline composer anchored to this row (at most one).
      final anchor = composerAnchor;
      if (composerBuilder != null &&
          anchor != null &&
          !composerRendered &&
          _rowMatches(row, anchor.line, anchor.side)) {
        composerRendered = true;
        children.add(composerBuilder!(anchor));
      }
    }

    // Threads whose anchor row was not found (outdated / wrong file slice).
    final outdated = threadBuilder == null
        ? const <DiffThread>[]
        : threads.where((t) => !anchoredThreads.contains(t)).toList();

    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 2, 12, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(kRadiusSmall),
            child: Container(
              width: double.infinity,
              decoration: BoxDecoration(
                border: Border.all(color: colors.divider),
                borderRadius: BorderRadius.circular(kRadiusSmall),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: children,
              ),
            ),
          ),
          if (outdated.isNotEmpty) ...[
            const SizedBox(height: kGapTight),
            Text(
              'Outdated threads',
              style: TextStyle(
                color: colors.textMuted,
                fontSize: 11,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: kGapInline),
            for (final t in outdated) threadBuilder!(t),
          ],
        ],
      ),
    );
  }

  // ---------------------------------------------------------------------------
  // Unified rendering (identical to the original tool-block renderer)
  // ---------------------------------------------------------------------------

  Widget _buildUnifiedRow(BuildContext context, DiffLine row, int gutterChars) {
    final colors = context.appColors;

    Color bgColor;
    Color textColor;
    String oldGutter;
    String newGutter;

    switch (row.type) {
      case DiffLineType.hunk:
        bgColor = colors.accentDim.withValues(alpha: 0.3);
        textColor = colors.accentLight;
        oldGutter = (row.oldLineNo?.toString() ?? '').padLeft(gutterChars);
        newGutter = (row.newLineNo?.toString() ?? '').padLeft(gutterChars);
      case DiffLineType.deletion:
        bgColor = _delBg;
        textColor = _delText;
        oldGutter = (row.oldLineNo?.toString() ?? '').padLeft(gutterChars);
        newGutter = ''.padLeft(gutterChars);
      case DiffLineType.addition:
        bgColor = _addBg;
        textColor = _addText;
        oldGutter = ''.padLeft(gutterChars);
        newGutter = (row.newLineNo?.toString() ?? '').padLeft(gutterChars);
      case DiffLineType.context:
        bgColor = Colors.transparent;
        textColor = colors.toolOutputText;
        oldGutter = (row.oldLineNo?.toString() ?? '').padLeft(gutterChars);
        newGutter = (row.newLineNo?.toString() ?? '').padLeft(gutterChars);
    }

    final anchor = _rowAnchor(row);
    return Container(
      color: bgColor,
      padding: const EdgeInsets.fromLTRB(8, 1, 8, 1),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: (gutterChars * 7.0) + 4,
            child: Text(
              oldGutter,
              style: _monoStyle.copyWith(color: colors.textMuted),
            ),
          ),
          SizedBox(
            width: (gutterChars * 7.0) + 4,
            child: Text(
              newGutter,
              style: _monoStyle.copyWith(color: colors.textMuted),
            ),
          ),
          const SizedBox(width: kGapTight),
          Expanded(
            child: Text(row.text, style: _monoStyle.copyWith(color: textColor)),
          ),
          if (onAddComment != null && anchor != null)
            _addCommentButton(context, anchor),
        ],
      ),
    );
  }

  /// A compact "add inline comment" affordance shown at the end of a diff row.
  Widget _addCommentButton(
    BuildContext context,
    ({int line, String side}) anchor,
  ) {
    final colors = context.appColors;
    return SizedBox(
      width: 18,
      height: 16,
      child: IconButton(
        padding: EdgeInsets.zero,
        constraints: const BoxConstraints(),
        iconSize: 13,
        splashRadius: 12,
        tooltip: 'Comment on line ${anchor.line}',
        icon: Icon(Icons.add_comment_outlined, color: colors.textMuted),
        onPressed: () => onAddComment!(anchor.line, anchor.side),
      ),
    );
  }

  // ---------------------------------------------------------------------------
  // Split rendering (old | new side-by-side)
  // ---------------------------------------------------------------------------

  Widget _buildSplitRow(BuildContext context, DiffLine row, int gutterChars) {
    final colors = context.appColors;

    // Hunk headers span the full width.
    if (row.type == DiffLineType.hunk) {
      return Container(
        color: colors.accentDim.withValues(alpha: 0.3),
        padding: const EdgeInsets.fromLTRB(8, 1, 8, 1),
        child: Text(
          row.text,
          style: _monoStyle.copyWith(color: colors.accentLight),
        ),
      );
    }

    // Resolve which side(s) the row appears on.
    final String oldText;
    final Color oldBg;
    final Color oldText2;
    final String newText;
    final Color newBg;
    final Color newText2;

    switch (row.type) {
      case DiffLineType.deletion:
        oldText = row.text;
        oldBg = _delBg;
        oldText2 = _delText;
        newText = '';
        newBg = Colors.transparent;
        newText2 = colors.toolOutputText;
      case DiffLineType.addition:
        oldText = '';
        oldBg = Colors.transparent;
        oldText2 = colors.toolOutputText;
        newText = row.text;
        newBg = _addBg;
        newText2 = _addText;
      case DiffLineType.context:
        oldText = row.text;
        oldBg = Colors.transparent;
        oldText2 = colors.toolOutputText;
        newText = row.text;
        newBg = Colors.transparent;
        newText2 = colors.toolOutputText;
      case DiffLineType.hunk:
        // Handled above; keep the switch exhaustive.
        oldText = '';
        oldBg = Colors.transparent;
        oldText2 = colors.toolOutputText;
        newText = '';
        newBg = Colors.transparent;
        newText2 = colors.toolOutputText;
    }

    final anchor = _rowAnchor(row);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(
          child: _buildSplitCell(
            context,
            gutter: row.oldLineNo,
            text: oldText,
            bgColor: oldBg,
            textColor: oldText2,
            gutterChars: gutterChars,
          ),
        ),
        Container(width: 1, color: colors.divider),
        Expanded(
          child: _buildSplitCell(
            context,
            gutter: row.newLineNo,
            text: newText,
            bgColor: newBg,
            textColor: newText2,
            gutterChars: gutterChars,
          ),
        ),
        if (onAddComment != null && anchor != null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 2),
            child: _addCommentButton(context, anchor),
          ),
      ],
    );
  }

  Widget _buildSplitCell(
    BuildContext context, {
    required int? gutter,
    required String text,
    required Color bgColor,
    required Color textColor,
    required int gutterChars,
  }) {
    final colors = context.appColors;
    return Container(
      color: bgColor,
      padding: const EdgeInsets.fromLTRB(8, 1, 8, 1),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: (gutterChars * 7.0) + 4,
            child: Text(
              (gutter?.toString() ?? '').padLeft(gutterChars),
              style: _monoStyle.copyWith(color: colors.textMuted),
            ),
          ),
          const SizedBox(width: kGapTight),
          Expanded(
            child: Text(text, style: _monoStyle.copyWith(color: textColor)),
          ),
        ],
      ),
    );
  }
}
