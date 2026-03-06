import 'package:flutter/material.dart';

import '../../models/split_tree.dart';
import '../../theme.dart';

/// A thin draggable divider between split panes.
class ResizableDivider extends StatefulWidget {
  final SplitAxis axis;
  final double totalSize;
  final ValueChanged<double> onDrag;

  const ResizableDivider({
    super.key,
    required this.axis,
    required this.totalSize,
    required this.onDrag,
  });

  @override
  State<ResizableDivider> createState() => _ResizableDividerState();
}

class _ResizableDividerState extends State<ResizableDivider> {
  bool _hovering = false;
  bool _dragging = false;

  MouseCursor get _cursor => widget.axis == SplitAxis.horizontal
      ? SystemMouseCursors.resizeColumn
      : SystemMouseCursors.resizeRow;

  @override
  Widget build(BuildContext context) {
    final isHorizontal = widget.axis == SplitAxis.horizontal;
    final highlighted = _hovering || _dragging;

    return MouseRegion(
      cursor: _cursor,
      onEnter: (_) => setState(() => _hovering = true),
      onExit: (_) => setState(() => _hovering = false),
      child: GestureDetector(
        onPanStart: (_) => setState(() => _dragging = true),
        onPanEnd: (_) => setState(() => _dragging = false),
        onPanCancel: () => setState(() => _dragging = false),
        onPanUpdate: (details) {
          final delta = isHorizontal ? details.delta.dx : details.delta.dy;
          if (widget.totalSize > 0) {
            widget.onDrag(delta / widget.totalSize);
          }
        },
        child: Container(
          width: isHorizontal ? 6 : double.infinity,
          height: isHorizontal ? double.infinity : 6,
          color: Colors.transparent,
          child: Center(
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 150),
              width: isHorizontal ? (highlighted ? 3 : 1) : double.infinity,
              height: isHorizontal ? double.infinity : (highlighted ? 3 : 1),
              color: highlighted ? context.appColors.accent.withAlpha(180) : context.appColors.divider,
            ),
          ),
        ),
      ),
    );
  }
}
