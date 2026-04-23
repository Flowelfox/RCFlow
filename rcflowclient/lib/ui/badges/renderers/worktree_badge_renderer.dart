import 'package:flutter/material.dart';

import '../../../models/badge_spec.dart';
import '../badge_chip.dart';
import '../badge_registry.dart';

/// Registers the worktree badge renderer with [registry].
void registerWorktreeBadge(BadgeRegistry registry) {
  registry.register(
      'worktree', (context, badge) => _WorktreeBadge(badge: badge));
}

class _WorktreeBadge extends StatelessWidget {
  final BadgeSpec badge;

  const _WorktreeBadge({required this.badge});

  @override
  Widget build(BuildContext context) {
    return BadgeChip(
      label: badge.label,
      icon: Icons.account_tree_outlined,
    );
  }
}
