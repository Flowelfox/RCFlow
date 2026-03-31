import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../../models/app_notification.dart';
import '../../../models/linear_issue_info.dart';
import '../../../models/task_info.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import '../../dialogs/task_create_dialog.dart';
import 'linear_issue_tile.dart';
import 'task_tile.dart';

/// Filters [issues] by a free-text [query], matching against title, identifier,
/// and assignee name. Returns all issues unmodified when [query] is empty.
///
/// Exposed at library level so that it can be exercised in unit tests without
/// requiring a widget environment.
List<LinearIssueInfo> filterLinearIssuesByQuery(
  List<LinearIssueInfo> issues,
  String query,
) {
  if (query.isEmpty) return issues;
  final q = query.toLowerCase();
  return issues.where((i) {
    return i.title.toLowerCase().contains(q) ||
        i.identifier.toLowerCase().contains(q) ||
        (i.assigneeName?.toLowerCase().contains(q) ?? false);
  }).toList();
}

/// Computes the ordered flat list of *visible* tasks given the current grouping
/// and collapse state. Tasks inside collapsed groups are excluded. The result
/// is used to resolve indices for Shift+click range selection.
///
/// Exposed at library level so it can be exercised in unit tests without
/// requiring a widget environment.
List<TaskInfo> computeFlatVisibleList({
  required List<TaskInfo> filteredTasks,
  required List<String> statusOrder,
  required Set<String> collapsedGroups,
  required bool groupByWorker,
  required Set<String> collapsedWorkerGroups,
}) {
  final result = <TaskInfo>[];
  if (groupByWorker) {
    final tasksByWorker = <String, List<TaskInfo>>{};
    for (final t in filteredTasks) {
      tasksByWorker.putIfAbsent(t.workerName, () => []).add(t);
    }
    for (final workerName in tasksByWorker.keys) {
      if (!collapsedWorkerGroups.contains(workerName)) {
        result.addAll(tasksByWorker[workerName]!);
      }
    }
  } else {
    final grouped = <String, List<TaskInfo>>{};
    for (final status in statusOrder) {
      grouped[status] = [];
    }
    for (final t in filteredTasks) {
      grouped.putIfAbsent(t.status, () => []).add(t);
    }
    for (final status in statusOrder) {
      final group = grouped[status] ?? [];
      if (group.isNotEmpty && !collapsedGroups.contains(status)) {
        result.addAll(group);
      }
    }
  }
  return result;
}

/// Task list panel for the sidebar Tasks tab.
///
/// Shows all tasks grouped by status (in_progress, todo, review, done), followed
/// by a collapsible "Unlinked Issues" section for Linear issues not yet linked
/// to any task. A sync button in the filter bar triggers a pull from Linear.
class TaskListPanel extends StatefulWidget {
  final VoidCallback? onTaskSelected;

  const TaskListPanel({super.key, this.onTaskSelected});

  @override
  State<TaskListPanel> createState() => _TaskListPanelState();
}

class _TaskListPanelState extends State<TaskListPanel> {
  final Set<String> _collapsedGroups = {'done'};
  final Set<String> _collapsedWorkerGroups = {};
  bool _unlinkedCollapsed = true;
  bool _groupByWorker = false;
  bool _syncing = false;
  final TextEditingController _searchController = TextEditingController();
  String _searchQuery = '';
  final Set<String> _activeStatusFilters = {};
  final Set<String> _activeSourceFilters = {};

  // ---- Multi-select state ----
  final Set<String> _selectedTaskIds = {};

  /// Index into [_currentFlatList] of the last plain/ctrl-clicked task.
  /// Used as the anchor for Shift+click range selection.
  int? _lastClickedVisibleIndex;

  /// Populated at the start of each [build] call; used by [_handleTaskTap]
  /// to resolve Shift+click ranges without passing the list through every
  /// widget constructor.
  List<TaskInfo> _currentFlatList = [];

  static const _statusOrder = ['in_progress', 'todo', 'review', 'done'];
  static const _statusLabels = {
    'in_progress': 'In Progress',
    'todo': 'To Do',
    'review': 'Review',
    'done': 'Done',
  };
  static const _statusColors = {
    'in_progress': Color(0xFF3B82F6),
    'todo': Color(0xFF6B7280),
    'review': Color(0xFFF59E0B),
    'done': Color(0xFF10B981),
  };
  static const _sourceOrder = ['ai', 'user'];
  static const _sourceLabels = {'ai': 'AI', 'user': 'User'};
  static const _sourceColors = {
    'ai': Color(0xFF8B5CF6),
    'user': Color(0xFF3B82F6),
  };

  @override
  void initState() {
    super.initState();
    final settings = Provider.of<AppState>(context, listen: false).settings;
    _searchQuery = settings.tasksFilterSearch;
    _searchController.text = _searchQuery;
    _activeStatusFilters.addAll(settings.tasksFilterStatus);
    _activeSourceFilters.addAll(settings.tasksFilterSource);
    _groupByWorker = settings.tasksGroupByWorker;
    final savedCollapsed = settings.tasksCollapsedGroups;
    if (savedCollapsed != null) {
      _collapsedGroups.clear();
      _collapsedGroups.addAll(savedCollapsed);
    }
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  void _saveFilters() {
    final settings = Provider.of<AppState>(context, listen: false).settings;
    settings.tasksFilterSearch = _searchQuery;
    settings.tasksFilterStatus = _activeStatusFilters.toList();
    settings.tasksFilterSource = _activeSourceFilters.toList();
  }

  void _saveCollapsedGroups() {
    final settings = Provider.of<AppState>(context, listen: false).settings;
    settings.tasksCollapsedGroups = _collapsedGroups.toList();
  }

  List<TaskInfo> _filterTasks(List<TaskInfo> tasks, AppState state) {
    var filtered = tasks;

    // Hide completed tasks by default unless the setting is on or
    // the user explicitly filtered for 'done'.
    if (!state.settings.showCompletedTasks &&
        !_activeStatusFilters.contains('done')) {
      filtered = filtered.where((t) => t.status != 'done').toList();
    }

    if (_activeStatusFilters.isNotEmpty) {
      filtered = filtered
          .where((t) => _activeStatusFilters.contains(t.status))
          .toList();
    }
    if (_activeSourceFilters.isNotEmpty) {
      filtered = filtered
          .where((t) => _activeSourceFilters.contains(t.source))
          .toList();
    }
    if (_searchQuery.isNotEmpty) {
      final query = _searchQuery.toLowerCase();
      filtered = filtered.where((t) {
        return t.title.toLowerCase().contains(query) ||
            (t.description?.toLowerCase().contains(query) ?? false) ||
            t.source.toLowerCase().contains(query) ||
            t.workerName.toLowerCase().contains(query);
      }).toList();
    }
    return filtered;
  }

  List<LinearIssueInfo> _filterUnlinkedIssues(List<LinearIssueInfo> issues) =>
      filterLinearIssuesByQuery(issues, _searchQuery);

  bool get _hasActiveFilters =>
      _searchQuery.isNotEmpty ||
      _activeStatusFilters.isNotEmpty ||
      _activeSourceFilters.isNotEmpty;

  void _clearFilters() {
    setState(() {
      _searchController.clear();
      _searchQuery = '';
      _activeStatusFilters.clear();
      _activeSourceFilters.clear();
      _selectedTaskIds.clear();
    });
    _saveFilters();
  }

  Future<void> _sync(BuildContext context, AppState state) async {
    final worker = state.getWorker(state.defaultWorkerId ?? '');
    if (worker == null) return;
    setState(() => _syncing = true);
    try {
      await worker.ws.syncLinearIssues();
      worker.ws.listLinearIssues();
    } catch (e) {
      if (context.mounted) {
        state.notificationService.show(
          level: NotificationLevel.error,
          title: 'Linear sync failed',
          body: _extractDetail(e),
          duration: const Duration(seconds: 8),
        );
      }
    } finally {
      if (mounted) setState(() => _syncing = false);
    }
  }

  // ---------------------------------------------------------------------------
  // Multi-select helpers
  // ---------------------------------------------------------------------------

  /// Builds a [TaskTile] wired with selection-aware tap and secondary-tap
  /// overrides. The parent owns all selection state.
  Widget _buildTaskTile(
    BuildContext context,
    TaskInfo task,
    AppState appState,
  ) {
    final idx = _currentFlatList.indexOf(task);
    return TaskTile(
      key: ValueKey(task.taskId),
      task: task,
      state: appState,
      onTaskSelected: widget.onTaskSelected,
      isSelected: _selectedTaskIds.contains(task.taskId),
      onTapOverride: () => _handleTaskTap(context, task, idx, appState),
      onSecondaryTapOverride: _selectedTaskIds.isNotEmpty
          ? (pos) {
              if (!_selectedTaskIds.contains(task.taskId)) {
                setState(() => _selectedTaskIds.add(task.taskId));
              }
              _showBulkContextMenu(context, pos, appState);
            }
          : null,
    );
  }

  /// Handles a tap on a task tile, respecting Shift/Ctrl/Meta modifiers.
  ///
  /// - **Shift+click**: range-selects from the last clicked index to [idx].
  /// - **Ctrl/Meta+click**: toggles [task] in the selection.
  /// - **Plain click** while selection is non-empty: toggles [task].
  /// - **Plain click** while selection is empty: opens the task in a pane
  ///   (default behaviour).
  void _handleTaskTap(
    BuildContext context,
    TaskInfo task,
    int idx,
    AppState appState,
  ) {
    final keys = HardwareKeyboard.instance.logicalKeysPressed;
    final shift =
        keys.contains(LogicalKeyboardKey.shiftLeft) ||
        keys.contains(LogicalKeyboardKey.shiftRight);
    final ctrl =
        keys.contains(LogicalKeyboardKey.controlLeft) ||
        keys.contains(LogicalKeyboardKey.controlRight) ||
        keys.contains(LogicalKeyboardKey.metaLeft) ||
        keys.contains(LogicalKeyboardKey.metaRight);

    if (shift && _lastClickedVisibleIndex != null) {
      final anchor = _lastClickedVisibleIndex!;
      final lo = anchor < idx ? anchor : idx;
      final hi = anchor < idx ? idx : anchor;
      setState(() {
        for (var i = lo; i <= hi; i++) {
          if (i < _currentFlatList.length) {
            _selectedTaskIds.add(_currentFlatList[i].taskId);
          }
        }
        _lastClickedVisibleIndex = idx;
      });
    } else if (ctrl) {
      setState(() {
        if (_selectedTaskIds.contains(task.taskId)) {
          _selectedTaskIds.remove(task.taskId);
        } else {
          _selectedTaskIds.add(task.taskId);
        }
        _lastClickedVisibleIndex = idx;
      });
    } else if (_selectedTaskIds.isNotEmpty) {
      setState(() {
        if (_selectedTaskIds.contains(task.taskId)) {
          _selectedTaskIds.remove(task.taskId);
        } else {
          _selectedTaskIds.add(task.taskId);
        }
        _lastClickedVisibleIndex = idx;
      });
    } else {
      // No selection, no modifiers — default: open task in pane.
      setState(() => _lastClickedVisibleIndex = idx);
      appState.openTaskInPane(task.taskId);
      widget.onTaskSelected?.call();
    }
  }

  /// The thin bar shown below the filter bar when tasks are selected.
  Widget _buildSelectionBar(BuildContext context, AppState state) {
    final count = _selectedTaskIds.length;
    return Container(
      color: context.appColors.accent.withAlpha(18),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 5),
      child: Row(
        children: [
          Icon(
            Icons.check_box_outlined,
            size: 14,
            color: context.appColors.accent,
          ),
          const SizedBox(width: 6),
          Text(
            '$count task${count == 1 ? '' : 's'} selected',
            style: TextStyle(
              color: context.appColors.accentLight,
              fontSize: 11,
              fontWeight: FontWeight.w500,
            ),
          ),
          const Spacer(),
          GestureDetector(
            onTap: () => setState(() => _selectedTaskIds.clear()),
            child: Tooltip(
              message: 'Clear selection (Esc)',
              child: Icon(
                Icons.close_rounded,
                size: 14,
                color: context.appColors.textMuted,
              ),
            ),
          ),
        ],
      ),
    );
  }

  /// Shows the bulk right-click context menu for the current selection.
  void _showBulkContextMenu(
    BuildContext context,
    Offset position,
    AppState state,
  ) {
    final count = _selectedTaskIds.length;
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
          enabled: false,
          height: 28,
          child: Text(
            '$count task${count == 1 ? '' : 's'} selected',
            style: TextStyle(
              color: context.appColors.textMuted,
              fontSize: 11,
              fontWeight: FontWeight.w500,
            ),
          ),
        ),
        const PopupMenuDivider(),
        PopupMenuItem(
          value: 'status_in_progress',
          child: Row(
            children: [
              Icon(
                Icons.play_circle_outline,
                color: const Color(0xFF3B82F6),
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Mark all \u2192 In Progress',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
        PopupMenuItem(
          value: 'status_todo',
          child: Row(
            children: [
              Icon(
                Icons.radio_button_unchecked,
                color: const Color(0xFF6B7280),
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Mark all \u2192 To Do',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
        PopupMenuItem(
          value: 'status_review',
          child: Row(
            children: [
              Icon(
                Icons.rate_review_outlined,
                color: const Color(0xFFF59E0B),
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Mark all \u2192 Review',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
        PopupMenuItem(
          value: 'status_done',
          child: Row(
            children: [
              Icon(
                Icons.check_circle_outline,
                color: const Color(0xFF10B981),
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Mark all \u2192 Done',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
        const PopupMenuDivider(),
        PopupMenuItem(
          value: 'delete',
          child: Row(
            children: [
              Icon(
                Icons.delete_outline,
                color: context.appColors.errorText,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Delete $count task${count == 1 ? '' : 's'}\u2026',
                style: TextStyle(color: context.appColors.errorText),
              ),
            ],
          ),
        ),
        const PopupMenuDivider(),
        PopupMenuItem(
          value: 'clear',
          child: Row(
            children: [
              Icon(
                Icons.close_rounded,
                color: context.appColors.textSecondary,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Clear selection',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
      ],
    ).then((value) {
      if (!context.mounted || value == null) return;
      switch (value) {
        case 'status_in_progress':
          _bulkUpdateStatus(context, state, 'in_progress');
        case 'status_todo':
          _bulkUpdateStatus(context, state, 'todo');
        case 'status_review':
          _bulkUpdateStatus(context, state, 'review');
        case 'status_done':
          _bulkUpdateStatus(context, state, 'done');
        case 'delete':
          _confirmBulkDelete(context, state);
        case 'clear':
          setState(() => _selectedTaskIds.clear());
      }
    });
  }

  Future<void> _bulkUpdateStatus(
    BuildContext context,
    AppState state,
    String newStatus,
  ) async {
    final ids = List<String>.from(_selectedTaskIds);
    setState(() => _selectedTaskIds.clear());

    int failures = 0;
    await Future.wait(
      ids.map((id) async {
        final task = state.getTask(id);
        if (task == null) return;
        final worker = state.getWorker(task.workerId);
        if (worker == null) return;
        try {
          await worker.ws.updateTask(id, status: newStatus);
        } catch (_) {
          failures++;
        }
      }),
    );

    if (failures > 0 && context.mounted) {
      state.addSystemMessage(
        'Failed to update $failures task${failures == 1 ? '' : 's'}',
        isError: true,
      );
    }
  }

  Future<void> _confirmBulkDelete(BuildContext context, AppState state) async {
    final count = _selectedTaskIds.length;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        title: Text(
          'Delete $count task${count == 1 ? '' : 's'}',
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 16),
        ),
        content: Text(
          'Delete $count task${count == 1 ? '' : 's'}? This cannot be undone.',
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
    if (confirmed != true || !context.mounted) return;
    await _bulkDelete(context, state);
  }

  Future<void> _bulkDelete(BuildContext context, AppState state) async {
    final ids = List<String>.from(_selectedTaskIds);
    setState(() => _selectedTaskIds.clear());

    int failures = 0;
    await Future.wait(
      ids.map((id) async {
        final task = state.getTask(id);
        if (task == null) return;
        final worker = state.getWorker(task.workerId);
        if (worker == null) return;
        try {
          await worker.ws.deleteTask(id);
        } catch (_) {
          failures++;
        }
      }),
    );

    if (failures > 0 && context.mounted) {
      state.addSystemMessage(
        'Failed to delete $failures task${failures == 1 ? '' : 's'}',
        isError: true,
      );
    }
  }

  /// Extracts the `detail` field from a JSON error body if present,
  /// otherwise returns the raw exception message.
  static String _extractDetail(Object e) {
    final raw = e.toString();
    final jsonStart = raw.indexOf('{');
    if (jsonStart >= 0) {
      try {
        final decoded =
            jsonDecode(raw.substring(jsonStart)) as Map<String, dynamic>;
        final detail = decoded['detail'] as String?;
        if (detail != null) return detail;
      } catch (_) {}
    }
    return raw;
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<AppState>(
      builder: (context, state, _) {
        final tasks = state.tasks;
        final allUnlinkedIssues = state.unlinkedLinearIssues;

        if (tasks.isEmpty && allUnlinkedIssues.isEmpty) {
          return _buildEmptyState(context, state);
        }

        final filtered = _filterTasks(tasks, state);
        final filteredUnlinked = _filterUnlinkedIssues(allUnlinkedIssues);

        // Compute and cache the flat visible list for range-selection.
        _currentFlatList = computeFlatVisibleList(
          filteredTasks: filtered,
          statusOrder: _statusOrder,
          collapsedGroups: _collapsedGroups,
          groupByWorker: _groupByWorker,
          collapsedWorkerGroups: _collapsedWorkerGroups,
        );

        // Build list items
        final listItems = <Widget>[];
        if (filtered.isEmpty && filteredUnlinked.isEmpty && _hasActiveFilters) {
          listItems.add(_buildNoResults(context));
        } else if (_groupByWorker) {
          _buildWorkerGroupedItems(
            context,
            state,
            filtered,
            filteredUnlinked,
            listItems,
          );
        } else {
          // Group by status (default)
          final grouped = <String, List<TaskInfo>>{};
          for (final status in _statusOrder) {
            grouped[status] = [];
          }
          for (final t in filtered) {
            grouped.putIfAbsent(t.status, () => []).add(t);
          }
          for (final status in _statusOrder) {
            final group = grouped[status] ?? [];
            if (group.isEmpty) continue;
            final collapsed = _collapsedGroups.contains(status);
            listItems.add(
              _buildStatusGroup(context, state, status, group, collapsed),
            );
          }
          if (filteredUnlinked.isNotEmpty) {
            listItems.add(
              _buildUnlinkedIssuesSection(context, state, filteredUnlinked),
            );
          }
        }

        return Focus(
          autofocus: false,
          onKeyEvent: (node, event) {
            if (event is KeyDownEvent &&
                event.logicalKey == LogicalKeyboardKey.escape &&
                _selectedTaskIds.isNotEmpty) {
              setState(() => _selectedTaskIds.clear());
              return KeyEventResult.handled;
            }
            return KeyEventResult.ignored;
          },
          child: Column(
            children: [
              _buildFilterBar(context, state),
              if (_selectedTaskIds.isNotEmpty)
                _buildSelectionBar(context, state),
              Expanded(
                child: ListView(
                  padding: const EdgeInsets.symmetric(vertical: 4),
                  children: listItems,
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  Widget _buildEmptyState(BuildContext context, AppState state) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.task_outlined,
            color: context.appColors.textMuted,
            size: 40,
          ),
          const SizedBox(height: 12),
          Text(
            'No tasks yet',
            style: TextStyle(
              color: context.appColors.textSecondary,
              fontSize: 16,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            'Create a task or let AI generate them',
            textAlign: TextAlign.center,
            style: TextStyle(color: context.appColors.textMuted, fontSize: 13),
          ),
          const SizedBox(height: 16),
          FilledButton.icon(
            onPressed: () => showTaskCreateDialog(context),
            icon: const Icon(Icons.add, size: 18),
            label: const Text('New Task'),
            style: FilledButton.styleFrom(
              backgroundColor: context.appColors.accent,
              foregroundColor: Colors.white,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(10),
              ),
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFilterBar(BuildContext context, AppState state) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 8, 10, 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            height: 30,
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _searchController,
                    onChanged: (v) {
                      setState(() => _searchQuery = v);
                      _saveFilters();
                    },
                    style: TextStyle(
                      color: context.appColors.textPrimary,
                      fontSize: 12,
                    ),
                    decoration: InputDecoration(
                      hintText: 'Search tasks...',
                      hintStyle: TextStyle(
                        color: context.appColors.textMuted,
                        fontSize: 12,
                      ),
                      prefixIcon: Padding(
                        padding: const EdgeInsets.only(left: 8, right: 4),
                        child: Icon(
                          Icons.search_rounded,
                          color: context.appColors.textMuted,
                          size: 16,
                        ),
                      ),
                      prefixIconConstraints: const BoxConstraints(
                        maxWidth: 28,
                        maxHeight: 30,
                      ),
                      suffixIcon: _searchQuery.isNotEmpty
                          ? GestureDetector(
                              onTap: () {
                                _searchController.clear();
                                setState(() => _searchQuery = '');
                                _saveFilters();
                              },
                              child: Padding(
                                padding: const EdgeInsets.only(right: 6),
                                child: Icon(
                                  Icons.close_rounded,
                                  color: context.appColors.textMuted,
                                  size: 14,
                                ),
                              ),
                            )
                          : null,
                      suffixIconConstraints: const BoxConstraints(
                        maxWidth: 24,
                        maxHeight: 30,
                      ),
                      filled: true,
                      fillColor: context.appColors.bgElevated,
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: 8,
                        vertical: 0,
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
                ),
                const SizedBox(width: 6),
                SizedBox(
                  width: 30,
                  height: 30,
                  child: IconButton(
                    padding: EdgeInsets.zero,
                    icon: Icon(
                      Icons.add_rounded,
                      color: context.appColors.textSecondary,
                      size: 18,
                    ),
                    tooltip: 'New Task',
                    onPressed: () => showTaskCreateDialog(context),
                  ),
                ),
                const SizedBox(width: 2),
                SizedBox(
                  width: 30,
                  height: 30,
                  child: IconButton(
                    padding: EdgeInsets.zero,
                    icon: Icon(
                      Icons.people_outlined,
                      color: _groupByWorker
                          ? context.appColors.accent
                          : context.appColors.textSecondary,
                      size: 18,
                    ),
                    tooltip: _groupByWorker
                        ? 'Grouping by worker (tap to group by status)'
                        : 'Group by worker',
                    onPressed: () {
                      setState(() => _groupByWorker = !_groupByWorker);
                      Provider.of<AppState>(
                        context,
                        listen: false,
                      ).settings.tasksGroupByWorker = _groupByWorker;
                    },
                  ),
                ),
                if (state.anyWorkerHasLinear) ...[
                  const SizedBox(width: 2),
                  SizedBox(
                    width: 30,
                    height: 30,
                    child: _syncing
                        ? const Center(
                            child: SizedBox(
                              width: 14,
                              height: 14,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            ),
                          )
                        : IconButton(
                            padding: EdgeInsets.zero,
                            icon: Icon(
                              Icons.sync,
                              color: context.appColors.textSecondary,
                              size: 18,
                            ),
                            tooltip: 'Sync from Linear',
                            onPressed: () => _sync(context, state),
                          ),
                  ),
                ],
              ],
            ),
          ),
          const SizedBox(height: 6),
          SizedBox(
            height: 24,
            child: Row(
              children: [
                Expanded(
                  child: ListView(
                    scrollDirection: Axis.horizontal,
                    children: [
                      for (final status in _statusOrder)
                        Padding(
                          padding: const EdgeInsets.only(right: 4),
                          child: _StatusFilterChip(
                            label: _statusLabels[status]!,
                            color: _statusColors[status]!,
                            selected: _activeStatusFilters.contains(status),
                            onTap: () {
                              setState(() {
                                if (_activeStatusFilters.contains(status)) {
                                  _activeStatusFilters.remove(status);
                                } else {
                                  _activeStatusFilters.add(status);
                                }
                              });
                              _saveFilters();
                            },
                          ),
                        ),
                    ],
                  ),
                ),
                if (_hasActiveFilters)
                  GestureDetector(
                    onTap: _clearFilters,
                    child: Padding(
                      padding: const EdgeInsets.only(left: 4),
                      child: Icon(
                        Icons.filter_alt_off_rounded,
                        color: context.appColors.textMuted,
                        size: 16,
                      ),
                    ),
                  ),
              ],
            ),
          ),
          const SizedBox(height: 4),
          SizedBox(
            height: 24,
            child: ListView(
              scrollDirection: Axis.horizontal,
              children: [
                for (final source in _sourceOrder)
                  Padding(
                    padding: const EdgeInsets.only(right: 4),
                    child: _StatusFilterChip(
                      label: _sourceLabels[source]!,
                      color: _sourceColors[source]!,
                      selected: _activeSourceFilters.contains(source),
                      onTap: () {
                        setState(() {
                          if (_activeSourceFilters.contains(source)) {
                            _activeSourceFilters.remove(source);
                          } else {
                            _activeSourceFilters.add(source);
                          }
                        });
                        _saveFilters();
                      },
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildNoResults(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.search_off_rounded,
            color: context.appColors.textMuted,
            size: 32,
          ),
          const SizedBox(height: 8),
          Text(
            'No matching tasks',
            style: TextStyle(
              color: context.appColors.textSecondary,
              fontSize: 13,
            ),
          ),
          const SizedBox(height: 4),
          GestureDetector(
            onTap: _clearFilters,
            child: Text(
              'Clear filters',
              style: TextStyle(color: context.appColors.accent, fontSize: 12),
            ),
          ),
        ],
      ),
    );
  }

  void _buildWorkerGroupedItems(
    BuildContext context,
    AppState state,
    List<TaskInfo> tasks,
    List<LinearIssueInfo> unlinkedIssues,
    List<Widget> out,
  ) {
    // Collect tasks per worker, preserving insertion order.
    final tasksByWorker = <String, List<TaskInfo>>{};
    for (final t in tasks) {
      tasksByWorker.putIfAbsent(t.workerName, () => []).add(t);
    }

    // Collect unlinked issues per worker.
    final issuesByWorker = <String, List<LinearIssueInfo>>{};
    for (final i in unlinkedIssues) {
      issuesByWorker.putIfAbsent(i.workerName, () => []).add(i);
    }

    // Union of all worker names, tasks first then issues-only workers.
    final workerNames = {...tasksByWorker.keys, ...issuesByWorker.keys};

    for (final workerName in workerNames) {
      final workerTasks = tasksByWorker[workerName] ?? [];
      final workerIssues = issuesByWorker[workerName] ?? [];
      out.add(
        _buildWorkerGroup(
          context,
          state,
          workerName,
          workerTasks,
          workerIssues,
        ),
      );
    }
  }

  Widget _buildWorkerGroup(
    BuildContext context,
    AppState state,
    String workerName,
    List<TaskInfo> tasks,
    List<LinearIssueInfo> unlinkedIssues,
  ) {
    final collapsed = _collapsedWorkerGroups.contains(workerName);
    final totalCount = tasks.length + unlinkedIssues.length;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          onTap: () => setState(() {
            if (collapsed) {
              _collapsedWorkerGroups.remove(workerName);
            } else {
              _collapsedWorkerGroups.add(workerName);
            }
          }),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            child: Row(
              children: [
                Icon(
                  collapsed
                      ? Icons.chevron_right_rounded
                      : Icons.expand_more_rounded,
                  color: context.appColors.textMuted,
                  size: 18,
                ),
                const SizedBox(width: 4),
                Icon(
                  Icons.person_outline_rounded,
                  color: context.appColors.textMuted,
                  size: 13,
                ),
                const SizedBox(width: 4),
                Text(
                  '$workerName ($totalCount)',
                  style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.5,
                  ),
                ),
              ],
            ),
          ),
        ),
        if (!collapsed) ...[
          ...tasks.map((t) => _buildTaskTile(context, t, state)),
          if (unlinkedIssues.isNotEmpty) ...[
            Padding(
              padding: const EdgeInsets.only(left: 32, top: 2, bottom: 2),
              child: Row(
                children: [
                  Icon(
                    Icons.link_off_rounded,
                    color: context.appColors.textMuted,
                    size: 11,
                  ),
                  const SizedBox(width: 4),
                  Text(
                    'Unlinked',
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 10,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                ],
              ),
            ),
            ...unlinkedIssues.map(
              (issue) => LinearIssueTile(
                issue: issue,
                state: state,
                onSelected: widget.onTaskSelected,
              ),
            ),
          ],
        ],
      ],
    );
  }

  Widget _buildStatusGroup(
    BuildContext context,
    AppState state,
    String status,
    List<TaskInfo> tasks,
    bool collapsed,
  ) {
    final label = _statusLabels[status] ?? status;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          onTap: () {
            setState(() {
              if (collapsed) {
                _collapsedGroups.remove(status);
              } else {
                _collapsedGroups.add(status);
              }
            });
            _saveCollapsedGroups();
          },
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            child: Row(
              children: [
                Icon(
                  collapsed
                      ? Icons.chevron_right_rounded
                      : Icons.expand_more_rounded,
                  color: context.appColors.textMuted,
                  size: 18,
                ),
                const SizedBox(width: 4),
                Text(
                  '$label (${tasks.length})',
                  style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.5,
                  ),
                ),
              ],
            ),
          ),
        ),
        if (!collapsed) ...tasks.map((t) => _buildTaskTile(context, t, state)),
      ],
    );
  }

  Widget _buildUnlinkedIssuesSection(
    BuildContext context,
    AppState state,
    List<LinearIssueInfo> issues,
  ) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const Divider(height: 1),
        InkWell(
          onTap: () => setState(() => _unlinkedCollapsed = !_unlinkedCollapsed),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            child: Row(
              children: [
                Icon(
                  _unlinkedCollapsed
                      ? Icons.chevron_right_rounded
                      : Icons.expand_more_rounded,
                  color: context.appColors.textMuted,
                  size: 18,
                ),
                const SizedBox(width: 4),
                Icon(
                  Icons.link_off_rounded,
                  color: context.appColors.textMuted,
                  size: 13,
                ),
                const SizedBox(width: 4),
                Text(
                  'Unlinked Issues (${issues.length})',
                  style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.5,
                  ),
                ),
              ],
            ),
          ),
        ),
        if (!_unlinkedCollapsed)
          ...issues.map(
            (issue) => LinearIssueTile(
              issue: issue,
              state: state,
              onSelected: widget.onTaskSelected,
            ),
          ),
      ],
    );
  }
}

class _StatusFilterChip extends StatelessWidget {
  final String label;
  final Color color;
  final bool selected;
  final VoidCallback onTap;

  const _StatusFilterChip({
    required this.label,
    required this.color,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: selected ? color.withAlpha(40) : Colors.transparent,
          borderRadius: BorderRadius.circular(6),
          border: Border.all(
            color: selected ? color.withAlpha(120) : context.appColors.divider,
            width: 1,
          ),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: selected ? color : context.appColors.textMuted,
            fontSize: 10,
            fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
          ),
        ),
      ),
    );
  }
}
