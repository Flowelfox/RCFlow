import 'package:flutter/material.dart';

import '../../../models/badge_spec.dart';
import '../badge_chip.dart';
import '../badge_registry.dart';

/// Registers the status badge renderer with [registry].
void registerStatusBadge(BadgeRegistry registry) {
  registry.register('status', (context, badge) => _StatusBadge(badge: badge));
}

class _StatusBadge extends StatelessWidget {
  final BadgeSpec badge;

  const _StatusBadge({required this.badge});

  @override
  Widget build(BuildContext context) {
    final (label, color) = switch (badge.label) {
      'active' || 'executing' => ('Active', const Color(0xFF3B82F6)),
      'paused' => ('Paused', const Color(0xFFF59E0B)),
      'completed' => ('Done', const Color(0xFF10B981)),
      'failed' => ('Failed', const Color(0xFFEF4444)),
      'cancelled' => ('Ended', const Color(0xFF6B7280)),
      _ => (badge.label, const Color(0xFF6B7280)),
    };

    return BadgeChip(color: color, label: label);
  }
}
