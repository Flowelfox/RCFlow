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

  const DiffViewer({
    super.key,
    required this.diff,
    this.mode = DiffViewMode.unified,
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

  @override
  Widget build(BuildContext context) {
    final rows = _parse(diff);

    // Gutter width based on max line number
    final maxLineNo = rows.fold<int>(0, (m, r) {
      final n = r.newLineNo ?? r.oldLineNo ?? 0;
      return n > m ? n : m;
    });
    final gutterChars = maxLineNo.toString().length;

    final colors = context.appColors;
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 2, 12, 10),
      child: ClipRRect(
        borderRadius: BorderRadius.circular(kRadiusSmall),
        child: Container(
          width: double.infinity,
          decoration: BoxDecoration(
            border: Border.all(color: colors.divider),
            borderRadius: BorderRadius.circular(kRadiusSmall),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              for (final row in rows)
                mode == DiffViewMode.split
                    ? _buildSplitRow(context, row, gutterChars)
                    : _buildUnifiedRow(context, row, gutterChars),
            ],
          ),
        ),
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
        ],
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
