import 'package:flutter/material.dart';

import '../../../models/badge_spec.dart';
import '../badge_chip.dart';
import '../badge_registry.dart';

/// Registers the project badge renderer with [registry].
void registerProjectBadge(BadgeRegistry registry) {
  registry.register(
      'project', (context, badge) => _ProjectBadge(badge: badge));
}

class _ProjectBadge extends StatelessWidget {
  final BadgeSpec badge;

  const _ProjectBadge({required this.badge});

  static const _errorColor = Color(0xFFEF4444); // red-500

  @override
  Widget build(BuildContext context) {
    final hasError = badge.payload['error'] != null;
    return BadgeChip(
      // Neutral by default; only color when there is an error to signal.
      color: hasError ? _errorColor : null,
      label: badge.label,
      icon: hasError ? Icons.error_outline_rounded : Icons.folder_outlined,
    );
  }
}
