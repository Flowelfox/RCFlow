import 'package:flutter/material.dart';

import '../../../models/badge_spec.dart';
import '../badge_chip.dart';
import '../badge_registry.dart';

/// Registers the agent badge renderer with [registry].
void registerAgentBadge(BadgeRegistry registry) {
  registry.register('agent', (context, badge) => _AgentBadge(badge: badge));
}

class _AgentBadge extends StatelessWidget {
  final BadgeSpec badge;

  const _AgentBadge({required this.badge});

  @override
  Widget build(BuildContext context) {
    // Match both the raw agent_type (used in draft badges from
    // DraftBadgeComposer) and the display label sent by the server.
    final label = switch (badge.label) {
      'claude_code' || 'ClaudeCode' => 'Claude Code',
      'codex' || 'Codex' => 'Codex',
      'opencode' || 'OpenCode' => 'OpenCode',
      _ => badge.label,
    };

    return BadgeChip(
      label: label,
      icon: Icons.smart_toy_outlined,
    );
  }
}
