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
import '../utils/selectable_code_block_builder.dart';

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

class _TaskPaneHeader extends StatelessWidget {
  final String paneId;
  final TaskInfo? task;
  final AppState appState;
  final bool isActive;
  final bool multiPane;
  final VoidCallback? onEditPressed;

  const _TaskPaneHeader({
    required this.paneId,
    required this.task,
    required this.appState,
    required this.isActive,
    required this.multiPane,
    required this.onEditPressed,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 32,
      decoration: BoxDecoration(
        color: isActive
            ? context.appColors.accent.withAlpha(20)
            : context.appColors.bgSurface,
        border: Border(bottom: BorderSide(color: context.appColors.divider)),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: Row(
        children: [
          if (appState.panes[paneId]?.canGoBack ?? false)
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.arrow_back_rounded,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Back',
                onPressed: () => appState.goBack(paneId),
              ),
            ),
          if (isActive)
            Container(
              width: 6,
              height: 6,
              margin: const EdgeInsets.only(right: 6),
              decoration: BoxDecoration(
                color: context.appColors.accent,
                shape: BoxShape.circle,
              ),
            ),
          Icon(
            Icons.task_outlined,
            color: context.appColors.textMuted,
            size: 14,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              task?.title ?? 'Task',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 12,
                fontWeight: FontWeight.w500,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (task case final t?) ...[
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.edit_outlined,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Edit',
                onPressed: onEditPressed,
              ),
            ),
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.delete_outline,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Delete',
                onPressed: () => _confirmDeleteTask(context, t, appState),
              ),
            ),
          ],
          if (multiPane) ...[
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.vertical_split_outlined,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Split',
                onPressed: () =>
                    appState.splitPane(paneId, SplitAxis.horizontal),
              ),
            ),
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.close_rounded,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Close',
                onPressed: () => appState.closePane(paneId),
              ),
            ),
          ] else
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.close_rounded,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Close task view',
                onPressed: () => appState.closeTaskView(paneId),
              ),
            ),
        ],
      ),
    );
  }

  void _confirmDeleteTask(
    BuildContext context,
    TaskInfo task,
    AppState appState,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        title: Text(
          'Delete Task',
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 16),
        ),
        content: Text(
          'Delete "${task.title}"? This cannot be undone.',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 14,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text(
              'Cancel',
              style: TextStyle(color: context.appColors.textSecondary),
            ),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: context.appColors.errorText,
            ),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Delete', style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    final worker = appState.getWorker(task.workerId);
    if (worker == null) return;
    try {
      await worker.ws.deleteTask(task.taskId);
    } catch (e) {
      if (context.mounted) {
        appState.addSystemMessage('Failed to delete task: $e', isError: true);
      }
    }
  }
}

class _TaskDetailContent extends StatefulWidget {
  final String paneId;
  final TaskInfo task;
  final AppState appState;

  const _TaskDetailContent({
    super.key,
    required this.paneId,
    required this.task,
    required this.appState,
  });

  @override
  State<_TaskDetailContent> createState() => _TaskDetailContentState();
}

class _TaskDetailContentState extends State<_TaskDetailContent> {
  bool _editingTitle = false;
  bool _editingDescription = false;
  late TextEditingController _titleCtrl;
  late TextEditingController _descCtrl;
  final _titleFocus = FocusNode();
  final _descFocus = FocusNode();

  static const _statusLabels = {
    'todo': 'To Do',
    'in_progress': 'In Progress',
    'review': 'Review',
    'done': 'Done',
  };

  static const _statusColors = {
    'todo': Color(0xFF6B7280),
    'in_progress': Color(0xFF3B82F6),
    'review': Color(0xFFF59E0B),
    'done': Color(0xFF10B981),
  };

  @override
  void initState() {
    super.initState();
    _titleCtrl = TextEditingController(text: widget.task.title);
    _descCtrl = TextEditingController(text: widget.task.description ?? '');
  }

  @override
  void didUpdateWidget(covariant _TaskDetailContent oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Sync controllers if task data changed externally and we're not editing
    if (oldWidget.task.taskId != widget.task.taskId) {
      _exitEditMode();
      _titleCtrl.text = widget.task.title;
      _descCtrl.text = widget.task.description ?? '';
    } else {
      if (!_editingTitle) _titleCtrl.text = widget.task.title;
      if (!_editingDescription) {
        _descCtrl.text = widget.task.description ?? '';
      }
    }
  }

  @override
  void dispose() {
    _titleCtrl.dispose();
    _descCtrl.dispose();
    _titleFocus.dispose();
    _descFocus.dispose();
    super.dispose();
  }

  /// Called from the header edit button via GlobalKey.
  void enterEditMode() {
    setState(() {
      _editingTitle = true;
      _editingDescription = true;
      _titleCtrl.text = widget.task.title;
      _descCtrl.text = widget.task.description ?? '';
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _titleFocus.requestFocus();
    });
  }

  void _exitEditMode() {
    setState(() {
      _editingTitle = false;
      _editingDescription = false;
    });
  }

  void _cancelEditing() {
    _titleCtrl.text = widget.task.title;
    _descCtrl.text = widget.task.description ?? '';
    _exitEditMode();
  }

  Future<void> _saveChanges() async {
    final newTitle = _titleCtrl.text.trim();
    final newDesc = _descCtrl.text.trim();

    if (newTitle.isEmpty) return; // title is required

    final titleChanged = newTitle != widget.task.title;
    final descChanged = newDesc != (widget.task.description ?? '');

    if (!titleChanged && !descChanged) {
      _exitEditMode();
      return;
    }

    final worker = widget.appState.getWorker(widget.task.workerId);
    if (worker == null) return;

    try {
      await worker.ws.updateTask(
        widget.task.taskId,
        title: titleChanged ? newTitle : null,
        description: descChanged ? newDesc : null,
      );
    } catch (e) {
      if (mounted) {
        widget.appState.addSystemMessage(
          'Failed to update task: $e',
          isError: true,
        );
      }
    }
    if (mounted) _exitEditMode();
  }

  bool get _isEditing => _editingTitle || _editingDescription;

  @override
  Widget build(BuildContext context) {
    final task = widget.task;
    final appState = widget.appState;
    final statusColor =
        _statusColors[task.status] ?? context.appColors.textMuted;

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        // Title — inline editable on double-click
        _editingTitle
            ? _buildTitleEditor(context)
            : SelectableText(
                task.title,
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 20,
                  fontWeight: FontWeight.w700,
                ),
              ),
        const SizedBox(height: 12),

        // Status chip row
        Row(
          children: [
            _StatusChip(
              label: _statusLabels[task.status] ?? task.status,
              color: statusColor,
              current: true,
            ),
            const SizedBox(width: 8),
            ..._buildTransitionChips(context),
          ],
        ),
        const SizedBox(height: 8),

        // Metadata
        Row(
          children: [
            Icon(
              task.source == 'ai'
                  ? Icons.smart_toy_outlined
                  : Icons.person_outline,
              color: context.appColors.textMuted,
              size: 14,
            ),
            const SizedBox(width: 4),
            Text(
              'Created by ${task.source == 'ai' ? 'AI' : 'User'} \u00B7 ${_formatDate(task.createdAt)}',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 12,
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),

        // Description — inline editable on double-click
        _editingDescription
            ? _buildDescriptionEditor(context)
            : (task.description != null && task.description!.isNotEmpty)
            ? Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Description',
                    style: TextStyle(
                      color: context.appColors.textSecondary,
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                      letterSpacing: 0.5,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: context.appColors.bgElevated,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: MarkdownBody(
                      data: task.description!,
                      shrinkWrap: true,
                      selectable: true,
                      builders: {
                        'pre': SelectableCodeBlockBuilder(
                          textStyle: TextStyle(
                            color: context.appColors.textPrimary,
                            fontSize: 12.5,
                            fontFamily: 'monospace',
                          ),
                        ),
                      },
                      styleSheet: MarkdownStyleSheet(
                        p: TextStyle(
                          color: context.appColors.textPrimary,
                          fontSize: 13,
                          height: 1.5,
                        ),
                        code: TextStyle(
                          color: context.appColors.textPrimary,
                          backgroundColor: context.appColors.toolBg.withValues(
                            alpha: 0.6,
                          ),
                          fontSize: 12.5,
                          fontFamily: 'monospace',
                        ),
                        codeblockDecoration: BoxDecoration(
                          color: context.appColors.toolBg,
                          borderRadius: BorderRadius.circular(8),
                        ),
                        codeblockPadding: EdgeInsets.all(12),
                        a: TextStyle(color: context.appColors.accentLight),
                        listBullet: TextStyle(
                          color: context.appColors.textPrimary,
                          fontSize: 13,
                        ),
                        h1: TextStyle(
                          color: context.appColors.textPrimary,
                          fontSize: 20,
                          fontWeight: FontWeight.bold,
                        ),
                        h2: TextStyle(
                          color: context.appColors.textPrimary,
                          fontSize: 18,
                          fontWeight: FontWeight.bold,
                        ),
                        h3: TextStyle(
                          color: context.appColors.textPrimary,
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                        ),
                        blockquoteDecoration: BoxDecoration(
                          border: Border(
                            left: BorderSide(
                              color: context.appColors.accentDim,
                              width: 3,
                            ),
                          ),
                          color: context.appColors.toolBg.withValues(
                            alpha: 0.3,
                          ),
                        ),
                        blockquotePadding: EdgeInsets.only(
                          left: 12,
                          top: 4,
                          bottom: 4,
                        ),
                        tableBorder: TableBorder.all(
                          color: context.appColors.divider,
                        ),
                        tableHead: TextStyle(
                          color: context.appColors.textPrimary,
                          fontWeight: FontWeight.bold,
                        ),
                        tableBody: TextStyle(
                          color: context.appColors.textPrimary,
                        ),
                        horizontalRuleDecoration: BoxDecoration(
                          border: Border(
                            top: BorderSide(color: context.appColors.divider),
                          ),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                ],
              )
            : Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  GestureDetector(
                    onDoubleTap: () {
                      setState(() {
                        _editingDescription = true;
                        _descCtrl.text = '';
                      });
                      WidgetsBinding.instance.addPostFrameCallback((_) {
                        _descFocus.requestFocus();
                      });
                    },
                    child: Tooltip(
                      message: 'Double-click to add description',
                      waitDuration: const Duration(milliseconds: 600),
                      child: Container(
                        width: double.infinity,
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: context.appColors.bgElevated,
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(
                            color: context.appColors.divider,
                            style: BorderStyle.solid,
                          ),
                        ),
                        child: Text(
                          'Add description...',
                          style: TextStyle(
                            color: context.appColors.textMuted,
                            fontSize: 13,
                            fontStyle: FontStyle.italic,
                          ),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                ],
              ),

        // Save / Cancel row when editing
        if (_isEditing) ...[
          const SizedBox(height: 8),
          Row(
            children: [
              Text(
                'Ctrl+Enter to save \u00B7 Esc to cancel',
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 11,
                ),
              ),
              const Spacer(),
              TextButton(
                onPressed: _cancelEditing,
                child: Text(
                  'Cancel',
                  style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 13,
                  ),
                ),
              ),
              const SizedBox(width: 6),
              FilledButton(
                style: FilledButton.styleFrom(
                  backgroundColor: context.appColors.accent,
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 8,
                  ),
                ),
                onPressed: _saveChanges,
                child: const Text(
                  'Save',
                  style: TextStyle(color: Colors.white, fontSize: 13),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
        ],

        // Action buttons
        Row(
          children: [
            FilledButton.icon(
              onPressed: () =>
                  appState.startSessionFromTask(widget.paneId, task),
              icon: const Icon(Icons.play_arrow_rounded, size: 18),
              label: const Text('Start Session'),
              style: FilledButton.styleFrom(
                backgroundColor: context.appColors.accent,
                foregroundColor: Colors.white,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(10),
                ),
                padding: const EdgeInsets.symmetric(
                  horizontal: 14,
                  vertical: 10,
                ),
                textStyle: const TextStyle(fontSize: 13),
              ),
            ),
            const SizedBox(width: 8),
            if (task.status != 'done')
              OutlinedButton.icon(
                onPressed: () => _updateStatus(context, 'done'),
                icon: Icon(
                  Icons.check_circle_outline,
                  size: 18,
                  color: const Color(0xFF10B981),
                ),
                label: Text(
                  'Mark Complete',
                  style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 13,
                  ),
                ),
                style: OutlinedButton.styleFrom(
                  side: BorderSide(color: context.appColors.divider),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 14,
                    vertical: 10,
                  ),
                ),
              )
            else
              OutlinedButton.icon(
                onPressed: () => _updateStatus(context, 'todo'),
                icon: Icon(
                  Icons.replay_rounded,
                  size: 18,
                  color: context.appColors.textSecondary,
                ),
                label: Text(
                  'Reopen',
                  style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 13,
                  ),
                ),
                style: OutlinedButton.styleFrom(
                  side: BorderSide(color: context.appColors.divider),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 14,
                    vertical: 10,
                  ),
                ),
              ),
          ],
        ),
        const SizedBox(height: 16),

        // Linked sessions
        Text(
          'Linked Sessions (${task.sessions.length})',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 12,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.5,
          ),
        ),
        const SizedBox(height: 8),
        if (task.sessions.isEmpty)
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: context.appColors.bgElevated,
              borderRadius: BorderRadius.circular(8),
            ),
            child: Text(
              'No sessions linked yet',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 13,
              ),
            ),
          )
        else
          ...task.sessions.map(
            (ref) => _SessionRefTile(
              ref: ref,
              appState: appState,
              taskId: task.taskId,
              workerId: task.workerId,
            ),
          ),
        const SizedBox(height: 20),

        // Linked Linear issues
        _buildLinkedIssuesSection(context, appState, task),
      ],
    );
  }

  Widget _buildTitleEditor(BuildContext context) {
    return KeyboardListener(
      focusNode: FocusNode(),
      onKeyEvent: (event) {
        if (event is KeyDownEvent) {
          if (event.logicalKey == LogicalKeyboardKey.escape) {
            _cancelEditing();
          } else if (event.logicalKey == LogicalKeyboardKey.enter &&
              !HardwareKeyboard.instance.isShiftPressed) {
            if (HardwareKeyboard.instance.isControlPressed) {
              _saveChanges();
            } else {
              // Enter in title field → save
              _saveChanges();
            }
          }
        }
      },
      child: TextField(
        controller: _titleCtrl,
        focusNode: _titleFocus,
        maxLength: 300,
        style: TextStyle(
          color: context.appColors.textPrimary,
          fontSize: 20,
          fontWeight: FontWeight.w700,
        ),
        decoration: InputDecoration(
          isDense: true,
          counterText: '',
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 8,
            vertical: 6,
          ),
          fillColor: context.appColors.bgElevated,
          filled: true,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: BorderSide(color: context.appColors.accent),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: BorderSide(color: context.appColors.accent, width: 1.5),
          ),
        ),
      ),
    );
  }

  Widget _buildDescriptionEditor(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Description',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 12,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.5,
          ),
        ),
        const SizedBox(height: 6),
        KeyboardListener(
          focusNode: FocusNode(),
          onKeyEvent: (event) {
            if (event is KeyDownEvent) {
              if (event.logicalKey == LogicalKeyboardKey.escape) {
                _cancelEditing();
              } else if (event.logicalKey == LogicalKeyboardKey.enter &&
                  HardwareKeyboard.instance.isControlPressed) {
                _saveChanges();
              }
            }
          },
          child: TextField(
            controller: _descCtrl,
            focusNode: _descFocus,
            minLines: 3,
            maxLines: 8,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 13,
              height: 1.5,
            ),
            decoration: InputDecoration(
              hintText: 'Add description...',
              hintStyle: TextStyle(color: context.appColors.textMuted),
              contentPadding: const EdgeInsets.all(12),
              fillColor: context.appColors.bgElevated,
              filled: true,
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: BorderSide(color: context.appColors.accent),
              ),
              focusedBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: BorderSide(
                  color: context.appColors.accent,
                  width: 1.5,
                ),
              ),
            ),
          ),
        ),
        const SizedBox(height: 16),
      ],
    );
  }

  List<Widget> _buildTransitionChips(BuildContext context) {
    final transitions = <String, String>{
      'todo': 'To Do',
      'in_progress': 'In Progress',
      'review': 'Review',
      'done': 'Done',
    };
    // Only show valid transitions
    final valid = _validTransitions[widget.task.status] ?? {};
    return valid.map((status) {
      return Padding(
        padding: const EdgeInsets.only(right: 4),
        child: _StatusChip(
          label: transitions[status] ?? status,
          color: _statusColors[status] ?? context.appColors.textMuted,
          current: false,
          onTap: () => _updateStatus(context, status),
        ),
      );
    }).toList();
  }

  static const _validTransitions = {
    'todo': {'in_progress', 'done'},
    'in_progress': {'todo', 'review', 'done'},
    'review': {'in_progress', 'done'},
    'done': {'todo', 'in_progress'},
  };

  void _updateStatus(BuildContext context, String newStatus) async {
    final worker = widget.appState.getWorker(widget.task.workerId);
    if (worker == null) return;
    try {
      await worker.ws.updateTask(widget.task.taskId, status: newStatus);
    } catch (e) {
      if (context.mounted) {
        widget.appState.addSystemMessage(
          'Failed to update task: $e',
          isError: true,
        );
      }
    }
  }

  String _formatDate(DateTime dt) {
    final local = dt.toLocal();
    final months = [
      'Jan',
      'Feb',
      'Mar',
      'Apr',
      'May',
      'Jun',
      'Jul',
      'Aug',
      'Sep',
      'Oct',
      'Nov',
      'Dec',
    ];
    return '${months[local.month - 1]} ${local.day}, '
        '${local.hour.toString().padLeft(2, '0')}:'
        '${local.minute.toString().padLeft(2, '0')}';
  }

  Widget _buildLinkedIssuesSection(
    BuildContext context,
    AppState appState,
    TaskInfo task,
  ) {
    final linkedIssues = appState.linearIssuesForTask(task.taskId);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text(
              'Linked Issues (${linkedIssues.length})',
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 12,
                fontWeight: FontWeight.w600,
                letterSpacing: 0.5,
              ),
            ),
            const Spacer(),
            SizedBox(
              height: 24,
              child: TextButton.icon(
                onPressed: () => _showLinkIssuePicker(context, appState, task),
                icon: Icon(
                  Icons.add_link_rounded,
                  size: 14,
                  color: context.appColors.accent,
                ),
                label: Text(
                  'Link Issue',
                  style: TextStyle(
                    color: context.appColors.accent,
                    fontSize: 11,
                  ),
                ),
                style: TextButton.styleFrom(
                  padding: const EdgeInsets.symmetric(horizontal: 6),
                  minimumSize: Size.zero,
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        if (linkedIssues.isEmpty)
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: context.appColors.bgElevated,
              borderRadius: BorderRadius.circular(8),
            ),
            child: Text(
              'No Linear issues linked',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 13,
              ),
            ),
          )
        else
          ...linkedIssues.map(
            (issue) => _LinkedIssueTile(
              issue: issue,
              appState: appState,
              taskId: task.taskId,
            ),
          ),
      ],
    );
  }

  Future<void> _showLinkIssuePicker(
    BuildContext context,
    AppState appState,
    TaskInfo task,
  ) async {
    await showDialog<void>(
      context: context,
      builder: (ctx) => _LinkIssueDialog(appState: appState, task: task),
    );
  }
}

class _StatusChip extends StatelessWidget {
  final String label;
  final Color color;
  final bool current;
  final VoidCallback? onTap;

  const _StatusChip({
    required this.label,
    required this.color,
    required this.current,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: current ? color.withAlpha(30) : Colors.transparent,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: current ? color : color.withAlpha(60),
            width: current ? 1.5 : 1,
          ),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: color,
            fontSize: 11,
            fontWeight: current ? FontWeight.w600 : FontWeight.w500,
          ),
        ),
      ),
    );
  }
}

class _SessionRefTile extends StatelessWidget {
  final TaskSessionRef ref;
  final AppState appState;
  final String taskId;
  final String workerId;

  const _SessionRefTile({
    required this.ref,
    required this.appState,
    required this.taskId,
    required this.workerId,
  });

  @override
  Widget build(BuildContext context) {
    final title = ref.title ?? _shortId(ref.sessionId);
    final isTerminal = {
      'completed',
      'failed',
      'cancelled',
    }.contains(ref.status);

    return GestureDetector(
      onSecondaryTapUp: (details) =>
          _showContextMenu(context, details.globalPosition),
      child: Container(
        margin: const EdgeInsets.only(bottom: 4),
        decoration: BoxDecoration(
          color: context.appColors.bgElevated,
          borderRadius: BorderRadius.circular(8),
        ),
        child: ListTile(
          dense: true,
          visualDensity: const VisualDensity(vertical: -3),
          leading: Icon(
            isTerminal ? Icons.check_circle_outline : Icons.play_circle_outline,
            color: isTerminal
                ? context.appColors.textMuted
                : context.appColors.accentLight,
            size: 18,
          ),
          title: Text(
            title,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 12,
              fontWeight: FontWeight.w500,
            ),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
          subtitle: Text(
            ref.status,
            style: TextStyle(color: context.appColors.textMuted, fontSize: 10),
          ),
          onTap: () {
            appState.ensureChatPane().switchSession(ref.sessionId);
          },
        ),
      ),
    );
  }

  void _showContextMenu(BuildContext context, Offset position) {
    final overlay = Overlay.of(context).context.findRenderObject() as RenderBox;
    showMenu<String>(
      context: context,
      position: RelativeRect.fromRect(
        position & const Size(1, 1),
        Offset.zero & overlay.size,
      ),
      color: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      items: [
        PopupMenuItem(
          value: 'open',
          child: Row(
            children: [
              Icon(
                Icons.open_in_new_rounded,
                color: context.appColors.textSecondary,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Open',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
        PopupMenuItem(
          value: 'open_split',
          child: Row(
            children: [
              Icon(
                Icons.vertical_split_outlined,
                color: context.appColors.textSecondary,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Open in Split',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
        PopupMenuItem(
          value: 'detach',
          child: Row(
            children: [
              Icon(
                Icons.link_off_rounded,
                color: context.appColors.errorText,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Detach session',
                style: TextStyle(color: context.appColors.errorText),
              ),
            ],
          ),
        ),
      ],
    ).then((value) {
      if (!context.mounted) return;
      if (value == 'open') {
        appState.ensureChatPane().switchSession(ref.sessionId);
      } else if (value == 'open_split') {
        appState.splitPaneWithSession(
          appState.activePaneId,
          DropZone.right,
          ref.sessionId,
        );
      } else if (value == 'detach') {
        _detachSession(context);
      }
    });
  }

  void _detachSession(BuildContext context) async {
    final worker = appState.getWorker(workerId);
    if (worker == null) return;
    try {
      await worker.ws.detachSessionFromTask(taskId, ref.sessionId);
    } catch (e) {
      if (context.mounted) {
        appState.addSystemMessage(
          'Failed to detach session: $e',
          isError: true,
        );
      }
    }
  }

  static String _shortId(String id) {
    if (id.length > 8) return id.substring(0, 8);
    return id;
  }
}

// ---------------------------------------------------------------------------
// Linked issue tile (inside task detail)
// ---------------------------------------------------------------------------

class _LinkedIssueTile extends StatelessWidget {
  final LinearIssueInfo issue;
  final AppState appState;
  final String taskId;

  const _LinkedIssueTile({
    required this.issue,
    required this.appState,
    required this.taskId,
  });

  static const _priorityIcons = {
    0: Icons.remove,
    1: Icons.keyboard_double_arrow_up,
    2: Icons.keyboard_arrow_up,
    3: Icons.drag_handle,
    4: Icons.keyboard_arrow_down,
  };
  static const _priorityColors = {
    0: Color(0xFF6B7280),
    1: Color(0xFFEF4444),
    2: Color(0xFFF97316),
    3: Color(0xFFF59E0B),
    4: Color(0xFF6B7280),
  };
  static const _stateTypeColors = {
    'triage': Color(0xFF8B5CF6),
    'backlog': Color(0xFF6B7280),
    'unstarted': Color(0xFF6B7280),
    'started': Color(0xFF3B82F6),
    'completed': Color(0xFF10B981),
    'cancelled': Color(0xFF9CA3AF),
  };

  @override
  Widget build(BuildContext context) {
    final priorityIcon = _priorityIcons[issue.priority] ?? Icons.remove;
    final priorityColor =
        _priorityColors[issue.priority] ?? context.appColors.textMuted;
    final stateColor =
        _stateTypeColors[issue.stateType] ?? context.appColors.textMuted;

    return GestureDetector(
      onSecondaryTapUp: (details) =>
          _showContextMenu(context, details.globalPosition),
      child: Container(
        margin: const EdgeInsets.only(bottom: 4),
        decoration: BoxDecoration(
          color: context.appColors.bgElevated,
          borderRadius: BorderRadius.circular(8),
        ),
        child: ListTile(
          dense: true,
          visualDensity: const VisualDensity(vertical: -3),
          leading: Container(
            width: 28,
            height: 28,
            decoration: BoxDecoration(
              color: stateColor.withAlpha(30),
              borderRadius: BorderRadius.circular(6),
            ),
            child: Icon(priorityIcon, color: priorityColor, size: 14),
          ),
          title: Row(
            children: [
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                decoration: BoxDecoration(
                  color: context.appColors.bgBase,
                  borderRadius: BorderRadius.circular(4),
                  border: Border.all(
                    color: context.appColors.divider,
                    width: 0.5,
                  ),
                ),
                child: Text(
                  issue.identifier,
                  style: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 10,
                    fontWeight: FontWeight.w600,
                    fontFamily: 'monospace',
                  ),
                ),
              ),
              const SizedBox(width: 6),
              Expanded(
                child: Text(
                  issue.title,
                  style: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 12,
                    fontWeight: FontWeight.w500,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          subtitle: Text(
            issue.stateName,
            style: TextStyle(color: context.appColors.textMuted, fontSize: 10),
          ),
          onTap: () => appState.openLinearIssueInPane(issue.id),
        ),
      ),
    );
  }

  void _showContextMenu(BuildContext context, Offset position) {
    final overlay = Overlay.of(context).context.findRenderObject() as RenderBox;
    showMenu<String>(
      context: context,
      position: RelativeRect.fromRect(
        position & const Size(1, 1),
        Offset.zero & overlay.size,
      ),
      color: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      items: [
        PopupMenuItem(
          value: 'open',
          child: Row(
            children: [
              Icon(
                Icons.open_in_new_rounded,
                color: context.appColors.textSecondary,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Open issue',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
        PopupMenuItem(
          value: 'unlink',
          child: Row(
            children: [
              Icon(
                Icons.link_off_rounded,
                color: context.appColors.errorText,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Unlink from task',
                style: TextStyle(color: context.appColors.errorText),
              ),
            ],
          ),
        ),
      ],
    ).then((value) {
      if (!context.mounted) return;
      if (value == 'open') {
        appState.openLinearIssueInPane(issue.id);
      } else if (value == 'unlink') {
        _unlinkIssue(context);
      }
    });
  }

  void _unlinkIssue(BuildContext context) async {
    final worker = appState.getWorker(issue.workerId);
    if (worker == null) return;
    try {
      await worker.ws.unlinkLinearIssueFromTask(issue.id);
    } catch (e) {
      if (context.mounted) {
        appState.addSystemMessage('Failed to unlink issue: $e', isError: true);
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Link issue picker dialog
// ---------------------------------------------------------------------------

class _LinkIssueDialog extends StatefulWidget {
  final AppState appState;
  final TaskInfo task;

  const _LinkIssueDialog({required this.appState, required this.task});

  @override
  State<_LinkIssueDialog> createState() => _LinkIssueDialogState();
}

class _LinkIssueDialogState extends State<_LinkIssueDialog> {
  final TextEditingController _searchController = TextEditingController();
  String _query = '';
  bool _linking = false;

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  List<LinearIssueInfo> _filtered(List<LinearIssueInfo> issues) {
    if (_query.isEmpty) return issues;
    final q = _query.toLowerCase();
    return issues
        .where(
          (i) =>
              i.title.toLowerCase().contains(q) ||
              i.identifier.toLowerCase().contains(q),
        )
        .toList();
  }

  Future<void> _link(LinearIssueInfo issue) async {
    setState(() => _linking = true);
    final worker = widget.appState.getWorker(issue.workerId);
    if (worker == null) {
      setState(() => _linking = false);
      return;
    }
    try {
      await worker.ws.linkLinearIssueToTask(issue.id, widget.task.taskId);
      if (mounted) Navigator.of(context).pop();
    } catch (e) {
      if (mounted) {
        widget.appState.addSystemMessage(
          'Failed to link issue: $e',
          isError: true,
        );
        setState(() => _linking = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final unlinked = widget.appState.unlinkedLinearIssues;
    final filtered = _filtered(unlinked);

    return Dialog(
      backgroundColor: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 480, maxHeight: 500),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 20, 20, 12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Link a Linear Issue',
                    style: TextStyle(
                      color: context.appColors.textPrimary,
                      fontSize: 16,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _searchController,
                    onChanged: (v) => setState(() => _query = v),
                    autofocus: true,
                    style: TextStyle(
                      color: context.appColors.textPrimary,
                      fontSize: 13,
                    ),
                    decoration: InputDecoration(
                      hintText: 'Search issues...',
                      hintStyle: TextStyle(
                        color: context.appColors.textMuted,
                        fontSize: 13,
                      ),
                      prefixIcon: Icon(
                        Icons.search_rounded,
                        color: context.appColors.textMuted,
                        size: 16,
                      ),
                      filled: true,
                      fillColor: context.appColors.bgElevated,
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: 12,
                        vertical: 8,
                      ),
                      border: OutlineInputBorder(
                        borderSide: BorderSide.none,
                        borderRadius: BorderRadius.circular(8),
                      ),
                      enabledBorder: OutlineInputBorder(
                        borderSide: BorderSide.none,
                        borderRadius: BorderRadius.circular(8),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderSide: BorderSide(
                          color: context.appColors.accent,
                          width: 1,
                        ),
                        borderRadius: BorderRadius.circular(8),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const Divider(height: 1),
            if (unlinked.isEmpty)
              Padding(
                padding: const EdgeInsets.all(24),
                child: Center(
                  child: Text(
                    'No unlinked issues available.\nSync from Linear first.',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 13,
                    ),
                  ),
                ),
              )
            else if (filtered.isEmpty)
              Padding(
                padding: const EdgeInsets.all(24),
                child: Center(
                  child: Text(
                    'No issues match your search.',
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 13,
                    ),
                  ),
                ),
              )
            else
              Flexible(
                child: ListView.separated(
                  shrinkWrap: true,
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  itemCount: filtered.length,
                  separatorBuilder: (ctx, i) => const SizedBox(height: 0),
                  itemBuilder: (context, index) {
                    final issue = filtered[index];
                    return ListTile(
                      dense: true,
                      visualDensity: const VisualDensity(vertical: -2),
                      enabled: !_linking,
                      leading: Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 5,
                          vertical: 2,
                        ),
                        decoration: BoxDecoration(
                          color: context.appColors.bgElevated,
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(
                            color: context.appColors.divider,
                            width: 0.5,
                          ),
                        ),
                        child: Text(
                          issue.identifier,
                          style: TextStyle(
                            color: context.appColors.textMuted,
                            fontSize: 10,
                            fontWeight: FontWeight.w600,
                            fontFamily: 'monospace',
                          ),
                        ),
                      ),
                      title: Text(
                        issue.title,
                        style: TextStyle(
                          color: context.appColors.textPrimary,
                          fontSize: 13,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      subtitle: Text(
                        issue.stateName,
                        style: TextStyle(
                          color: context.appColors.textMuted,
                          fontSize: 11,
                        ),
                      ),
                      onTap: () => _link(issue),
                    );
                  },
                ),
              ),
            const Divider(height: 1),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  TextButton(
                    onPressed: _linking
                        ? null
                        : () => Navigator.of(context).pop(),
                    child: Text(
                      'Cancel',
                      style: TextStyle(color: context.appColors.textSecondary),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
