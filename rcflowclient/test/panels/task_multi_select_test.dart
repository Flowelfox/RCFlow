/// Unit tests for [computeFlatVisibleList], the pure helper used by
/// [TaskListPanel] to build the ordered flat task list for Shift+click
/// range selection.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/task_info.dart';
import 'package:rcflowclient/ui/widgets/session_panel/task_list_panel.dart';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

final _now = DateTime(2025);

TaskInfo _task({
  required String id,
  String status = 'todo',
  String workerName = 'Alpha',
}) =>
    TaskInfo(
      taskId: id,
      title: 'Task $id',
      status: status,
      source: 'user',
      workerId: 'w1',
      workerName: workerName,
      createdAt: _now,
      updatedAt: _now,
    );

const _statusOrder = ['in_progress', 'todo', 'review', 'done'];

// ---------------------------------------------------------------------------
// computeFlatVisibleList — grouped by status
// ---------------------------------------------------------------------------

void main() {
  group('computeFlatVisibleList (grouped by status)', () {
    test('returns tasks in status order', () {
      final tasks = [
        _task(id: 'a', status: 'todo'),
        _task(id: 'b', status: 'in_progress'),
        _task(id: 'c', status: 'review'),
      ];
      final result = computeFlatVisibleList(
        filteredTasks: tasks,
        statusOrder: _statusOrder,
        collapsedGroups: {},
        groupByWorker: false,
        collapsedWorkerGroups: {},
      );
      // in_progress first, then todo, then review
      expect(result.map((t) => t.taskId), equals(['b', 'a', 'c']));
    });

    test('excludes tasks in collapsed status groups', () {
      final tasks = [
        _task(id: 'a', status: 'todo'),
        _task(id: 'b', status: 'in_progress'),
        _task(id: 'c', status: 'done'),
      ];
      final result = computeFlatVisibleList(
        filteredTasks: tasks,
        statusOrder: _statusOrder,
        collapsedGroups: {'done', 'todo'},
        groupByWorker: false,
        collapsedWorkerGroups: {},
      );
      expect(result.map((t) => t.taskId), equals(['b']));
    });

    test('returns empty list when all groups are collapsed', () {
      final tasks = [
        _task(id: 'a', status: 'todo'),
        _task(id: 'b', status: 'done'),
      ];
      final result = computeFlatVisibleList(
        filteredTasks: tasks,
        statusOrder: _statusOrder,
        collapsedGroups: {'todo', 'done'},
        groupByWorker: false,
        collapsedWorkerGroups: {},
      );
      expect(result, isEmpty);
    });

    test('returns empty list when input is empty', () {
      final result = computeFlatVisibleList(
        filteredTasks: [],
        statusOrder: _statusOrder,
        collapsedGroups: {},
        groupByWorker: false,
        collapsedWorkerGroups: {},
      );
      expect(result, isEmpty);
    });

    test('tasks with unknown status are excluded (not in statusOrder)', () {
      // The backend only produces the four known statuses; tasks with any other
      // status value are simply not iterated by the status-order loop and are
      // therefore excluded from the flat list.
      final tasks = [
        _task(id: 'a', status: 'todo'),
        _task(id: 'b', status: 'custom'), // unknown — not in statusOrder
      ];
      final result = computeFlatVisibleList(
        filteredTasks: tasks,
        statusOrder: _statusOrder,
        collapsedGroups: {},
        groupByWorker: false,
        collapsedWorkerGroups: {},
      );
      expect(result.map((t) => t.taskId), equals(['a']));
    });

    test('done group visible when not collapsed', () {
      final tasks = [
        _task(id: 'a', status: 'done'),
        _task(id: 'b', status: 'todo'),
      ];
      final result = computeFlatVisibleList(
        filteredTasks: tasks,
        statusOrder: _statusOrder,
        collapsedGroups: {}, // nothing collapsed
        groupByWorker: false,
        collapsedWorkerGroups: {},
      );
      expect(result.map((t) => t.taskId), containsAll(['a', 'b']));
    });
  });

  // ---------------------------------------------------------------------------
  // computeFlatVisibleList — grouped by worker
  // ---------------------------------------------------------------------------

  group('computeFlatVisibleList (grouped by worker)', () {
    test('returns tasks for all visible workers', () {
      final tasks = [
        _task(id: 'a', workerName: 'Alpha'),
        _task(id: 'b', workerName: 'Beta'),
        _task(id: 'c', workerName: 'Alpha'),
      ];
      final result = computeFlatVisibleList(
        filteredTasks: tasks,
        statusOrder: _statusOrder,
        collapsedGroups: {},
        groupByWorker: true,
        collapsedWorkerGroups: {},
      );
      expect(result.length, 3);
      expect(result.map((t) => t.taskId), containsAll(['a', 'b', 'c']));
    });

    test('excludes tasks for collapsed worker groups', () {
      final tasks = [
        _task(id: 'a', workerName: 'Alpha'),
        _task(id: 'b', workerName: 'Beta'),
        _task(id: 'c', workerName: 'Alpha'),
      ];
      final result = computeFlatVisibleList(
        filteredTasks: tasks,
        statusOrder: _statusOrder,
        collapsedGroups: {},
        groupByWorker: true,
        collapsedWorkerGroups: {'Alpha'},
      );
      expect(result.map((t) => t.taskId), equals(['b']));
    });

    test('returns empty when all worker groups are collapsed', () {
      final tasks = [
        _task(id: 'a', workerName: 'Alpha'),
        _task(id: 'b', workerName: 'Beta'),
      ];
      final result = computeFlatVisibleList(
        filteredTasks: tasks,
        statusOrder: _statusOrder,
        collapsedGroups: {},
        groupByWorker: true,
        collapsedWorkerGroups: {'Alpha', 'Beta'},
      );
      expect(result, isEmpty);
    });

    test('collapsed status groups are ignored when grouping by worker', () {
      final tasks = [
        _task(id: 'a', status: 'done', workerName: 'Alpha'),
      ];
      // Even though 'done' is in collapsedGroups, worker grouping ignores it.
      final result = computeFlatVisibleList(
        filteredTasks: tasks,
        statusOrder: _statusOrder,
        collapsedGroups: {'done'},
        groupByWorker: true,
        collapsedWorkerGroups: {},
      );
      expect(result.map((t) => t.taskId), equals(['a']));
    });
  });

  // ---------------------------------------------------------------------------
  // Range selection arithmetic (index-based, pure logic)
  // ---------------------------------------------------------------------------

  group('range selection indices', () {
    // These tests simulate the _handleTaskTap range logic by verifying that
    // given a flat list, the correct slice of IDs is produced.

    List<String> rangeSelect(
        List<TaskInfo> flatList, int anchor, int target) {
      final lo = anchor < target ? anchor : target;
      final hi = anchor < target ? target : anchor;
      return [
        for (var i = lo; i <= hi; i++)
          if (i < flatList.length) flatList[i].taskId,
      ];
    }

    test('forward range (anchor < target)', () {
      final flat = [
        _task(id: 'a'),
        _task(id: 'b'),
        _task(id: 'c'),
        _task(id: 'd'),
      ];
      expect(rangeSelect(flat, 0, 2), equals(['a', 'b', 'c']));
    });

    test('backward range (anchor > target)', () {
      final flat = [
        _task(id: 'a'),
        _task(id: 'b'),
        _task(id: 'c'),
        _task(id: 'd'),
      ];
      expect(rangeSelect(flat, 3, 1), equals(['b', 'c', 'd']));
    });

    test('single-item range (anchor == target)', () {
      final flat = [_task(id: 'a'), _task(id: 'b')];
      expect(rangeSelect(flat, 1, 1), equals(['b']));
    });

    test('target beyond list length is clamped', () {
      final flat = [_task(id: 'a'), _task(id: 'b')];
      // i < flatList.length guard prevents index out of range
      expect(rangeSelect(flat, 0, 5), equals(['a', 'b']));
    });
  });
}
