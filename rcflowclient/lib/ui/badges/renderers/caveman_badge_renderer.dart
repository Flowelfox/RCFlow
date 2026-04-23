import 'package:flutter/material.dart';

import '../badge_chip.dart';
import '../badge_registry.dart';

/// Registers the caveman badge renderer with [registry].
void registerCavemanBadge(BadgeRegistry registry) {
  registry.register(
      'caveman', (context, badge) => const _CavemanBadge());
}

class _CavemanBadge extends StatelessWidget {
  const _CavemanBadge();

  static const _color = Color(0xFF92400E); // amber-800

  @override
  Widget build(BuildContext context) {
    return const BadgeChip(
      color: _color,
      label: 'Caveman',
      icon: Icons.warning_amber_rounded,
    );
  }
}
