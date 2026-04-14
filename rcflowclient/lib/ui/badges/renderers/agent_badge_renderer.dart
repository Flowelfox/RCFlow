import 'package:flutter/material.dart';

import '../../../models/badge_spec.dart';
import '../badge_registry.dart';

/// Registers the agent badge renderer with [registry].
void registerAgentBadge(BadgeRegistry registry) {
  registry.register('agent', (context, badge) => _AgentBadge(badge: badge));
}

class _AgentBadge extends StatelessWidget {
  final BadgeSpec badge;

  const _AgentBadge({required this.badge});

  static const _color = Color(0xFF8B5CF6); // violet-500

  @override
  Widget build(BuildContext context) {
    final label = switch (badge.label) {
      'claude_code' => 'Claude Code',
      'codex' => 'Codex',
      'opencode' => 'OpenCode',
      _ => badge.label,
    };

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
            Icons.smart_toy_outlined,
            size: 10,
            color: _color.withAlpha(180),
          ),
          const SizedBox(width: 4),
          Text(
            label,
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
