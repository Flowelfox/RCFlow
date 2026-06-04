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

  /// First line of a multi-line range selection, or null for a single-line
  /// comment. The composer always anchors after the END row ([line]); when set,
  /// the queued comment is posted to GitHub spanning [startLine]..[line].
  final int? startLine;

  const ComposerAnchor({
    required this.path,
    required this.line,
    required this.side,
    this.startLine,
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

/// A contiguous run of NEW-side lines hidden between (or around) the patch
/// hunks, expandable GitHub-style. Inserted into the row list as a band marker.
///
/// [startNew]..[endNew] are the inclusive 1-based new-side line numbers that
/// are currently hidden. [endNew] is null for the trailing gap after the last
/// hunk, whose true extent is unknown until the file content is fetched.
class _Gap {
  final int startNew;
  final int? endNew;

  const _Gap({required this.startNew, this.endNew});

  /// Number of hidden lines, or null when unbounded (trailing gap).
  int? get count => endNew == null ? null : (endNew! - startNew + 1);
}

/// Renders a unified diff string with line numbers and red/green colouring.
///
/// Extracted from the tool-block diff renderer so the same widget powers both
/// agent tool output and the PR review pane. [DiffViewMode.unified] reproduces
/// the original inline rendering exactly; [DiffViewMode.split] shows old/new in
/// two side-by-side columns.
///
/// When the comment callbacks are wired the line-number gutters become a
/// click-drag range selector for inline comments, and (when [onExpandContext]
/// is supplied) the gaps between hunks render as expandable "hidden lines"
/// bands. With all callbacks null — the tool-block case — the widget renders
/// the patch exactly as before with no gestures, bands, or affordances.
class DiffViewer extends StatefulWidget {
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

  /// Invoked when the user taps the add-comment affordance on a diff row, or
  /// clicks a single line in the line-number gutter. Receives the resolved
  /// ([line], [side]) for that row. When null (the default, e.g. in tool blocks)
  /// no affordance and no gutter gestures are shown.
  final void Function(int line, String side)? onAddComment;

  /// Invoked when the user drags across the line-number gutter to select a
  /// contiguous range of ≥2 lines on a single side, then releases. Receives the
  /// inclusive ([startLine], [endLine]) range and the fixed [side]. A drag that
  /// resolves to a single line falls back to [onAddComment]. When null, gutter
  /// dragging still degrades to single-line tap behaviour via [onAddComment].
  final void Function(int startLine, int endLine, String side)?
  onAddRangeComment;

  /// Called when a gutter range-drag begins, so the parent can disable its own
  /// vertical scroll for the duration of the drag (the raw [Listener] pointer
  /// stream does not compete in the gesture arena, so this handshake is how the
  /// surrounding scroll view yields). Paired with [onGutterDragEnd].
  final VoidCallback? onGutterDragStart;

  /// Called when a gutter range-drag ends (release or cancel), so the parent
  /// can re-enable its scroll. Always fires exactly once per [onGutterDragStart].
  final VoidCallback? onGutterDragEnd;

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

  /// Fetches the requested inclusive NEW-side line range ([startLine]..
  /// [endLineInclusive], 1-based) of the file from [side] ("head"/"base") so the
  /// viewer can reveal context lines hidden between the patch hunks. When null
  /// (e.g. in tool blocks) no gap bands or expand affordances are rendered.
  final Future<List<String>> Function(
    String side,
    int startLine,
    int endLineInclusive,
  )?
  onExpandContext;

  const DiffViewer({
    super.key,
    required this.diff,
    this.mode = DiffViewMode.unified,
    this.threads = const [],
    this.draftComments = const [],
    this.onAddComment,
    this.onAddRangeComment,
    this.onGutterDragStart,
    this.onGutterDragEnd,
    this.threadBuilder,
    this.draftBuilder,
    this.composerAnchor,
    this.composerBuilder,
    this.onExpandContext,
  });

  @override
  State<DiffViewer> createState() => _DiffViewerState();
}

class _DiffViewerState extends State<DiffViewer> {
  // Diff add/del colours (copied verbatim from the original tool-block renderer).
  static const Color _delBg = Color(0x33F85149); // red tint
  static const Color _delText = Color(0xFFF85149); // bright red
  static const Color _addBg = Color(0x3356D364); // green tint
  static const Color _addText = Color(0xFF56D364); // bright green

  /// How many hidden lines a single "expand" click reveals at once.
  static const int _expandChunk = 20;

  static const TextStyle _monoStyle = TextStyle(
    fontSize: 11,
    fontFamily: 'monospace',
    height: 1.4,
  );

  // --- Gutter range-drag transient state -------------------------------------

  /// Index (into the current render rows) where a gutter drag started, or null
  /// when no drag is in progress.
  int? _dragAnchorRow;

  /// Index of the row currently under the pointer during a drag.
  int? _dragCurrentRow;

  /// Side ("LEFT"/"RIGHT") fixed by the first selected row; the selection is
  /// constrained to rows that carry a line number on this side.
  String? _dragSide;

  /// Render entries (rows + gap bands) captured at drag start so the per-row
  /// hit mapping stays stable for the duration of the gesture.
  List<Object> _dragRows = const [];

  /// True once a drag has actually moved off its anchor row, distinguishing a
  /// click (down+up, no move) from a real range drag.
  bool _dragMoved = false;

  // --- Context expansion state -----------------------------------------------

  /// NEW-side line numbers (1-based) that the user has expanded into view, keyed
  /// by nothing more than membership: a line is shown as a context row when its
  /// number is in this set. Reset whenever [widget.diff] changes (file switch).
  final Set<int> _expandedNewLines = {};

  /// Lines fetched for expansion, keyed by NEW-side line number. Holds the raw
  /// text so re-renders don't need to refetch.
  final Map<int, String> _expandedText = {};

  /// Gaps (by their [_Gap.startNew]) currently fetching, so the band shows a
  /// spinner and clicks are debounced.
  final Set<int> _fetchingGaps = {};

  /// Cached full new-side line count once the trailing gap has been fetched, so
  /// the trailing band can disappear when fully expanded.
  int? _newSideTotalLines;

  @override
  void didUpdateWidget(DiffViewer oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Switching files (new patch) must reset all expansion + drag state so we
    // never show one file's expanded lines over another's diff.
    if (oldWidget.diff != widget.diff) {
      _expandedNewLines.clear();
      _expandedText.clear();
      _fetchingGaps.clear();
      _newSideTotalLines = null;
      _dragAnchorRow = null;
      _dragCurrentRow = null;
      _dragSide = null;
      _dragRows = const [];
      _dragMoved = false;
    }
  }

  /// The inclusive [low, high] row-index span currently selected, or null.
  (int, int)? get _selectionSpan {
    final a = _dragAnchorRow;
    final b = _dragCurrentRow;
    if (a == null || b == null) return null;
    return a <= b ? (a, b) : (b, a);
  }

  /// True when row [index] is within the active drag selection AND lies on the
  /// fixed drag side (so the highlight only paints selectable rows).
  bool _rowSelected(int index, DiffLine row) {
    final span = _selectionSpan;
    if (span == null || _dragSide == null) return false;
    if (index < span.$1 || index > span.$2) return false;
    return _rowAnchor(row)?.side == _dragSide;
  }

  // ---------------------------------------------------------------------------
  // Parsing
  // ---------------------------------------------------------------------------

  /// Parse [diff] into typed rows, tracking line numbers from hunk headers. Also
  /// records, per hunk, its new-side start and count via [hunks] (each entry is
  /// `(newStart, newCount)`), so gaps between hunks can be computed.
  static List<DiffLine> _parse(String diff, List<(int, int)> hunks) {
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
          r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@',
        ).firstMatch(line);
        if (match != null) {
          oldLine = int.parse(match.group(1)!);
          newLine = int.parse(match.group(3)!);
          final newCount = int.parse(match.group(4) ?? '1');
          hunks.add((newLine, newCount));
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
  static bool _rowMatches(DiffLine row, int? line, String side) {
    if (line == null) return false;
    if (side == 'LEFT') return row.oldLineNo == line;
    return row.newLineNo == line; // RIGHT (default)
  }

  // ---------------------------------------------------------------------------
  // Render-row assembly (rows + interleaved gap bands)
  // ---------------------------------------------------------------------------

  /// An entry in the flattened render list: either a parsed [DiffLine] row or a
  /// [_Gap] band. Only [DiffLine]s carry gutter keys / drag participation.
  ///
  /// Returns the entries in display order, splicing any expanded context lines
  /// into the gaps and shrinking each gap accordingly.
  List<Object> _buildEntries(List<DiffLine> rows, List<(int, int)> hunks) {
    // No expansion support → return rows verbatim (tool-block path).
    if (widget.onExpandContext == null) return List<Object>.from(rows);

    final entries = <Object>[];

    // Map the first hunk's new-start and the running new-end so we can detect
    // the leading gap and the gaps between consecutive hunks.
    int? firstHunkNewStart;
    for (final h in hunks) {
      firstHunkNewStart = h.$1;
      break;
    }

    // Leading gap: new lines 1 .. firstHunkNewStart-1.
    if (firstHunkNewStart != null && firstHunkNewStart > 1) {
      _emitGap(entries, 1, firstHunkNewStart - 1, rows);
    }

    // Walk rows, and whenever we cross a hunk boundary insert the between-gap.
    var hunkIdx = -1;
    for (final row in rows) {
      if (row.type == DiffLineType.hunk) {
        hunkIdx++;
        // Gap between the previous hunk's new-end and this hunk's new-start.
        if (hunkIdx > 0) {
          final prev = hunks[hunkIdx - 1];
          final prevNewEnd = prev.$1 + prev.$2 - 1;
          final thisNewStart = hunks[hunkIdx].$1;
          if (thisNewStart > prevNewEnd + 1) {
            _emitGap(entries, prevNewEnd + 1, thisNewStart - 1, rows);
          }
        }
      }
      entries.add(row);
    }

    // Trailing gap: from the last hunk's new-end onward. The true extent is
    // unknown until the full file is fetched (which happens lazily when the
    // user expands some other gap). Only render a trailing band once that total
    // is KNOWN and there are genuinely more new-side lines past the last hunk;
    // before any fetch we show nothing, so deleted/short files (whose last hunk
    // reaches EOF, or which have no new side at all) never show a dead band
    // that reveals nothing on click.
    if (hunks.isNotEmpty) {
      final last = hunks.last;
      final lastNewEnd = last.$1 + last.$2 - 1;
      final total = _newSideTotalLines;
      if (total != null && total > lastNewEnd) {
        _emitGap(entries, lastNewEnd + 1, total, rows);
      }
    }

    return entries;
  }

  /// Append the expanded context rows for [startNew]..[endNew] that the user has
  /// already revealed, plus a residual [_Gap] band for whatever remains hidden.
  void _emitGap(
    List<Object> entries,
    int startNew,
    int? endNew,
    List<DiffLine> rows,
  ) {
    // Walk the range; runs of revealed lines become context rows, the rest are
    // collapsed into residual gap bands.
    var cursor = startNew;
    final hardEnd = endNew; // null = unbounded
    while (hardEnd == null || cursor <= hardEnd) {
      final revealed = _expandedNewLines.contains(cursor);
      if (revealed) {
        entries.add(
          DiffLine(
            type: DiffLineType.context,
            text: ' ${_expandedText[cursor] ?? ''}',
            // Old-side number for expanded lines is not derivable from the
            // patch alone, so we only carry the (exact) new-side number.
            newLineNo: cursor,
          ),
        );
        cursor++;
        if (hardEnd == null && !_expandedNewLines.contains(cursor)) {
          // Reached the end of a revealed run inside the unbounded trailing
          // region; emit a band for the remaining unknown tail and stop.
          entries.add(_Gap(startNew: cursor, endNew: null));
          return;
        }
        continue;
      }
      // Find the extent of the hidden run.
      var runEnd = cursor;
      if (hardEnd == null) {
        // Unbounded: a single band covers the rest.
        entries.add(_Gap(startNew: cursor, endNew: null));
        return;
      }
      while (runEnd <= hardEnd && !_expandedNewLines.contains(runEnd)) {
        runEnd++;
      }
      entries.add(_Gap(startNew: cursor, endNew: runEnd - 1));
      cursor = runEnd;
    }
  }

  // ---------------------------------------------------------------------------
  // Expansion
  // ---------------------------------------------------------------------------

  /// Fetch and reveal up to [_expandChunk] lines of [gap] from the [direction]
  /// end ('down' reveals from the top of the gap; 'up' from the bottom).
  Future<void> _expandGap(_Gap gap, {required bool fromBottom}) async {
    final fetcher = widget.onExpandContext;
    if (fetcher == null) return;
    if (_fetchingGaps.contains(gap.startNew)) return;

    final int from;
    final int to;
    if (gap.endNew == null) {
      // Trailing gap: only "down" makes sense; reveal a chunk from the top.
      from = gap.startNew;
      to = gap.startNew + _expandChunk - 1;
    } else if (fromBottom) {
      to = gap.endNew!;
      from = (gap.endNew! - _expandChunk + 1).clamp(gap.startNew, gap.endNew!);
    } else {
      from = gap.startNew;
      to = (gap.startNew + _expandChunk - 1).clamp(gap.startNew, gap.endNew!);
    }

    setState(() => _fetchingGaps.add(gap.startNew));
    try {
      final lines = await fetcher('head', from, to);
      if (!mounted) return;
      setState(() {
        for (var i = 0; i < lines.length; i++) {
          final lineNo = from + i;
          _expandedNewLines.add(lineNo);
          _expandedText[lineNo] = lines[i];
        }
        // A short return (fewer lines than requested) means we hit EOF: the
        // fetcher clamps to the file's real length. Record the new-side total
        // so the trailing band can appear correctly bounded (or disappear once
        // fully revealed). This is learned from ANY gap fetch, not just the
        // trailing one, so a between-hunk/leading expansion that reaches EOF
        // also populates it — which is what lets the trailing band surface for
        // files that genuinely have more lines past the last hunk.
        if (lines.length < (to - from + 1)) {
          _newSideTotalLines = from + lines.length - 1;
        }
        _fetchingGaps.remove(gap.startNew);
      });
    } catch (_) {
      if (mounted) setState(() => _fetchingGaps.remove(gap.startNew));
    }
  }

  // ---------------------------------------------------------------------------
  // Build
  // ---------------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    final hunks = <(int, int)>[];
    final rows = _parse(widget.diff, hunks);
    final entries = _buildEntries(rows, hunks);

    // Gutter width based on max line number across rows + any revealed lines.
    var maxLineNo = rows.fold<int>(0, (m, r) {
      final n = r.newLineNo ?? r.oldLineNo ?? 0;
      return n > m ? n : m;
    });
    for (final n in _expandedNewLines) {
      if (n > maxLineNo) maxLineNo = n;
    }
    final gutterChars = maxLineNo.toString().length;

    final anchoredThreads = <DiffThread>{};
    final anchoredDrafts = <DraftComment>{};
    var composerRendered = false;

    final colors = context.appColors;

    final children = <Widget>[];
    for (var i = 0; i < entries.length; i++) {
      final entry = entries[i];
      if (entry is _Gap) {
        children.add(_buildGapBand(context, entry, gutterChars));
        continue;
      }
      final row = entry as DiffLine;
      children.add(
        widget.mode == DiffViewMode.split
            ? _buildSplitRow(context, row, gutterChars, i, entries)
            : _buildUnifiedRow(context, row, gutterChars, i, entries),
      );

      // Inline existing threads anchored to this row.
      if (widget.threadBuilder != null) {
        for (final t in widget.threads) {
          if (anchoredThreads.contains(t)) continue;
          if (_rowMatches(row, t.line, t.side)) {
            anchoredThreads.add(t);
            children.add(widget.threadBuilder!(t));
          }
        }
      }

      // Inline queued draft comments anchored to this row.
      if (widget.draftBuilder != null) {
        for (final d in widget.draftComments) {
          if (anchoredDrafts.contains(d)) continue;
          if (_rowMatches(row, d.line, d.side)) {
            anchoredDrafts.add(d);
            children.add(widget.draftBuilder!(d));
          }
        }
      }

      // Inline composer anchored to this row (at most one).
      final anchor = widget.composerAnchor;
      if (widget.composerBuilder != null &&
          anchor != null &&
          !composerRendered &&
          _rowMatches(row, anchor.line, anchor.side)) {
        composerRendered = true;
        children.add(widget.composerBuilder!(anchor));
      }
    }

    // Threads whose anchor row was not found (outdated / wrong file slice).
    final outdated = widget.threadBuilder == null
        ? const <DiffThread>[]
        : widget.threads.where((t) => !anchoredThreads.contains(t)).toList();

    final diffBody = ClipRRect(
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
    );

    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 2, 12, 10),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // SelectionArea makes the code Text widgets click-drag selectable and
          // copyable. The line-number gutters opt out via
          // SelectionContainer.disabled so dragging on a gutter drives the
          // comment range selector instead of text selection. While a gutter
          // drag is active we drop the SelectionArea entirely so a drag that
          // began on a line number never starts a text selection as it moves
          // over the code.
          if (_dragAnchorRow != null)
            diffBody
          else
            SelectionArea(child: diffBody),
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
            for (final t in outdated) widget.threadBuilder!(t),
          ],
        ],
      ),
    );
  }

  // ---------------------------------------------------------------------------
  // Gap band rendering
  // ---------------------------------------------------------------------------

  Widget _buildGapBand(BuildContext context, _Gap gap, int gutterChars) {
    final colors = context.appColors;
    final fetching = _fetchingGaps.contains(gap.startNew);
    final count = gap.count;
    final label = count != null
        ? '⋯ $count hidden line${count == 1 ? '' : 's'}'
        : '⋯ hidden lines';

    Widget expandButton(IconData icon, String tooltip, VoidCallback onTap) {
      return SizedBox(
        width: 22,
        height: 18,
        child: IconButton(
          padding: EdgeInsets.zero,
          constraints: const BoxConstraints(),
          iconSize: 14,
          splashRadius: 12,
          tooltip: tooltip,
          icon: Icon(icon, color: colors.accentLight),
          onPressed: fetching ? null : onTap,
        ),
      );
    }

    // A small bounded gap (≤ chunk) expands fully in one click; larger gaps and
    // the unbounded trailing gap reveal a chunk from the chosen end.
    final small = count != null && count <= _expandChunk;

    return Container(
      width: double.infinity,
      decoration: BoxDecoration(
        color: colors.bgElevated,
        border: Border(
          top: BorderSide(color: colors.divider, width: 0.5),
          bottom: BorderSide(color: colors.divider, width: 0.5),
        ),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
      child: Row(
        children: [
          // Dashed-style leading motif so the collapse is unmistakable.
          SizedBox(
            width: (gutterChars * 7.0 * 2) + 8,
            child: Text(
              '┄┄',
              style: _monoStyle.copyWith(color: colors.textMuted),
            ),
          ),
          const SizedBox(width: kGapTight),
          if (fetching)
            const SizedBox(
              width: 12,
              height: 12,
              child: CircularProgressIndicator(strokeWidth: 1.5),
            )
          else if (small)
            expandButton(
              Icons.unfold_more,
              'Expand $count hidden line${count == 1 ? '' : 's'}',
              () => _expandGap(gap, fromBottom: false),
            )
          else ...[
            // Reveal from the top of the gap (lines just below the prior hunk).
            expandButton(
              Icons.expand_more,
              'Expand $_expandChunk lines down',
              () => _expandGap(gap, fromBottom: false),
            ),
            if (gap.endNew != null)
              // Reveal from the bottom of the gap (just above the next hunk).
              expandButton(
                Icons.expand_less,
                'Expand $_expandChunk lines up',
                () => _expandGap(gap, fromBottom: true),
              ),
          ],
          const SizedBox(width: kGapInline),
          Expanded(
            child: Text(
              label,
              style: _monoStyle.copyWith(
                color: colors.textMuted,
                fontStyle: FontStyle.italic,
              ),
            ),
          ),
        ],
      ),
    );
  }

  // ---------------------------------------------------------------------------
  // Gutter range-selection drag (raw pointer, see SelectionContainer.disabled)
  // ---------------------------------------------------------------------------
  //
  // Each line-number gutter cell is wrapped in [SelectionContainer.disabled] so
  // it is excluded from the surrounding [SelectionArea] (dragging on the gutter
  // does NOT select text). Inside that, a raw [Listener] drives a click-drag
  // range selection: a raw pointer stream does not enter the gesture arena, so
  // it never fights the parent scroll view — instead we ask the parent to
  // disable its scroll for the drag via [onGutterDragStart]/[onGutterDragEnd].
  //
  // pointer DOWN  → record anchor row + side, ask parent to disable scroll.
  // pointer MOVE  → hit-test global Y against per-row gutter keys; extend
  //                 selection (clamped to the anchor side); repaint highlight.
  // pointer UP    → re-enable scroll; ≥2 distinct lines → onAddRangeComment,
  //                 else (plain click / no move) → single-line onAddComment.

  void _onGutterPointerDown(int index, DiffLine row, List<Object> rows) {
    final anchor = _rowAnchor(row);
    if (anchor == null) return;
    setState(() {
      _dragRows = rows;
      _dragAnchorRow = index;
      _dragCurrentRow = index;
      _dragSide = anchor.side;
      _dragMoved = false;
    });
    widget.onGutterDragStart?.call();
  }

  void _onGutterPointerMove(Offset globalPosition) {
    if (_dragAnchorRow == null) return;
    final hit = _rowIndexAt(globalPosition);
    if (hit != null && hit != _dragCurrentRow) {
      setState(() {
        _dragCurrentRow = hit;
        if (hit != _dragAnchorRow) _dragMoved = true;
      });
    }
  }

  void _onGutterPointerUp() {
    if (_dragAnchorRow == null) {
      widget.onGutterDragEnd?.call();
      return;
    }
    final side = _dragSide;
    final span = _selectionSpan;
    final rows = _dragRows;
    final moved = _dragMoved;
    setState(() {
      _dragAnchorRow = null;
      _dragCurrentRow = null;
      _dragSide = null;
      _dragRows = const [];
      _dragMoved = false;
    });
    widget.onGutterDragEnd?.call();
    if (side == null || span == null) return;

    // Collect the concrete line numbers on the fixed side within the span,
    // skipping any gap bands that fall inside the selected span.
    final lines = <int>[];
    for (var i = span.$1; i <= span.$2 && i < rows.length; i++) {
      final entry = rows[i];
      if (entry is! DiffLine) continue;
      final a = _rowAnchor(entry);
      if (a != null && a.side == side) lines.add(a.line);
    }
    if (lines.isEmpty) return;
    final lo = lines.reduce((a, b) => a < b ? a : b);
    final hi = lines.reduce((a, b) => a > b ? a : b);

    if (moved &&
        lines.length >= 2 &&
        lo != hi &&
        widget.onAddRangeComment != null) {
      widget.onAddRangeComment!(lo, hi, side);
    } else {
      widget.onAddComment?.call(lo, side);
    }
  }

  /// Map a global pointer position to the index of the gutter row under it, by
  /// hit-testing each registered gutter's render box. Clamps to the nearest end
  /// when dragged past the diff edges so the selection still extends.
  int? _rowIndexAt(Offset globalPosition) {
    int? best;
    double? bestDist;
    for (final entry in _gutterKeys.entries) {
      final ctx = entry.value.currentContext;
      if (ctx == null) continue;
      final box = ctx.findRenderObject() as RenderBox?;
      if (box == null || !box.attached) continue;
      final topLeft = box.localToGlobal(Offset.zero);
      final rect = topLeft & box.size;
      if (globalPosition.dy >= rect.top && globalPosition.dy <= rect.bottom) {
        return entry.key;
      }
      final centerDy = rect.center.dy;
      final dist = (globalPosition.dy - centerDy).abs();
      if (bestDist == null || dist < bestDist) {
        bestDist = dist;
        best = entry.key;
      }
    }
    return best;
  }

  /// GlobalKeys for each row's gutter, keyed by render-entry index.
  final Map<int, GlobalKey> _gutterKeys = {};

  GlobalKey _gutterKey(int index) =>
      _gutterKeys.putIfAbsent(index, () => GlobalKey());

  /// Wrap a gutter region so it is excluded from text selection and drives the
  /// comment range-drag. Returns [child] unchanged when no comment callbacks are
  /// wired (e.g. tool blocks), so those keep rendering identically.
  Widget _wrapGutter(int index, DiffLine row, List<Object> rows, Widget child) {
    if (widget.onAddComment == null && widget.onAddRangeComment == null) {
      return child;
    }
    if (_rowAnchor(row) == null) return child; // hunk / expanded-only rows
    return SelectionContainer.disabled(
      child: Listener(
        key: _gutterKey(index),
        behavior: HitTestBehavior.opaque,
        onPointerDown: (_) => _onGutterPointerDown(index, row, rows),
        onPointerMove: (e) => _onGutterPointerMove(e.position),
        onPointerUp: (_) => _onGutterPointerUp(),
        onPointerCancel: (_) => _onGutterPointerUp(),
        child: MouseRegion(cursor: SystemMouseCursors.cell, child: child),
      ),
    );
  }

  // ---------------------------------------------------------------------------
  // Unified rendering (identical to the original tool-block renderer)
  // ---------------------------------------------------------------------------

  Widget _buildUnifiedRow(
    BuildContext context,
    DiffLine row,
    int gutterChars,
    int index,
    List<Object> rows,
  ) {
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

    final selected = _rowSelected(index, row);
    final gutter = _wrapGutter(
      index,
      row,
      rows,
      Row(
        mainAxisSize: MainAxisSize.min,
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
        ],
      ),
    );
    return Container(
      color: selected ? colors.accent.withAlpha(60) : bgColor,
      padding: const EdgeInsets.fromLTRB(8, 1, 8, 1),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          gutter,
          const SizedBox(width: kGapTight),
          Expanded(
            child: Text(row.text, style: _monoStyle.copyWith(color: textColor)),
          ),
        ],
      ),
    );
  }

  // ---------------------------------------------------------------------------
  // Split rendering (old | new side-by-side)
  // ---------------------------------------------------------------------------

  Widget _buildSplitRow(
    BuildContext context,
    DiffLine row,
    int gutterChars,
    int index,
    List<Object> rows,
  ) {
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
    final selected = _rowSelected(index, row);
    final selectableSide = anchor?.side;
    return IntrinsicHeight(
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Expanded(
            child: _buildSplitCell(
              context,
              gutter: row.oldLineNo,
              text: oldText,
              bgColor: selected && selectableSide == 'LEFT'
                  ? colors.accent.withAlpha(60)
                  : oldBg,
              textColor: oldText2,
              gutterChars: gutterChars,
              index: index,
              row: row,
              rows: rows,
              wrapGutter: selectableSide == 'LEFT',
            ),
          ),
          Container(width: 1, color: colors.divider),
          Expanded(
            child: _buildSplitCell(
              context,
              gutter: row.newLineNo,
              text: newText,
              bgColor: selected && selectableSide == 'RIGHT'
                  ? colors.accent.withAlpha(60)
                  : newBg,
              textColor: newText2,
              gutterChars: gutterChars,
              index: index,
              row: row,
              rows: rows,
              wrapGutter: selectableSide == 'RIGHT',
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildSplitCell(
    BuildContext context, {
    required int? gutter,
    required String text,
    required Color bgColor,
    required Color textColor,
    required int gutterChars,
    required int index,
    required DiffLine row,
    required List<Object> rows,
    required bool wrapGutter,
  }) {
    final colors = context.appColors;
    Widget gutterBox = SizedBox(
      width: (gutterChars * 7.0) + 4,
      child: Text(
        (gutter?.toString() ?? '').padLeft(gutterChars),
        style: _monoStyle.copyWith(color: colors.textMuted),
      ),
    );
    if (wrapGutter) {
      gutterBox = _wrapGutter(index, row, rows, gutterBox);
    } else {
      gutterBox = SelectionContainer.disabled(child: gutterBox);
    }
    return Container(
      color: bgColor,
      padding: const EdgeInsets.fromLTRB(8, 1, 8, 1),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          gutterBox,
          const SizedBox(width: kGapTight),
          Expanded(
            child: Text(text, style: _monoStyle.copyWith(color: textColor)),
          ),
        ],
      ),
    );
  }
}
