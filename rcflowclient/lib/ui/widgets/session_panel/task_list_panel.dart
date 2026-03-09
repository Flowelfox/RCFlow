import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/task_info.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import '../../dialogs/task_create_dialog.dart';
import 'task_tile.dart';

/// Task list panel for the sidebar Tasks tab.
///
/// Shows all tasks grouped by status (in_progress, todo, review, done).
/// Includes a search bar and status filter chips.
class TaskListPanel extends StatefulWidget {
  final VoidCallback? onTaskSelected;

  const TaskListPanel({super.key, this.onTaskSelected});

  @override
  State<TaskListPanel> createState() => _TaskListPanelState();
}

class _TaskListPanelState extends State<TaskListPanel> {
  final Set<String> _collapsedGroups = {'done'};
  final TextEditingController _searchController = TextEditingController();
  String _searchQuery = '';
  final Set<String> _activeStatusFilters = {};
  final Set<String> _activeSourceFilters = {};

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
  static const _sourceLabels = {
    'ai': 'AI',
    'user': 'User',
  };
  static const _sourceColors = {
    'ai': Color(0xFF8B5CF6),
    'user': Color(0xFF3B82F6),
  };

  @override
  void initState() {
    super.initState();
    final settings =
        Provider.of<AppState>(context, listen: false).settings;
    _searchQuery = settings.tasksFilterSearch;
    _searchController.text = _searchQuery;
    _activeStatusFilters.addAll(settings.tasksFilterStatus);
    _activeSourceFilters.addAll(settings.tasksFilterSource);
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  void _saveFilters() {
    final settings =
        Provider.of<AppState>(context, listen: false).settings;
    settings.tasksFilterSearch = _searchQuery;
    settings.tasksFilterStatus = _activeStatusFilters.toList();
    settings.tasksFilterSource = _activeSourceFilters.toList();
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
    });
    _saveFilters();
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<AppState>(
      builder: (context, state, _) {
        final tasks = state.tasks;

        if (tasks.isEmpty) {
          return Center(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.task_outlined,
                    color: context.appColors.textMuted, size: 40),
                const SizedBox(height: 12),
                Text('No tasks yet',
                    style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 16,
                        fontWeight: FontWeight.w600)),
                const SizedBox(height: 4),
                Text('Create a task or let AI generate them',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                        color: context.appColors.textMuted, fontSize: 13)),
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
                    padding: const EdgeInsets.symmetric(
                        horizontal: 16, vertical: 10),
                  ),
                ),
              ],
            ),
          );
        }

        final filtered = _filterTasks(tasks, state);

        // Group by status
        final grouped = <String, List<TaskInfo>>{};
        for (final status in _statusOrder) {
          grouped[status] = [];
        }
        for (final t in filtered) {
          grouped.putIfAbsent(t.status, () => []).add(t);
        }

        final sections = <Widget>[];
        for (final status in _statusOrder) {
          final group = grouped[status] ?? [];
          if (group.isEmpty) continue;
          final collapsed = _collapsedGroups.contains(status);
          sections.add(_buildStatusGroup(
            context, state, status, group, collapsed,
          ));
        }

        return Column(
          children: [
            _buildFilterBar(context),
            Expanded(
              child: filtered.isEmpty && _hasActiveFilters
                  ? _buildNoResults(context)
                  : ListView(
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      children: sections,
                    ),
            ),
          ],
        );
      },
    );
  }

  Widget _buildFilterBar(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 8, 10, 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            height: 30,
            child: Row(
              children: [
                Expanded(child: TextField(
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
                  child: Icon(Icons.search_rounded,
                      color: context.appColors.textMuted, size: 16),
                ),
                prefixIconConstraints:
                    const BoxConstraints(maxWidth: 28, maxHeight: 30),
                suffixIcon: _searchQuery.isNotEmpty
                    ? GestureDetector(
                        onTap: () {
                          _searchController.clear();
                          setState(() => _searchQuery = '');
                          _saveFilters();
                        },
                        child: Padding(
                          padding: const EdgeInsets.only(right: 6),
                          child: Icon(Icons.close_rounded,
                              color: context.appColors.textMuted, size: 14),
                        ),
                      )
                    : null,
                suffixIconConstraints:
                    const BoxConstraints(maxWidth: 24, maxHeight: 30),
                filled: true,
                fillColor: context.appColors.bgElevated,
                contentPadding:
                    const EdgeInsets.symmetric(horizontal: 8, vertical: 0),
                border: OutlineInputBorder(
                  borderSide: BorderSide.none,
                  borderRadius: BorderRadius.circular(8),
                ),
                enabledBorder: OutlineInputBorder(
                  borderSide: BorderSide.none,
                  borderRadius: BorderRadius.circular(8),
                ),
                focusedBorder: OutlineInputBorder(
                  borderSide:
                      BorderSide(color: context.appColors.accent, width: 1),
                  borderRadius: BorderRadius.circular(8),
                ),
              ),
            )),
                const SizedBox(width: 6),
                SizedBox(
                  width: 30,
                  height: 30,
                  child: IconButton(
                    padding: EdgeInsets.zero,
                    icon: Icon(Icons.add_rounded,
                        color: context.appColors.textSecondary, size: 18),
                    tooltip: 'New Task',
                    onPressed: () => showTaskCreateDialog(context),
                  ),
                ),
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
                            selected:
                                _activeStatusFilters.contains(status),
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
                      child: Icon(Icons.filter_alt_off_rounded,
                          color: context.appColors.textMuted, size: 16),
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
          Icon(Icons.search_off_rounded,
              color: context.appColors.textMuted, size: 32),
          const SizedBox(height: 8),
          Text('No matching tasks',
              style: TextStyle(
                  color: context.appColors.textSecondary, fontSize: 13)),
          const SizedBox(height: 4),
          GestureDetector(
            onTap: _clearFilters,
            child: Text('Clear filters',
                style: TextStyle(
                    color: context.appColors.accent, fontSize: 12)),
          ),
        ],
      ),
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
        if (!collapsed)
          ...tasks.map((t) => TaskTile(
                task: t,
                state: state,
                onTaskSelected: widget.onTaskSelected,
              )),
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
