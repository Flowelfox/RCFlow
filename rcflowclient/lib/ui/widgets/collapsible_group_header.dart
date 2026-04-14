/// Collapsible group-section header used across the session-panel list panels.
///
/// Renders a tappable row with a chevron icon, an optional leading [icon],
/// a label, and an item count badge.  Toggling calls [onToggle]; the caller
/// owns the collapsed-state.
library;

import 'package:flutter/material.dart';

import '../../theme.dart';

class CollapsibleGroupHeader extends StatelessWidget {
  const CollapsibleGroupHeader({
    super.key,
    required this.label,
    required this.count,
    required this.collapsed,
    required this.onToggle,
    this.icon,
    this.trailing,
    this.padding = const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
  });

  /// Display label for the group (e.g. `'In Progress'`, `'worker-1'`).
  final String label;

  /// Number of items in this group shown in the badge.
  final int count;

  /// Whether the group is currently collapsed.
  final bool collapsed;

  /// Called when the user taps the header to toggle collapse state.
  final VoidCallback onToggle;

  /// Optional leading icon shown between the chevron and the label.
  final IconData? icon;

  /// Optional trailing widget (e.g. action buttons) shown at the far right.
  final Widget? trailing;

  /// Padding around the header row content.
  final EdgeInsets padding;

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onToggle,
      child: Padding(
        padding: padding,
        child: Row(
          children: [
            Icon(
              collapsed
                  ? Icons.chevron_right_rounded
                  : Icons.expand_more_rounded,
              color: context.appColors.textMuted,
              size: 18,
            ),
            if (icon != null) ...[
              const SizedBox(width: 4),
              Icon(icon, color: context.appColors.textMuted, size: 13),
            ],
            const SizedBox(width: 4),
            Expanded(
              child: Text(
                '$label ($count)',
                style: TextStyle(
                  color: context.appColors.textSecondary,
                  fontSize: 11,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 0.5,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ),
            ?trailing,
          ],
        ),
      ),
    );
  }
}
