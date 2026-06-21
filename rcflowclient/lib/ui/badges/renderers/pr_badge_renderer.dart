import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/badge_spec.dart';
import '../../../state/app_state.dart';
import '../badge_chip.dart';
import '../badge_registry.dart';

/// Registers the interactive GitHub pull-request badge.
///
/// The badge appears on a session that was created from the Pull requests view
/// or whose branch had a PR opened for it mid-run. Tapping it opens that PR in
/// the review pane.
void registerPrBadge(BadgeRegistry registry) {
  registry.register('pr', (context, badge) => _PrBadge(badge: badge));
}

class _PrBadge extends StatelessWidget {
  final BadgeSpec badge;

  const _PrBadge({required this.badge});

  // GitHub "open PR" green — signals this chip carries an action.
  static const _prColor = Color(0xFF3FB950);

  @override
  Widget build(BuildContext context) {
    final prId = badge.payload['pr_id'] as String?;
    final chip = BadgeChip(
      color: _prColor,
      label: badge.label,
      icon: Icons.merge_rounded,
    );
    if (prId == null || prId.isEmpty) return chip;
    return GestureDetector(
      onTap: () => context.read<AppState>().openGithubPrInPane(prId),
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        child: chip,
      ),
    );
  }
}
