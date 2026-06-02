import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:provider/provider.dart';

import '../../models/linear_issue_info.dart';
import '../../models/split_tree.dart';
import '../../models/task_info.dart';
import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';
import '../utils/markdown_copy_menu.dart';
import '../utils/selectable_code_block_builder.dart';
import '../../theme/spacing.dart';

part 'task_pane_header.dart';
part 'task_detail_content.dart';
part 'task_tiles.dart';
part 'task_link_issue.dart';
part 'task_plan_banner.dart';

/// Full-pane task detail view.
///
/// Shows task metadata, status controls, and linked sessions.
class TaskPane extends StatefulWidget {
  final String paneId;
  final PaneState pane;

  const TaskPane({super.key, required this.paneId, required this.pane});

  @override
  State<TaskPane> createState() => _TaskPaneState();
}

class _TaskPaneState extends State<TaskPane> {
  final _contentKey = GlobalKey<_TaskDetailContentState>();

  @override
  Widget build(BuildContext context) {
    final appState = context.watch<AppState>();
    final taskId = widget.pane.taskId;
    if (taskId == null) {
      return _emptyState(context, appState);
    }
    final task = appState.getTask(taskId);
    if (task == null) {
      return _emptyState(context, appState);
    }

    final isActive = appState.activePaneId == widget.paneId;
    final multiPane = appState.paneCount > 1;

    return ChangeNotifierProvider<PaneState>.value(
      value: widget.pane,
      child: Column(
        children: [
          _TaskPaneHeader(
            paneId: widget.paneId,
            task: task,
            appState: appState,
            isActive: isActive,
            multiPane: multiPane,
            onEditPressed: () => _contentKey.currentState?.enterEditMode(),
          ),
          Expanded(
            child: _TaskDetailContent(
              key: _contentKey,
              paneId: widget.paneId,
              task: task,
              appState: appState,
            ),
          ),
        ],
      ),
    );
  }

  Widget _emptyState(BuildContext context, AppState appState) {
    return Column(
      children: [
        _TaskPaneHeader(
          paneId: widget.paneId,
          task: null,
          appState: appState,
          isActive: appState.activePaneId == widget.paneId,
          multiPane: appState.paneCount > 1,
          onEditPressed: null,
        ),
        Expanded(
          child: Center(
            child: Text(
              'Task not found',
              style: TextStyle(color: context.appColors.textMuted),
            ),
          ),
        ),
      ],
    );
  }
}
