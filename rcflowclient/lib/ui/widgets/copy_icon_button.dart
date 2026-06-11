import 'dart:async';

import 'package:flutter/material.dart';

import '../../theme.dart';

/// A compact copy button that runs [onCopy] and briefly swaps its glyph to a
/// check mark as feedback. Hover shows a click cursor and a subtle background.
/// Self-contained — no Scaffold / SnackBar dependency — so it can sit inside
/// message bubbles, code blocks, and tool headers alike.
class CopyIconButton extends StatefulWidget {
  final Future<void> Function() onCopy;
  final double iconSize;
  final String tooltip;

  const CopyIconButton({
    super.key,
    required this.onCopy,
    this.iconSize = 14,
    this.tooltip = 'Copy',
  });

  @override
  State<CopyIconButton> createState() => _CopyIconButtonState();
}

class _CopyIconButtonState extends State<CopyIconButton> {
  bool _copied = false;
  bool _hovered = false;
  Timer? _resetTimer;

  @override
  void dispose() {
    _resetTimer?.cancel();
    super.dispose();
  }

  Future<void> _handleTap() async {
    await widget.onCopy();
    if (!mounted) return;
    setState(() => _copied = true);
    _resetTimer?.cancel();
    _resetTimer = Timer(const Duration(milliseconds: 1200), () {
      if (mounted) setState(() => _copied = false);
    });
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final color = _copied ? colors.successText : colors.textMuted;
    return Tooltip(
      message: _copied ? 'Copied' : widget.tooltip,
      waitDuration: const Duration(milliseconds: 400),
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        onEnter: (_) => setState(() => _hovered = true),
        onExit: (_) => setState(() => _hovered = false),
        child: GestureDetector(
          onTap: _handleTap,
          behavior: HitTestBehavior.opaque,
          child: Container(
            padding: const EdgeInsets.all(4),
            decoration: BoxDecoration(
              color: _hovered ? colors.bgElevated : Colors.transparent,
              borderRadius: BorderRadius.circular(6),
              border: Border.all(
                color: _hovered ? colors.divider : Colors.transparent,
                width: 0.5,
              ),
            ),
            child: Icon(
              _copied ? Icons.check_rounded : Icons.content_copy_rounded,
              size: widget.iconSize,
              color: color,
            ),
          ),
        ),
      ),
    );
  }
}
