import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/split_tree.dart';

void main() {
  group('PaneLeaf', () {
    test('stores pane ID', () {
      const leaf = PaneLeaf('p1');
      expect(leaf.paneId, 'p1');
    });
  });

  group('SplitBranch', () {
    test('stores children, axis, and default ratio', () {
      final branch = SplitBranch(
        first: const PaneLeaf('a'),
        second: const PaneLeaf('b'),
        axis: SplitAxis.horizontal,
      );
      expect((branch.first as PaneLeaf).paneId, 'a');
      expect((branch.second as PaneLeaf).paneId, 'b');
      expect(branch.axis, SplitAxis.horizontal);
      expect(branch.ratio, 0.5);
    });

    test('ratio is mutable', () {
      final branch = SplitBranch(
        first: const PaneLeaf('a'),
        second: const PaneLeaf('b'),
        axis: SplitAxis.vertical,
        ratio: 0.3,
      );
      expect(branch.ratio, 0.3);
      branch.ratio = 0.7;
      expect(branch.ratio, 0.7);
    });
  });

  group('allPaneIds', () {
    test('single leaf returns one ID', () {
      expect(allPaneIds(const PaneLeaf('x')), ['x']);
    });

    test('branch returns all IDs in order', () {
      final tree = SplitBranch(
        first: const PaneLeaf('a'),
        second: SplitBranch(
          first: const PaneLeaf('b'),
          second: const PaneLeaf('c'),
          axis: SplitAxis.vertical,
        ),
        axis: SplitAxis.horizontal,
      );
      expect(allPaneIds(tree), ['a', 'b', 'c']);
    });
  });

  group('containsPane', () {
    test('finds pane in leaf', () {
      expect(containsPane(const PaneLeaf('a'), 'a'), true);
      expect(containsPane(const PaneLeaf('a'), 'b'), false);
    });

    test('finds pane in nested branch', () {
      final tree = SplitBranch(
        first: const PaneLeaf('a'),
        second: SplitBranch(
          first: const PaneLeaf('b'),
          second: const PaneLeaf('c'),
          axis: SplitAxis.vertical,
        ),
        axis: SplitAxis.horizontal,
      );
      expect(containsPane(tree, 'a'), true);
      expect(containsPane(tree, 'c'), true);
      expect(containsPane(tree, 'z'), false);
    });
  });

  group('paneCount', () {
    test('single leaf returns 1', () {
      expect(paneCount(const PaneLeaf('x')), 1);
    });

    test('branch with two leaves returns 2', () {
      final tree = SplitBranch(
        first: const PaneLeaf('a'),
        second: const PaneLeaf('b'),
        axis: SplitAxis.horizontal,
      );
      expect(paneCount(tree), 2);
    });

    test('nested tree returns correct count', () {
      final tree = SplitBranch(
        first: const PaneLeaf('a'),
        second: SplitBranch(
          first: const PaneLeaf('b'),
          second: const PaneLeaf('c'),
          axis: SplitAxis.vertical,
        ),
        axis: SplitAxis.horizontal,
      );
      expect(paneCount(tree), 3);
    });
  });

  group('splitPane', () {
    test('splits a single leaf into a branch', () {
      const root = PaneLeaf('p1');
      final result = splitPane(root, 'p1', 'p2', SplitAxis.horizontal);

      expect(result, isA<SplitBranch>());
      final branch = result as SplitBranch;
      expect(branch.axis, SplitAxis.horizontal);
      expect((branch.first as PaneLeaf).paneId, 'p1');
      expect((branch.second as PaneLeaf).paneId, 'p2');
      expect(branch.ratio, 0.5);
    });

    test('returns unchanged node when target not found', () {
      const root = PaneLeaf('p1');
      final result = splitPane(root, 'nonexistent', 'p2', SplitAxis.vertical);
      expect(identical(result, root), true);
    });

    test('splits nested leaf correctly', () {
      final root = SplitBranch(
        first: const PaneLeaf('a'),
        second: const PaneLeaf('b'),
        axis: SplitAxis.horizontal,
      );
      final result = splitPane(root, 'b', 'c', SplitAxis.vertical);

      expect(result, isA<SplitBranch>());
      final outer = result as SplitBranch;
      expect((outer.first as PaneLeaf).paneId, 'a');
      expect(outer.second, isA<SplitBranch>());
      final inner = outer.second as SplitBranch;
      expect(inner.axis, SplitAxis.vertical);
      expect((inner.first as PaneLeaf).paneId, 'b');
      expect((inner.second as PaneLeaf).paneId, 'c');
    });
  });

  group('closePane', () {
    test('returns null for root leaf (cannot close last pane)', () {
      expect(closePane(const PaneLeaf('p1'), 'p1'), null);
    });

    test('returns unchanged node when target not found', () {
      const root = PaneLeaf('p1');
      final result = closePane(root, 'nonexistent');
      expect(identical(result, root), true);
    });

    test('closing first child returns second sibling', () {
      final root = SplitBranch(
        first: const PaneLeaf('a'),
        second: const PaneLeaf('b'),
        axis: SplitAxis.horizontal,
      );
      final result = closePane(root, 'a');
      expect(result, isA<PaneLeaf>());
      expect((result as PaneLeaf).paneId, 'b');
    });

    test('closing second child returns first sibling', () {
      final root = SplitBranch(
        first: const PaneLeaf('a'),
        second: const PaneLeaf('b'),
        axis: SplitAxis.horizontal,
      );
      final result = closePane(root, 'b');
      expect(result, isA<PaneLeaf>());
      expect((result as PaneLeaf).paneId, 'a');
    });

    test('closing nested pane collapses parent branch', () {
      final root = SplitBranch(
        first: const PaneLeaf('a'),
        second: SplitBranch(
          first: const PaneLeaf('b'),
          second: const PaneLeaf('c'),
          axis: SplitAxis.vertical,
        ),
        axis: SplitAxis.horizontal,
      );
      final result = closePane(root, 'b');

      expect(result, isA<SplitBranch>());
      final branch = result as SplitBranch;
      expect((branch.first as PaneLeaf).paneId, 'a');
      expect((branch.second as PaneLeaf).paneId, 'c');
    });

    test('closing deeply nested pane works correctly', () {
      final root = SplitBranch(
        first: SplitBranch(
          first: const PaneLeaf('a'),
          second: const PaneLeaf('b'),
          axis: SplitAxis.vertical,
        ),
        second: const PaneLeaf('c'),
        axis: SplitAxis.horizontal,
      );
      final result = closePane(root, 'a');

      expect(result, isA<SplitBranch>());
      final branch = result as SplitBranch;
      expect((branch.first as PaneLeaf).paneId, 'b');
      expect((branch.second as PaneLeaf).paneId, 'c');
    });
  });
}
