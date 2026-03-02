import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/split_tree.dart';
import '../../state/app_state.dart';
import 'resizable_divider.dart';
import 'session_pane.dart';

/// Recursively renders a [SplitNode] tree into nested split panes.
class SplitView extends StatelessWidget {
  final SplitNode node;

  const SplitView({super.key, required this.node});

  @override
  Widget build(BuildContext context) {
    switch (node) {
      case PaneLeaf leaf:
        final appState = context.read<AppState>();
        final pane = appState.panes[leaf.paneId];
        if (pane == null) return const SizedBox.shrink();
        return SessionPane(pane: pane);
      case SplitBranch branch:
        return _SplitBranchView(branch: branch);
    }
  }
}

class _SplitBranchView extends StatelessWidget {
  final SplitBranch branch;

  const _SplitBranchView({required this.branch});

  @override
  Widget build(BuildContext context) {
    final appState = context.read<AppState>();
    final isHorizontal = branch.axis == SplitAxis.horizontal;

    return LayoutBuilder(
      builder: (context, constraints) {
        final totalSize =
            isHorizontal ? constraints.maxWidth : constraints.maxHeight;
        // Account for divider width (6px)
        const dividerSize = 6.0;
        final available = totalSize - dividerSize;
        final firstSize = (available * branch.ratio).clamp(0.0, available);
        final secondSize = available - firstSize;

        final children = <Widget>[
          SizedBox(
            width: isHorizontal ? firstSize : null,
            height: isHorizontal ? null : firstSize,
            child: SplitView(node: branch.first),
          ),
          ResizableDivider(
            axis: branch.axis,
            totalSize: totalSize,
            onDrag: (delta) => appState.updateSplitRatio(
              branch,
              branch.ratio + delta,
            ),
          ),
          SizedBox(
            width: isHorizontal ? secondSize : null,
            height: isHorizontal ? null : secondSize,
            child: SplitView(node: branch.second),
          ),
        ];

        if (isHorizontal) {
          return Row(children: children);
        } else {
          return Column(children: children);
        }
      },
    );
  }
}
