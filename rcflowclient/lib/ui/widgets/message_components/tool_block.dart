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

  @override
  Widget build(BuildContext context) {
    final toolName = message.toolName ?? 'tool';
    final name = message.displayName ?? toolName;
    final output = message.content;
    final finished = message.finished;
    final expanded = message.expanded;
    final isError = message.isError;
    final summary = _toolSummary(toolName, message.toolInput);
    final hasExpandableContent = finished && output.isNotEmpty;

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
          ],
        ),
      ),
    );
  }
}
