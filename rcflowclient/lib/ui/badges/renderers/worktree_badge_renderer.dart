import 'package:flutter/material.dart';

import '../../../models/badge_spec.dart';
import '../badge_registry.dart';

/// Registers the worktree badge renderer with [registry].
void registerWorktreeBadge(BadgeRegistry registry) {
  registry.register(
      'worktree', (context, badge) => _WorktreeBadge(badge: badge));
}

class _WorktreeBadge extends StatelessWidget {
  final BadgeSpec badge;

  const _WorktreeBadge({required this.badge});

  static const _color = Color(0xFF0EA5E9); // sky-500

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: _color.withAlpha(25),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: _color.withAlpha(70), width: 0.5),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.account_tree_outlined,
            size: 10,
            color: _color.withAlpha(180),
          ),
          const SizedBox(width: 4),
          Text(
            badge.label,
            style: const TextStyle(
              color: _color,
              fontSize: 10,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}
