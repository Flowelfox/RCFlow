import 'package:flutter/material.dart';

import '../../models/badge_spec.dart';
import 'badge_registry.dart';

/// A horizontal row of session badge chips.
///
/// Renders all [badges] whose [BadgeSpec.visible] is true, sorted by
/// [BadgeSpec.priority] (ascending), with an optional [slotFilter] to show
/// only specific badge types.
///
/// Each chip is rendered via [BadgeRegistry.instance.render] so no badge
/// display logic lives here — only ordering and filtering.
class BadgeBar extends StatelessWidget {
  /// The full badge list, typically from [SessionInfo.badges].
  final List<BadgeSpec> badges;

  /// When non-null, only badges whose [BadgeSpec.type] is in this set are
  /// shown.  Pass ``null`` (the default) to show all visible badges.
  final Set<String>? slotFilter;

  const BadgeBar({
    required this.badges,
    this.slotFilter,
    super.key,
  });

  @override
  Widget build(BuildContext context) {
    final visible = badges
        .where((b) => b.visible)
        .where((b) => slotFilter == null || slotFilter!.contains(b.type))
        .toList()
      ..sort((a, b) => a.priority.compareTo(b.priority));

    if (visible.isEmpty) return const SizedBox.shrink();

    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        for (int i = 0; i < visible.length; i++) ...[
          BadgeRegistry.instance.render(context, visible[i]),
          if (i < visible.length - 1) const SizedBox(width: 4),
        ],
      ],
    );
  }
}
