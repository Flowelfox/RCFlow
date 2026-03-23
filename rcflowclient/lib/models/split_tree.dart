/// Binary split tree model for multi-pane layout.
///
/// Each node is either a [PaneLeaf] (single session view) or a [SplitBranch]
/// (two children divided along an axis). The tree always has at least one leaf.
library;

/// Type of content a pane displays.
enum PaneType { chat, terminal, task, artifact, linearIssue, workerSettings }

enum SplitAxis { horizontal, vertical }

/// Drop zone for drag-and-drop split targeting.
enum DropZone { left, right, top, bottom }

/// Returns the [SplitAxis] for a given [DropZone].
SplitAxis dropZoneAxis(DropZone zone) => switch (zone) {
      DropZone.left || DropZone.right => SplitAxis.horizontal,
      DropZone.top || DropZone.bottom => SplitAxis.vertical,
    };

/// Whether the new pane should be inserted as the first child.
bool dropZoneIsFirst(DropZone zone) => switch (zone) {
      DropZone.left || DropZone.top => true,
      DropZone.right || DropZone.bottom => false,
    };

sealed class SplitNode {
  const SplitNode();
}

/// A single session pane identified by [paneId].
class PaneLeaf extends SplitNode {
  final String paneId;
  const PaneLeaf(this.paneId);
}

/// Two children separated by a divider along [axis].
/// [ratio] (0.0–1.0) controls the first child's share of available space.
class SplitBranch extends SplitNode {
  final SplitNode first;
  final SplitNode second;
  final SplitAxis axis;
  double ratio;

  SplitBranch({
    required this.first,
    required this.second,
    required this.axis,
    this.ratio = 0.5,
  });
}

// ---------------------------------------------------------------------------
// Pure tree operations
// ---------------------------------------------------------------------------

/// Replace the leaf identified by [targetId] with a branch containing it and a
/// new leaf [newPaneId], split along [axis].
SplitNode splitPane(
    SplitNode node, String targetId, String newPaneId, SplitAxis axis) {
  switch (node) {
    case PaneLeaf leaf:
      if (leaf.paneId == targetId) {
        return SplitBranch(
          first: leaf,
          second: PaneLeaf(newPaneId),
          axis: axis,
        );
      }
      return leaf;
    case SplitBranch branch:
      final newFirst = splitPane(branch.first, targetId, newPaneId, axis);
      final newSecond = splitPane(branch.second, targetId, newPaneId, axis);
      if (identical(newFirst, branch.first) &&
          identical(newSecond, branch.second)) {
        return branch;
      }
      return SplitBranch(
        first: newFirst,
        second: newSecond,
        axis: branch.axis,
        ratio: branch.ratio,
      );
  }
}

/// Like [splitPane] but allows inserting the new pane as either first or second
/// child, controlled by [insertFirst].
SplitNode splitPaneAtPosition(SplitNode node, String targetId,
    String newPaneId, SplitAxis axis, {required bool insertFirst}) {
  switch (node) {
    case PaneLeaf leaf:
      if (leaf.paneId == targetId) {
        final newLeaf = PaneLeaf(newPaneId);
        return SplitBranch(
          first: insertFirst ? newLeaf : leaf,
          second: insertFirst ? leaf : newLeaf,
          axis: axis,
        );
      }
      return leaf;
    case SplitBranch branch:
      final newFirst = splitPaneAtPosition(
          branch.first, targetId, newPaneId, axis, insertFirst: insertFirst);
      final newSecond = splitPaneAtPosition(
          branch.second, targetId, newPaneId, axis, insertFirst: insertFirst);
      if (identical(newFirst, branch.first) &&
          identical(newSecond, branch.second)) {
        return branch;
      }
      return SplitBranch(
        first: newFirst,
        second: newSecond,
        axis: branch.axis,
        ratio: branch.ratio,
      );
  }
}

/// Remove the leaf identified by [targetId]. If it's inside a branch, the
/// sibling replaces the branch. Returns null if [targetId] is the root leaf
/// (cannot close the last pane).
SplitNode? closePane(SplitNode node, String targetId) {
  switch (node) {
    case PaneLeaf leaf:
      return leaf.paneId == targetId ? null : leaf;
    case SplitBranch branch:
      final newFirst = closePane(branch.first, targetId);
      if (newFirst == null) return branch.second;
      final newSecond = closePane(branch.second, targetId);
      if (newSecond == null) return branch.first;
      if (identical(newFirst, branch.first) &&
          identical(newSecond, branch.second)) {
        return branch;
      }
      return SplitBranch(
        first: newFirst,
        second: newSecond,
        axis: branch.axis,
        ratio: branch.ratio,
      );
  }
}

/// Collect all pane IDs in tree order.
List<String> allPaneIds(SplitNode node) {
  switch (node) {
    case PaneLeaf leaf:
      return [leaf.paneId];
    case SplitBranch branch:
      return [...allPaneIds(branch.first), ...allPaneIds(branch.second)];
  }
}

/// Whether the tree contains a pane with the given [id].
bool containsPane(SplitNode node, String id) {
  switch (node) {
    case PaneLeaf leaf:
      return leaf.paneId == id;
    case SplitBranch branch:
      return containsPane(branch.first, id) ||
          containsPane(branch.second, id);
  }
}

/// Count total panes in the tree.
int paneCount(SplitNode node) {
  switch (node) {
    case PaneLeaf():
      return 1;
    case SplitBranch branch:
      return paneCount(branch.first) + paneCount(branch.second);
  }
}
