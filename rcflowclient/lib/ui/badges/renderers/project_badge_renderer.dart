import 'package:flutter/material.dart';

import '../../../models/badge_spec.dart';
import '../badge_registry.dart';

/// Registers the project badge renderer with [registry].
void registerProjectBadge(BadgeRegistry registry) {
  registry.register(
      'project', (context, badge) => _ProjectBadge(badge: badge));
}

class _ProjectBadge extends StatelessWidget {
  final BadgeSpec badge;

  const _ProjectBadge({required this.badge});

  static const _normalColor = Color(0xFF6B7280); // grey-500
  static const _errorColor = Color(0xFFEF4444); // red-500

  @override
  Widget build(BuildContext context) {
    final hasError = badge.payload['error'] != null;
    final color = hasError ? _errorColor : _normalColor;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withAlpha(25),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: color.withAlpha(80), width: 0.5),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.folder_outlined,
            size: 10,
            color: color.withAlpha(180),
          ),
          const SizedBox(width: 4),
          Text(
            badge.label,
            style: TextStyle(
              color: color,
              fontSize: 10,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}
