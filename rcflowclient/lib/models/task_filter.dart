import 'package:flutter/foundation.dart';

import 'linear_issue_info.dart';
import 'task_info.dart';

/// Sentinel stored in [TaskFilter.assigneeIds] meaning "assigned to the
/// Linear account the worker is connected as" (resolved per worker at
/// filter time).
const String kAssigneeMe = '__me__';

/// Sentinel stored in [TaskFilter.assigneeIds] matching issues with no
/// assignee.
const String kAssigneeUnassigned = '__unassigned__';

/// Immutable filter state for the Tasks tab.
///
/// Status and source apply to tasks only. Assignee, priority and label
/// selections are issue-level dimensions: they filter unlinked Linear issues
/// directly and tasks through their linked issues (see [filterTasks]).
@immutable
class TaskFilter {
  final String search;
  final Set<String> statuses;
  final Set<String> sources;
  final Set<String> assigneeIds;
  final Set<int> priorities;
  final Set<String> labels;

  const TaskFilter({
    this.search = '',
    this.statuses = const {},
    this.sources = const {},
    this.assigneeIds = const {},
    this.priorities = const {},
    this.labels = const {},
  });

  static const TaskFilter empty = TaskFilter();

  /// True when no dimension is active, including the free-text search.
  bool get isEmpty =>
      search.isEmpty &&
      statuses.isEmpty &&
      sources.isEmpty &&
      assigneeIds.isEmpty &&
      priorities.isEmpty &&
      labels.isEmpty;

  /// Number of selected values across the popover dimensions (search
  /// excluded — it is visible in the search field itself).
  int get activeCount =>
      statuses.length +
      sources.length +
      assigneeIds.length +
      priorities.length +
      labels.length;

  /// True when any issue-level dimension (assignee/priority/labels) is active.
  bool get hasIssueFilters =>
      assigneeIds.isNotEmpty || priorities.isNotEmpty || labels.isNotEmpty;

  TaskFilter copyWith({
    String? search,
    Set<String>? statuses,
    Set<String>? sources,
    Set<String>? assigneeIds,
    Set<int>? priorities,
    Set<String>? labels,
  }) {
    return TaskFilter(
      search: search ?? this.search,
      statuses: statuses ?? this.statuses,
      sources: sources ?? this.sources,
      assigneeIds: assigneeIds ?? this.assigneeIds,
      priorities: priorities ?? this.priorities,
      labels: labels ?? this.labels,
    );
  }

  TaskFilter toggleStatus(String status) =>
      copyWith(statuses: _toggled(statuses, status));

  TaskFilter toggleSource(String source) =>
      copyWith(sources: _toggled(sources, source));

  TaskFilter toggleAssignee(String id) =>
      copyWith(assigneeIds: _toggled(assigneeIds, id));

  TaskFilter togglePriority(int priority) =>
      copyWith(priorities: _toggled(priorities, priority));

  TaskFilter toggleLabel(String label) =>
      copyWith(labels: _toggled(labels, label));

  static Set<T> _toggled<T>(Set<T> values, T value) {
    final next = Set<T>.of(values);
    if (!next.remove(value)) next.add(value);
    return next;
  }
}

/// Free-text match over title, identifier and assignee name.
/// Returns true for an empty [query].
bool issueMatchesQuery(LinearIssueInfo issue, String query) {
  if (query.isEmpty) return true;
  final q = query.toLowerCase();
  return issue.title.toLowerCase().contains(q) ||
      issue.identifier.toLowerCase().contains(q) ||
      (issue.assigneeName?.toLowerCase().contains(q) ?? false);
}

/// Whether [issue] passes the issue-level dimensions of [filter] (assignee,
/// priority, labels). Search/status/source are NOT applied here.
///
/// Semantics: AND across dimensions, OR within a dimension's values.
/// [kAssigneeMe] matches iff the issue's assignee equals
/// `viewerIdByWorker[issue.workerId]` (a missing entry means Me matches
/// nothing for that worker's issues). [kAssigneeUnassigned] matches issues
/// with a null assignee. The labels dimension passes when the issue carries
/// any selected label.
bool issueMatchesFilter(
  LinearIssueInfo issue,
  TaskFilter filter, {
  Map<String, String> viewerIdByWorker = const {},
}) {
  if (filter.assigneeIds.isNotEmpty) {
    var matched = false;
    for (final id in filter.assigneeIds) {
      if (id == kAssigneeUnassigned) {
        matched = issue.assigneeId == null;
      } else if (id == kAssigneeMe) {
        final viewerId = viewerIdByWorker[issue.workerId];
        matched = viewerId != null && issue.assigneeId == viewerId;
      } else {
        matched = issue.assigneeId == id;
      }
      if (matched) break;
    }
    if (!matched) return false;
  }
  if (filter.priorities.isNotEmpty &&
      !filter.priorities.contains(issue.priority)) {
    return false;
  }
  if (filter.labels.isNotEmpty &&
      !issue.labels.any(filter.labels.contains)) {
    return false;
  }
  return true;
}

/// Filters unlinked Linear issues by the free-text query plus the
/// issue-level dimensions of [filter].
List<LinearIssueInfo> filterLinearIssues(
  List<LinearIssueInfo> issues,
  TaskFilter filter, {
  Map<String, String> viewerIdByWorker = const {},
}) {
  return issues
      .where(
        (i) =>
            issueMatchesQuery(i, filter.search) &&
            issueMatchesFilter(i, filter, viewerIdByWorker: viewerIdByWorker),
      )
      .toList();
}

/// Filters the task list.
///
/// Replicates the pre-popover pipeline (done-hiding, then status, then
/// source, then free-text search) and inserts the issue-level step: when any
/// issue dimension is active, a task is kept iff at least one of its linked
/// issues (from [issuesForTask]) satisfies ALL active issue-level dimensions;
/// tasks with no linked issues are dropped.
List<TaskInfo> filterTasks({
  required List<TaskInfo> tasks,
  required TaskFilter filter,
  required bool showCompletedTasks,
  required List<LinearIssueInfo> Function(String taskId) issuesForTask,
  Map<String, String> viewerIdByWorker = const {},
}) {
  var filtered = tasks;

  // Hide completed tasks by default unless the setting is on or the user
  // explicitly filtered for 'done'.
  if (!showCompletedTasks && !filter.statuses.contains('done')) {
    filtered = filtered.where((t) => t.status != 'done').toList();
  }

  if (filter.statuses.isNotEmpty) {
    filtered =
        filtered.where((t) => filter.statuses.contains(t.status)).toList();
  }
  if (filter.sources.isNotEmpty) {
    filtered =
        filtered.where((t) => filter.sources.contains(t.source)).toList();
  }
  if (filter.hasIssueFilters) {
    filtered = filtered.where((t) {
      return issuesForTask(t.taskId).any(
        (i) => issueMatchesFilter(i, filter,
            viewerIdByWorker: viewerIdByWorker),
      );
    }).toList();
  }
  if (filter.search.isNotEmpty) {
    final query = filter.search.toLowerCase();
    filtered = filtered.where((t) {
      return t.title.toLowerCase().contains(query) ||
          (t.description?.toLowerCase().contains(query) ?? false) ||
          t.source.toLowerCase().contains(query) ||
          t.workerName.toLowerCase().contains(query);
    }).toList();
  }
  return filtered;
}

/// One selectable entry in the popover's Assignee section.
@immutable
class AssigneeOption {
  final String id;
  final String label;
  final bool enabled;

  const AssigneeOption({
    required this.id,
    required this.label,
    this.enabled = true,
  });
}

/// Option lists for the filter popover, derived from the cached issues.
@immutable
class TaskFilterOptions {
  final List<AssigneeOption> assignees;
  final List<String> labels;
  final bool hasIssues;

  const TaskFilterOptions({
    required this.assignees,
    required this.labels,
    required this.hasIssues,
  });
}

/// Derives the popover option lists from every cached issue across all
/// workers. [meKnown] is true when at least one worker's Linear viewer
/// identity is known; it controls whether the "Me" entry is enabled.
///
/// Assignees: Me first, Unassigned second, then distinct named assignees
/// (keyed by id, labelled by name) sorted case-insensitively. Labels:
/// distinct, sorted case-insensitively.
TaskFilterOptions buildTaskFilterOptions(
  List<LinearIssueInfo> allIssues, {
  required bool meKnown,
}) {
  final byId = <String, String>{};
  final labelSet = <String>{};
  for (final issue in allIssues) {
    final id = issue.assigneeId;
    if (id != null) {
      byId[id] = issue.assigneeName ?? id;
    }
    labelSet.addAll(issue.labels);
  }
  final named = byId.entries
      .map((e) => AssigneeOption(id: e.key, label: e.value))
      .toList()
    ..sort(
      (a, b) => a.label.toLowerCase().compareTo(b.label.toLowerCase()),
    );
  final labels = labelSet.toList()
    ..sort((a, b) => a.toLowerCase().compareTo(b.toLowerCase()));
  return TaskFilterOptions(
    assignees: [
      AssigneeOption(id: kAssigneeMe, label: 'Me', enabled: meKnown),
      const AssigneeOption(id: kAssigneeUnassigned, label: 'Unassigned'),
      ...named,
    ],
    labels: labels,
    hasIssues: allIssues.isNotEmpty,
  );
}
