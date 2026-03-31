import 'package:flutter/material.dart';

import '../../../models/todo_item.dart';
import '../../../models/ws_messages.dart';
import '../../../theme.dart';

/// Compact inline indicator for `todo_update` messages.
/// Rendered in the same box style as ToolBlock (icon + progress bar, no spinner).
/// Full task details are visible in the side panel.
class TodoListBlock extends StatelessWidget {
  final DisplayMessage message;
  const TodoListBlock({super.key, required this.message});

  List<TodoItem> get _todos {
    final rawTodos = message.toolInput?['todos'] as List<dynamic>? ?? [];
    return rawTodos
        .whereType<Map<String, dynamic>>()
        .map((t) => TodoItem.fromJson(t))
        .toList();
  }

  @override
  Widget build(BuildContext context) {
    final todos = _todos;
    if (todos.isEmpty) return const SizedBox.shrink();

    final completed = todos
        .where((t) => t.status == TodoStatus.completed)
        .length;
    final total = todos.length;
    final allDone = completed == total;
    final progress = total > 0 ? completed / total : 0.0;

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Container(
        decoration: BoxDecoration(
          color: context.appColors.toolBg,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: context.appColors.divider),
        ),
        clipBehavior: Clip.antiAlias,
        child: Container(
          color: Colors.transparent,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          child: Row(
            children: [
              Icon(
                allDone
                    ? Icons.check_circle_outline_rounded
                    : Icons.sync_rounded,
                color: allDone
                    ? context.appColors.successText
                    : context.appColors.toolAccent,
                size: 14,
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Tasks updated',
                      style: TextStyle(
                        color: context.appColors.toolAccent,
                        fontSize: 13,
                        fontFamily: 'monospace',
                        fontWeight: FontWeight.w600,
                      ),
                      overflow: TextOverflow.ellipsis,
                    ),
                    Text(
                      '$completed/$total completed',
                      style: TextStyle(
                        color: context.appColors.toolOutputText,
                        fontSize: 11,
                        fontFamily: 'monospace',
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    const SizedBox(height: 4),
                    ClipRRect(
                      borderRadius: BorderRadius.circular(2),
                      child: LinearProgressIndicator(
                        value: progress,
                        backgroundColor: context.appColors.divider,
                        color: context.appColors.successText,
                        minHeight: 4,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
