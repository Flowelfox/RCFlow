import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/todo_item.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';

/// Persistent dockable panel showing the current session's todo list.
///
/// Displayed on the right side of the pane area when the active session
/// has non-empty todos. Visibility is toggled via [PaneState.toggleTodoPanel].
class TodoPanel extends StatelessWidget {
  const TodoPanel({super.key});

  @override
  Widget build(BuildContext context) {
    final pane = context.watch<PaneState>();
    final todos = pane.todos;
    if (todos.isEmpty) return const SizedBox.shrink();

    final completed = todos
        .where((t) => t.status == TodoStatus.completed)
        .length;
    final total = todos.length;
    final progress = total > 0 ? completed / total : 0.0;

    return Container(
      color: context.appColors.bgSurface,
      child: Column(
        children: [
          // Header
          Container(
            height: 36,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            decoration: BoxDecoration(
              border: Border(
                bottom: BorderSide(color: context.appColors.divider),
              ),
            ),
            child: Row(
              children: [
                Icon(
                  Icons.checklist_rounded,
                  color: context.appColors.toolAccent,
                  size: 16,
                ),
                const SizedBox(width: 6),
                Text(
                  'Todo',
                  style: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 13,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  '$completed/$total',
                  style: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 11,
                  ),
                ),
                const Spacer(),
                SizedBox(
                  width: 24,
                  height: 24,
                  child: IconButton(
                    padding: EdgeInsets.zero,
                    iconSize: 14,
                    icon: Icon(
                      Icons.close_rounded,
                      color: context.appColors.textMuted,
                    ),
                    tooltip: 'Hide todo',
                    onPressed: pane.toggleTodoPanel,
                  ),
                ),
              ],
            ),
          ),
          // Progress bar
          Padding(
            padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
            child: ClipRRect(
              borderRadius: BorderRadius.circular(2),
              child: LinearProgressIndicator(
                value: progress,
                minHeight: 4,
                backgroundColor: context.appColors.divider,
                color: context.appColors.successText,
              ),
            ),
          ),
          // Todo list
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.symmetric(vertical: 4),
              itemCount: todos.length,
              itemBuilder: (context, index) =>
                  _TodoPanelItem(todo: todos[index]),
            ),
          ),
        ],
      ),
    );
  }
}

class _TodoPanelItem extends StatefulWidget {
  final TodoItem todo;
  const _TodoPanelItem({required this.todo});

  @override
  State<_TodoPanelItem> createState() => _TodoPanelItemState();
}

class _TodoPanelItemState extends State<_TodoPanelItem>
    with SingleTickerProviderStateMixin {
  late final AnimationController _spinController;

  @override
  void initState() {
    super.initState();
    _spinController = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1200),
    );
    if (widget.todo.status == TodoStatus.inProgress) {
      _spinController.repeat();
    }
  }

  @override
  void didUpdateWidget(_TodoPanelItem oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.todo.status == TodoStatus.inProgress) {
      if (!_spinController.isAnimating) _spinController.repeat();
    } else {
      _spinController.stop();
      _spinController.reset();
    }
  }

  @override
  void dispose() {
    _spinController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final isCompleted = widget.todo.status == TodoStatus.completed;
    final isActive = widget.todo.status == TodoStatus.inProgress;

    final icon = Icon(
      isCompleted
          ? Icons.check_box_rounded
          : isActive
          ? Icons.sync_rounded
          : Icons.check_box_outline_blank_rounded,
      size: 14,
      color: isCompleted
          ? context.appColors.successText
          : isActive
          ? context.appColors.accent
          : context.appColors.textMuted,
    );

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
      color: isActive
          ? context.appColors.accent.withAlpha(15)
          : Colors.transparent,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 2),
            child: isActive
                ? RotationTransition(turns: _spinController, child: icon)
                : icon,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              widget.todo.displayText,
              style: TextStyle(
                color: isCompleted
                    ? context.appColors.textMuted
                    : isActive
                    ? context.appColors.textPrimary
                    : context.appColors.textSecondary,
                fontSize: 12,
                height: 1.4,
                decoration: isCompleted ? TextDecoration.lineThrough : null,
                fontWeight: isActive ? FontWeight.w600 : FontWeight.w400,
              ),
            ),
          ),
        ],
      ),
    );
  }
}
