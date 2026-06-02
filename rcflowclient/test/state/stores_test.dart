import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/state/stores/artifact_store.dart';
import 'package:rcflowclient/state/stores/linear_issue_store.dart';
import 'package:rcflowclient/state/stores/task_store.dart';

Map<String, dynamic> _issue(
  String id, {
  String? taskId,
  String updatedAt = '2026-01-01T00:00:00Z',
}) => {
  'id': id,
  'linear_id': 'L-$id',
  'identifier': id.toUpperCase(),
  'title': 'Issue $id',
  'task_id': taskId,
  'updated_at': updatedAt,
};

Map<String, dynamic> _task(
  String id, {
  List<Map<String, dynamic>> sessions = const [],
  String updatedAt = '2026-01-01T00:00:00Z',
}) => {
  'task_id': id,
  'title': 'Task $id',
  'sessions': sessions,
  'updated_at': updatedAt,
};

Map<String, dynamic> _artifact(
  String id, {
  String discoveredAt = '2026-01-01T00:00:00Z',
}) => {
  'artifact_id': id,
  'file_name': '$id.txt',
  'discovered_at': discoveredAt,
};

void main() {
  group('LinearIssueStore', () {
    test('replaceWorker isolates by worker; all() sorts by updatedAt desc', () {
      final s = LinearIssueStore();
      s.replaceWorker('w1', 'W1', [
        _issue('a', updatedAt: '2026-01-01T00:00:00Z'),
        _issue('b', updatedAt: '2026-03-01T00:00:00Z'),
      ]);
      s.replaceWorker('w2', 'W2', [
        _issue('c', updatedAt: '2026-02-01T00:00:00Z'),
      ]);
      expect(s.all().map((i) => i.id).toList(), ['b', 'c', 'a']);

      // Replacing w1 leaves w2 untouched.
      s.replaceWorker('w1', 'W1', [_issue('d')]);
      expect(s.all().map((i) => i.id).toSet(), {'c', 'd'});
    });

    test('get / forTask / unlinked / upsert / remove', () {
      final s = LinearIssueStore();
      s.replaceWorker('w1', 'W1', [
        _issue('a', taskId: 't1'),
        _issue('b'),
        _issue('c', taskId: 't1'),
      ]);
      expect(s.get('a')!.taskId, 't1');
      expect(s.forTask('t1').map((i) => i.id).toSet(), {'a', 'c'});
      expect(s.unlinked().map((i) => i.id).toList(), ['b']);
      expect(s.remove('b'), isTrue);
      expect(s.remove('b'), isFalse);
      expect(s.get('b'), isNull);
    });

    test('byWorker buckets by workerId', () {
      final s = LinearIssueStore();
      s.replaceWorker('w1', 'W1', [_issue('a')]);
      s.replaceWorker('w2', 'W2', [_issue('b'), _issue('c')]);
      final m = s.byWorker(const []);
      expect(m['w1']!.length, 1);
      expect(m['w2']!.length, 2);
    });
  });

  group('TaskStore', () {
    test('all() sorts desc; byWorker; get; remove', () {
      final s = TaskStore();
      s.replaceWorker('w1', 'W1', [
        _task('a', updatedAt: '2026-01-01T00:00:00Z'),
        _task('b', updatedAt: '2026-05-01T00:00:00Z'),
      ]);
      expect(s.all().map((t) => t.taskId).toList(), ['b', 'a']);
      expect(s.byWorker(const [])['w1']!.length, 2);
      expect(s.get('a')!.title, 'Task a');
      expect(s.remove('a'), isTrue);
      expect(s.get('a'), isNull);
    });

    test('forSession / isAttachedToSession', () {
      final s = TaskStore();
      s.replaceWorker('w1', 'W1', [
        _task(
          'a',
          sessions: [
            {'session_id': 's1'},
          ],
        ),
        _task('b'),
      ]);
      expect(s.isAttachedToSession('s1'), isTrue);
      expect(s.isAttachedToSession('s9'), isFalse);
      expect(s.forSession('s1').map((t) => t.taskId).toList(), ['a']);
    });
  });

  group('ArtifactStore', () {
    test(
      'all() sorts by discoveredAt desc; replaceWorker isolates; remove',
      () {
        final s = ArtifactStore();
        s.replaceWorker('w1', 'W1', [
          _artifact('a', discoveredAt: '2026-01-01T00:00:00Z'),
          _artifact('b', discoveredAt: '2026-06-01T00:00:00Z'),
        ]);
        s.replaceWorker('w2', 'W2', [_artifact('c')]);
        expect(s.all().first.artifactId, 'b');
        s.replaceWorker('w1', 'W1', [_artifact('d')]);
        expect(s.all().map((a) => a.artifactId).toSet(), {'c', 'd'});
        expect(s.get('d')!.fileName, 'd.txt');
        expect(s.remove('d'), isTrue);
        expect(s.remove('d'), isFalse);
      },
    );
  });
}
