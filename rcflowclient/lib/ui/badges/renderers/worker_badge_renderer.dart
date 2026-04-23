import 'package:flutter/material.dart';

import '../../../models/badge_spec.dart';
import '../badge_chip.dart';
import '../badge_registry.dart';

/// Registers the worker badge renderer with [registry].
void registerWorkerBadge(BadgeRegistry registry) {
  registry.register('worker', (context, badge) => _WorkerBadge(badge: badge));
}

class _WorkerBadge extends StatelessWidget {
  final BadgeSpec badge;

  const _WorkerBadge({required this.badge});

  @override
  Widget build(BuildContext context) {
    // Interactive badges (draft/new-chat context) receive tap handling via
    // the host widget, not here — the renderer only wraps the visual.
    return BadgeChip(
      label: badge.label,
      icon: Icons.dns_outlined,
      trailing:
          badge.interactive ? BadgeChip.neutralDropdownCaret(context) : null,
    );
  }
}
