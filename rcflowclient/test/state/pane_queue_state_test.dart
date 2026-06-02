import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/ws_messages.dart';
import 'package:rcflowclient/state/pane_queue_state.dart';

QueuedMessage _m(String id, int pos, {String content = 'c'}) => QueuedMessage(
  queuedId: id,
  position: pos,
  content: content,
  displayContent: content,
  submittedAt: DateTime(2026, 1, 1),
  updatedAt: DateTime(2026, 1, 1),
);

void main() {
  group('PaneQueueState', () {
    test('upsert inserts ordered by position; updates in place', () {
      final q = PaneQueueState();
      q.upsert(_m('b', 1));
      q.upsert(_m('a', 0));
      expect(q.snapshot.map((e) => e.queuedId).toList(), ['a', 'b']);
      expect(q.length, 2);

      q.upsert(_m('a', 0, content: 'updated'));
      expect(q.length, 2);
      expect(q.snapshot.first.content, 'updated');
    });

    test('dequeue removes and renumbers densely; returns found', () {
      final q = PaneQueueState()
        ..upsert(_m('a', 0))
        ..upsert(_m('b', 1))
        ..upsert(_m('c', 2));
      expect(q.dequeue('b'), isTrue);
      expect(q.snapshot.map((e) => e.queuedId).toList(), ['a', 'c']);
      expect(q.snapshot.map((e) => e.position).toList(), [0, 1]);
      expect(q.dequeue('zzz'), isFalse);
    });

    test('update applies only non-null fields; returns found', () {
      final q = PaneQueueState()..upsert(_m('a', 0, content: 'orig'));
      expect(q.update('a', content: 'new'), isTrue);
      expect(q.snapshot.first.content, 'new');
      expect(q.snapshot.first.displayContent, 'orig'); // unchanged
      expect(q.update('missing', content: 'x'), isFalse);
    });

    test('replaceSnapshot sorts + replaces; editText mutates', () {
      final q = PaneQueueState()..upsert(_m('old', 0));
      q.replaceSnapshot([_m('y', 1), _m('x', 0)]);
      expect(q.snapshot.map((e) => e.queuedId).toList(), ['x', 'y']);
      expect(q.editText('x', 'edited', DateTime(2026, 2, 2)), isTrue);
      expect(q.snapshot.first.content, 'edited');
      expect(q.snapshot.first.displayContent, 'edited');
    });

    test('clear empties the queue', () {
      final q = PaneQueueState()..upsert(_m('a', 0));
      q.clear();
      expect(q.length, 0);
    });
  });
}
