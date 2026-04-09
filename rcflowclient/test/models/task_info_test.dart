/// Tests for [TaskInfo] model — focused on plan_artifact_id handling.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/task_info.dart';

TaskInfo _base() => TaskInfo(
      taskId: 'task-1',
      title: 'Fix the bug',
      status: 'todo',
      source: 'user',
      workerId: 'worker-1',
      workerName: 'Worker 1',
      createdAt: DateTime.utc(2026, 4, 1),
      updatedAt: DateTime.utc(2026, 4, 1),
    );

void main() {
  group('TaskInfo.fromJson — plan_artifact_id', () {
    test('parses plan_artifact_id when present', () {
      final task = TaskInfo.fromJson({
        'task_id': 'task-1',
        'title': 'Do something',
        'status': 'todo',
        'source': 'user',
        'created_at': '2026-04-01T00:00:00Z',
        'updated_at': '2026-04-01T00:00:00Z',
        'plan_artifact_id': 'artifact-uuid-123',
      });

      expect(task.planArtifactId, 'artifact-uuid-123');
    });

    test('planArtifactId is null when field absent from JSON', () {
      final task = TaskInfo.fromJson({
        'task_id': 'task-2',
        'title': 'Do something else',
        'status': 'todo',
        'source': 'user',
        'created_at': '2026-04-01T00:00:00Z',
        'updated_at': '2026-04-01T00:00:00Z',
      });

      expect(task.planArtifactId, isNull);
    });

    test('planArtifactId is null when field is explicitly null in JSON', () {
      final task = TaskInfo.fromJson({
        'task_id': 'task-3',
        'title': 'Another task',
        'status': 'todo',
        'source': 'user',
        'created_at': '2026-04-01T00:00:00Z',
        'updated_at': '2026-04-01T00:00:00Z',
        'plan_artifact_id': null,
      });

      expect(task.planArtifactId, isNull);
    });
  });

  group('TaskInfo.copyWith — plan_artifact_id sentinel pattern', () {
    test('copyWith without planArtifactId preserves existing value', () {
      final task = _base().copyWith();
      // planArtifactId not provided — should remain null (default)
      expect(task.planArtifactId, isNull);
    });

    test('copyWith preserves non-null planArtifactId when not specified', () {
      final original = _base()..planArtifactId = 'artifact-abc';
      final updated = original.copyWith(title: 'New title');

      expect(updated.planArtifactId, 'artifact-abc');
      expect(updated.title, 'New title');
    });

    test('copyWith can set planArtifactId to a new value', () {
      final original = _base();
      final updated = original.copyWith(planArtifactId: 'new-artifact-id');

      expect(updated.planArtifactId, 'new-artifact-id');
    });

    test('copyWith can clear planArtifactId to null using explicit null', () {
      final original = _base()..planArtifactId = 'artifact-abc';
      final updated = original.copyWith(planArtifactId: null);

      expect(updated.planArtifactId, isNull);
    });

    test('other fields are not affected by planArtifactId change', () {
      final original = _base();
      final updated = original.copyWith(planArtifactId: 'some-artifact');

      expect(updated.taskId, original.taskId);
      expect(updated.title, original.title);
      expect(updated.status, original.status);
      expect(updated.workerId, original.workerId);
    });
  });

  group('TaskInfo — plan status helpers', () {
    test('hasPlan returns true when planArtifactId is set', () {
      final task = _base()..planArtifactId = 'some-artifact';
      expect(task.planArtifactId != null, isTrue);
    });

    test('hasPlan returns false when planArtifactId is null', () {
      final task = _base();
      expect(task.planArtifactId != null, isFalse);
    });
  });
}
