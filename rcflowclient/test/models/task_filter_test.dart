/// Unit tests for the pure task-filter model and functions in
/// `lib/models/task_filter.dart`.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/linear_issue_info.dart';
import 'package:rcflowclient/models/task_filter.dart';
import 'package:rcflowclient/models/task_info.dart';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

LinearIssueInfo _issue({
  String id = 'id',
  String identifier = 'ENG-1',
  String title = 'Test issue',
  String? assigneeId,
  String? assigneeName,
  int priority = 0,
  List<String> labels = const [],
  String? taskId,
  String workerId = 'w1',
}) => LinearIssueInfo(
  id: id,
  linearId: 'lin-$id',
  identifier: identifier,
  title: title,
  priority: priority,
  stateName: 'Todo',
  stateType: 'unstarted',
  teamId: 'team1',
  url: 'https://linear.app/issue/$id',
  labels: List.of(labels),
  createdAt: DateTime(2025),
  updatedAt: DateTime(2025),
  syncedAt: DateTime(2025),
  assigneeId: assigneeId,
  assigneeName: assigneeName,
  taskId: taskId,
  workerId: workerId,
  workerName: 'Worker',
);

TaskInfo _task({
  String taskId = 't1',
  String title = 'Task',
  String? description,
  String status = 'todo',
  String source = 'user',
  String workerName = 'Worker',
}) => TaskInfo(
  taskId: taskId,
  title: title,
  description: description,
  status: status,
  source: source,
  workerId: 'w1',
  workerName: workerName,
  createdAt: DateTime(2025),
  updatedAt: DateTime(2025),
  sessions: const [],
);

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  group('TaskFilter', () {
    test('empty filter isEmpty and has zero activeCount', () {
      expect(TaskFilter.empty.isEmpty, isTrue);
      expect(TaskFilter.empty.activeCount, 0);
      expect(TaskFilter.empty.hasIssueFilters, isFalse);
    });

    test('search alone makes filter non-empty but not counted in badge', () {
      const f = TaskFilter(search: 'x');
      expect(f.isEmpty, isFalse);
      expect(f.activeCount, 0);
    });

    test('activeCount sums all popover dimensions', () {
      const f = TaskFilter(
        statuses: {'todo', 'done'},
        sources: {'ai'},
        assigneeIds: {kAssigneeMe},
        priorities: {1, 2},
        labels: {'bug'},
      );
      expect(f.activeCount, 7);
      expect(f.hasIssueFilters, isTrue);
    });

    test('toggles add and remove values immutably', () {
      const f = TaskFilter.empty;
      final on = f.toggleStatus('todo');
      expect(on.statuses, {'todo'});
      expect(f.statuses, isEmpty);
      expect(on.toggleStatus('todo').statuses, isEmpty);
      expect(f.toggleSource('ai').sources, {'ai'});
      expect(f.toggleAssignee(kAssigneeMe).assigneeIds, {kAssigneeMe});
      expect(f.togglePriority(1).priorities, {1});
      expect(f.toggleLabel('bug').labels, {'bug'});
    });

    test('copyWith replaces only given dimensions', () {
      const f = TaskFilter(statuses: {'todo'}, labels: {'bug'});
      final g = f.copyWith(search: 'q');
      expect(g.search, 'q');
      expect(g.statuses, {'todo'});
      expect(g.labels, {'bug'});
    });
  });

  group('issueMatchesFilter', () {
    test('direct assignee id match and non-match', () {
      final issue = _issue(assigneeId: 'user-1');
      const f = TaskFilter(assigneeIds: {'user-1'});
      expect(issueMatchesFilter(issue, f), isTrue);
      expect(
        issueMatchesFilter(_issue(assigneeId: 'user-2'), f),
        isFalse,
      );
    });

    test('Unassigned matches only null assignee', () {
      const f = TaskFilter(assigneeIds: {kAssigneeUnassigned});
      expect(issueMatchesFilter(_issue(), f), isTrue);
      expect(issueMatchesFilter(_issue(assigneeId: 'user-1'), f), isFalse);
    });

    test('Me resolves per worker', () {
      const f = TaskFilter(assigneeIds: {kAssigneeMe});
      final viewers = {'w1': 'user-1', 'w2': 'user-2'};
      expect(
        issueMatchesFilter(_issue(assigneeId: 'user-1', workerId: 'w1'), f,
            viewerIdByWorker: viewers),
        isTrue,
      );
      // Worker A's issue assigned to worker B's viewer does not match.
      expect(
        issueMatchesFilter(_issue(assigneeId: 'user-2', workerId: 'w1'), f,
            viewerIdByWorker: viewers),
        isFalse,
      );
    });

    test('Me with no known viewers matches nothing', () {
      const f = TaskFilter(assigneeIds: {kAssigneeMe});
      expect(
        issueMatchesFilter(_issue(assigneeId: 'user-1'), f),
        isFalse,
      );
    });

    test('assignee values OR within the dimension', () {
      const f = TaskFilter(assigneeIds: {kAssigneeUnassigned, 'user-1'});
      expect(issueMatchesFilter(_issue(), f), isTrue);
      expect(issueMatchesFilter(_issue(assigneeId: 'user-1'), f), isTrue);
      expect(issueMatchesFilter(_issue(assigneeId: 'user-9'), f), isFalse);
    });

    test('priority set membership including 0', () {
      const f = TaskFilter(priorities: {0, 2});
      expect(issueMatchesFilter(_issue(priority: 0), f), isTrue);
      expect(issueMatchesFilter(_issue(priority: 2), f), isTrue);
      expect(issueMatchesFilter(_issue(priority: 1), f), isFalse);
    });

    test('labels OR within; empty issue labels never match a selection', () {
      const f = TaskFilter(labels: {'bug', 'infra'});
      expect(issueMatchesFilter(_issue(labels: ['bug']), f), isTrue);
      expect(issueMatchesFilter(_issue(labels: ['ui']), f), isFalse);
      expect(issueMatchesFilter(_issue(), f), isFalse);
    });

    test('dimensions AND together', () {
      const f = TaskFilter(assigneeIds: {'user-1'}, priorities: {1});
      expect(
        issueMatchesFilter(_issue(assigneeId: 'user-1', priority: 1), f),
        isTrue,
      );
      expect(
        issueMatchesFilter(_issue(assigneeId: 'user-1', priority: 2), f),
        isFalse,
      );
    });
  });

  group('filterLinearIssues', () {
    test('empty filter returns all issues', () {
      final issues = [_issue(id: 'a'), _issue(id: 'b')];
      expect(filterLinearIssues(issues, TaskFilter.empty), issues);
    });

    test('composes query and issue dimensions', () {
      final issues = [
        _issue(id: 'a', title: 'Fix login', assigneeId: 'user-1'),
        _issue(id: 'b', title: 'Fix logout', assigneeId: 'user-2'),
        _issue(id: 'c', title: 'Other', assigneeId: 'user-1'),
      ];
      const f = TaskFilter(search: 'fix', assigneeIds: {'user-1'});
      final out = filterLinearIssues(issues, f);
      expect(out.map((i) => i.id), ['a']);
    });
  });

  group('filterTasks', () {
    List<LinearIssueInfo> noIssues(String taskId) => const [];

    test('done hidden by default, shown with setting or done filter', () {
      final tasks = [_task(taskId: 'a', status: 'done'), _task(taskId: 'b')];
      expect(
        filterTasks(
          tasks: tasks,
          filter: TaskFilter.empty,
          showCompletedTasks: false,
          issuesForTask: noIssues,
        ).map((t) => t.taskId),
        ['b'],
      );
      expect(
        filterTasks(
          tasks: tasks,
          filter: TaskFilter.empty,
          showCompletedTasks: true,
          issuesForTask: noIssues,
        ).length,
        2,
      );
      expect(
        filterTasks(
          tasks: tasks,
          filter: const TaskFilter(statuses: {'done'}),
          showCompletedTasks: false,
          issuesForTask: noIssues,
        ).map((t) => t.taskId),
        ['a'],
      );
    });

    test('status and source filters', () {
      final tasks = [
        _task(taskId: 'a', status: 'todo', source: 'ai'),
        _task(taskId: 'b', status: 'in_progress', source: 'user'),
      ];
      expect(
        filterTasks(
          tasks: tasks,
          filter: const TaskFilter(statuses: {'todo'}),
          showCompletedTasks: false,
          issuesForTask: noIssues,
        ).map((t) => t.taskId),
        ['a'],
      );
      expect(
        filterTasks(
          tasks: tasks,
          filter: const TaskFilter(sources: {'user'}),
          showCompletedTasks: false,
          issuesForTask: noIssues,
        ).map((t) => t.taskId),
        ['b'],
      );
    });

    test('search matches title, description, source and worker name', () {
      final tasks = [
        _task(taskId: 'a', title: 'Deploy backend'),
        _task(taskId: 'b', description: 'deploy soon'),
        _task(taskId: 'c', title: 'Other'),
      ];
      final out = filterTasks(
        tasks: tasks,
        filter: const TaskFilter(search: 'deploy'),
        showCompletedTasks: false,
        issuesForTask: noIssues,
      );
      expect(out.map((t) => t.taskId), ['a', 'b']);
    });

    test('task kept when one linked issue matches all issue dimensions', () {
      final linked = {
        't1': [_issue(id: 'a', assigneeId: 'user-1', priority: 1)],
      };
      final out = filterTasks(
        tasks: [_task(taskId: 't1')],
        filter: const TaskFilter(assigneeIds: {'user-1'}, priorities: {1}),
        showCompletedTasks: false,
        issuesForTask: (id) => linked[id] ?? const [],
      );
      expect(out.length, 1);
    });

    test('task hidden when dimensions only match across separate issues', () {
      // Issue A matches the assignee only, issue B the priority only — no
      // single linked issue satisfies both, so the task is hidden.
      final linked = {
        't1': [
          _issue(id: 'a', assigneeId: 'user-1', priority: 3),
          _issue(id: 'b', assigneeId: 'user-2', priority: 1),
        ],
      };
      final out = filterTasks(
        tasks: [_task(taskId: 't1')],
        filter: const TaskFilter(assigneeIds: {'user-1'}, priorities: {1}),
        showCompletedTasks: false,
        issuesForTask: (id) => linked[id] ?? const [],
      );
      expect(out, isEmpty);
    });

    test('task with no linked issues hidden iff issue filters active', () {
      final tasks = [_task(taskId: 't1')];
      expect(
        filterTasks(
          tasks: tasks,
          filter: const TaskFilter(assigneeIds: {kAssigneeUnassigned}),
          showCompletedTasks: false,
          issuesForTask: noIssues,
        ),
        isEmpty,
      );
      expect(
        filterTasks(
          tasks: tasks,
          filter: TaskFilter.empty,
          showCompletedTasks: false,
          issuesForTask: noIssues,
        ).length,
        1,
      );
    });
  });

  group('buildTaskFilterOptions', () {
    test('dedupes assignees, sorts case-insensitively, Me and Unassigned first',
        () {
      final issues = [
        _issue(id: 'a', assigneeId: 'u1', assigneeName: 'bob'),
        _issue(id: 'b', assigneeId: 'u2', assigneeName: 'Alice'),
        _issue(id: 'c', assigneeId: 'u1', assigneeName: 'bob'),
        _issue(id: 'd'),
      ];
      final opts = buildTaskFilterOptions(issues, meKnown: true);
      expect(opts.assignees.map((a) => a.id),
          [kAssigneeMe, kAssigneeUnassigned, 'u2', 'u1']);
      expect(opts.assignees.map((a) => a.label),
          ['Me', 'Unassigned', 'Alice', 'bob']);
      expect(opts.assignees.first.enabled, isTrue);
      expect(opts.hasIssues, isTrue);
    });

    test('Me disabled when no viewer known', () {
      final opts = buildTaskFilterOptions([_issue()], meKnown: false);
      expect(opts.assignees.first.id, kAssigneeMe);
      expect(opts.assignees.first.enabled, isFalse);
    });

    test('assignee without name falls back to id label', () {
      final opts = buildTaskFilterOptions(
        [_issue(assigneeId: 'u9')],
        meKnown: false,
      );
      expect(opts.assignees.last.label, 'u9');
    });

    test('labels distinct and sorted; hasIssues false when empty', () {
      final opts = buildTaskFilterOptions(
        [
          _issue(id: 'a', labels: ['ui', 'bug']),
          _issue(id: 'b', labels: ['bug', 'Infra']),
        ],
        meKnown: false,
      );
      expect(opts.labels, ['bug', 'Infra', 'ui']);
      expect(buildTaskFilterOptions(const [], meKnown: false).hasIssues,
          isFalse);
    });
  });
}
