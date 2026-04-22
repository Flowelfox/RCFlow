import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/ws_messages.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';

class ToolBlock extends StatelessWidget {
  final DisplayMessage message;
  const ToolBlock({super.key, required this.message});

  /// Extract a short summary from [toolInput] based on the tool name.
  static String? _toolSummary(String name, Map<String, dynamic>? input) {
    if (input == null || input.isEmpty) return null;
    final lowerName = name.toLowerCase();

    if (lowerName == 'read' ||
        lowerName == 'write' ||
        lowerName == 'edit' ||
        lowerName == 'notebookedit') {
      final path = input['file_path'] ?? input['notebook_path'];
      if (path is String && path.isNotEmpty) return path;
    }

    if (lowerName == 'bash' || lowerName == 'shell_exec') {
      final cmd = input['command'];
      if (cmd is String && cmd.isNotEmpty) return cmd;
    }

    if (lowerName == 'grep' || lowerName == 'glob') {
      final pattern = input['pattern'];
      if (pattern is String && pattern.isNotEmpty) return pattern;
    }

    if (lowerName == 'task') {
      final desc = input['description'];
      if (desc is String && desc.isNotEmpty) return desc;
    }

    if (lowerName == 'webfetch') {
      final url = input['url'];
      if (url is String && url.isNotEmpty) return url;
    }

    for (final v in input.values) {
      if (v is String && v.isNotEmpty) return v;
    }
    return null;
  }

  /// Compute a compact diff stat string like "+12 -3" from a unified diff.
  static String? _diffStats(String? diff) {
    if (diff == null || diff.isEmpty) return null;
    int additions = 0;
    int deletions = 0;
    for (final line in diff.split('\n')) {
      if (line.startsWith('+++') || line.startsWith('---')) continue;
      if (line.startsWith('+')) {
        additions++;
      } else if (line.startsWith('-')) {
        deletions++;
      }
    }
    if (additions == 0 && deletions == 0) return null;
    final parts = <String>[];
    if (additions > 0) parts.add('+$additions');
    if (deletions > 0) parts.add('-$deletions');
    return parts.join(' ');
  }

  @override
  Widget build(BuildContext context) {
    final toolName = message.toolName ?? 'tool';
    final name = message.displayName ?? toolName;
    final output = message.content;
    final finished = message.finished;
    final expanded = message.expanded;
    final isError = message.isError;
    final summary = _toolSummary(toolName, message.toolInput);
    final diff = message.fileDiff;
    final hasDiff = diff != null && diff.isNotEmpty;
    final diffStats = _diffStats(diff);
    final hasExpandableContent =
        finished && (output.isNotEmpty || hasDiff);

    return Padding(
      padding: EdgeInsets.symmetric(vertical: 4),
      child: Container(
        decoration: BoxDecoration(
          color: context.appColors.toolBg,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: context.appColors.divider),
        ),
        clipBehavior: Clip.antiAlias,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            GestureDetector(
              onTap: hasExpandableContent
                  ? () {
                      message.expanded = !message.expanded;
                      context.read<PaneState>().refresh();
                    }
                  : null,
              child: Container(
                color: Colors.transparent,
                padding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                child: Row(
                  children: [
                    if (hasExpandableContent) ...[
                      Icon(
                        expanded
                            ? Icons.expand_less_rounded
                            : Icons.expand_more_rounded,
                        color: context.appColors.toolAccent,
                        size: 18,
                      ),
                      SizedBox(width: 8),
                    ],
                    Icon(
                      finished
                          ? (isError
                                ? Icons.error_outline_rounded
                                : Icons.check_circle_outline_rounded)
                          : Icons.sync_rounded,
                      color: finished
                          ? (isError
                                ? context.appColors.errorText
                                : context.appColors.successText)
                          : context.appColors.toolAccent,
                      size: 14,
                    ),
                    SizedBox(width: 6),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            name,
                            style: TextStyle(
                              color: context.appColors.toolAccent,
                              fontSize: 13,
                              fontFamily: 'monospace',
                              fontWeight: FontWeight.w600,
                            ),
                            overflow: TextOverflow.ellipsis,
                          ),
                          if (summary != null)
                            Text(
                              summary,
                              style: TextStyle(
                                color: context.appColors.toolOutputText,
                                fontSize: 11,
                                fontFamily: 'monospace',
                              ),
                              overflow: TextOverflow.clip,
                            ),
                        ],
                      ),
                    ),
                    if (diffStats != null) ...[
                      SizedBox(width: 8),
                      Text(
                        diffStats,
                        style: TextStyle(
                          color: context.appColors.textMuted,
                          fontSize: 11,
                          fontFamily: 'monospace',
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            ),
            if (expanded && output.isNotEmpty)
              Container(
                width: double.infinity,
                padding: EdgeInsets.fromLTRB(12, 0, 12, 10),
                child: Text(
                  output,
                  style: TextStyle(
                    color: isError
                        ? context.appColors.errorText
                        : context.appColors.toolOutputText,
                    fontSize: 11,
                    fontFamily: 'monospace',
                    height: 1.3,
                  ),
                ),
              ),
            if (expanded && hasDiff) _DiffView(diff: diff),
          ],
        ),
      ),
    );
  }
}

/// Renders a unified diff with line numbers and red/green coloring.
class _DiffView extends StatelessWidget {
  final String diff;
  const _DiffView({required this.diff});

  @override
  Widget build(BuildContext context) {
    final lines = diff.split('\n');
    // Parse lines, tracking line numbers from hunk headers.
    int oldLine = 0;
    int newLine = 0;

    final rows = <_DiffLine>[];
    for (final line in lines) {
      if (line.startsWith('---') || line.startsWith('+++')) {
        // File header — skip, info already in tool summary
        continue;
      }
      if (line.startsWith('@@')) {
        // Parse hunk header: @@ -oldStart,oldCount +newStart,newCount @@
        final match = RegExp(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@')
            .firstMatch(line);
        if (match != null) {
          oldLine = int.parse(match.group(1)!);
          newLine = int.parse(match.group(2)!);
        }
        rows.add(_DiffLine(
          type: _DiffLineType.hunk,
          text: line,
          oldLineNo: oldLine,
          newLineNo: newLine,
        ));
        continue;
      }
      if (line.startsWith('-')) {
        rows.add(_DiffLine(
          type: _DiffLineType.deletion,
          text: line,
          oldLineNo: oldLine,
        ));
        oldLine++;
      } else if (line.startsWith('+')) {
        rows.add(_DiffLine(
          type: _DiffLineType.addition,
          text: line,
          newLineNo: newLine,
        ));
        newLine++;
      } else {
        rows.add(_DiffLine(
          type: _DiffLineType.context,
          text: line,
          oldLineNo: oldLine,
          newLineNo: newLine,
        ));
        oldLine++;
        newLine++;
      }
    }

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
        borderRadius: BorderRadius.circular(6),
        child: Container(
          width: double.infinity,
          decoration: BoxDecoration(
            border: Border.all(color: colors.divider),
            borderRadius: BorderRadius.circular(6),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              for (final row in rows) _buildDiffRow(context, row, gutterChars),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildDiffRow(
      BuildContext context, _DiffLine row, int gutterChars) {
    final colors = context.appColors;

    Color bgColor;
    Color textColor;
    String oldGutter;
    String newGutter;

    switch (row.type) {
      case _DiffLineType.hunk:
        bgColor = colors.accentDim.withValues(alpha: 0.3);
        textColor = colors.accentLight;
        oldGutter =
            (row.oldLineNo?.toString() ?? '').padLeft(gutterChars);
        newGutter =
            (row.newLineNo?.toString() ?? '').padLeft(gutterChars);
      case _DiffLineType.deletion:
        bgColor = const Color(0x33F85149); // red tint
        textColor = const Color(0xFFF85149); // bright red
        oldGutter =
            (row.oldLineNo?.toString() ?? '').padLeft(gutterChars);
        newGutter = ''.padLeft(gutterChars);
      case _DiffLineType.addition:
        bgColor = const Color(0x3356D364); // green tint
        textColor = const Color(0xFF56D364); // bright green
        oldGutter = ''.padLeft(gutterChars);
        newGutter =
            (row.newLineNo?.toString() ?? '').padLeft(gutterChars);
      case _DiffLineType.context:
        bgColor = Colors.transparent;
        textColor = colors.toolOutputText;
        oldGutter =
            (row.oldLineNo?.toString() ?? '').padLeft(gutterChars);
        newGutter =
            (row.newLineNo?.toString() ?? '').padLeft(gutterChars);
    }

    const monoStyle = TextStyle(
      fontSize: 11,
      fontFamily: 'monospace',
      height: 1.4,
    );

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
              style: monoStyle.copyWith(color: colors.textMuted),
            ),
          ),
          SizedBox(
            width: (gutterChars * 7.0) + 4,
            child: Text(
              newGutter,
              style: monoStyle.copyWith(color: colors.textMuted),
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              row.text,
              style: monoStyle.copyWith(color: textColor),
            ),
          ),
        ],
      ),
    );
  }
}

enum _DiffLineType { hunk, deletion, addition, context }

class _DiffLine {
  final _DiffLineType type;
  final String text;
  final int? oldLineNo;
  final int? newLineNo;

  const _DiffLine({
    required this.type,
    required this.text,
    this.oldLineNo,
    this.newLineNo,
  });
}
