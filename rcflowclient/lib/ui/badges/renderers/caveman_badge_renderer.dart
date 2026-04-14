import 'package:flutter/material.dart';

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
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: _color.withAlpha(30),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: _color.withAlpha(80), width: 0.5),
      ),
      child: const Text(
        'Caveman',
        style: TextStyle(
          color: _color,
          fontSize: 10,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}
