import 'package:flutter/material.dart';

import '../../../models/badge_spec.dart';
import '../badge_registry.dart';

/// Registers the worker badge renderer with [registry].
void registerWorkerBadge(BadgeRegistry registry) {
  registry.register('worker', (context, badge) => _WorkerBadge(badge: badge));
}

class _WorkerBadge extends StatelessWidget {
  final BadgeSpec badge;

  const _WorkerBadge({required this.badge});

  static const _color = Color(0xFF6366F1); // indigo-500

  @override
  Widget build(BuildContext context) {
    final chip = Container(
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
            Icons.dns_outlined,
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
          if (badge.interactive) ...[
            const SizedBox(width: 2),
            Icon(
              Icons.arrow_drop_down,
              size: 12,
              color: _color.withAlpha(150),
            ),
          ],
        ],
      ),
    );

    // Interactive badges (draft/new-chat context) receive tap handling via
    // the host widget, not here — the renderer only wraps the visual.
    return chip;
  }
}
