import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/badge_spec.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import '../badge_chip.dart';
import '../badge_registry.dart';

/// Registers the interactive Claude Code model-picker badge.
void registerModelBadge(BadgeRegistry registry) {
  registry.register('model', (context, badge) => _ModelBadge(badge: badge));
}

/// The Claude Code model aliases the picker offers — mirrors Claude Code's own
/// `/model` menu. `null` value clears the override (use the worker's model).
const _modelOptions = <({String? value, String label})>[
  (value: null, label: 'Default'),
  (value: 'opus', label: 'Opus'),
  (value: 'sonnet', label: 'Sonnet'),
  (value: 'opusplan', label: 'Opus Plan'),
  (value: 'haiku', label: 'Haiku'),
];

class _ModelBadge extends StatelessWidget {
  final BadgeSpec badge;
  const _ModelBadge({required this.badge});

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTapUp: (d) => _showMenu(context, d.globalPosition),
      child: MouseRegion(
        cursor: SystemMouseCursors.click,
        child: BadgeChip(
          label: badge.label,
          icon: Icons.tune_rounded,
        ),
      ),
    );
  }

  Future<void> _showMenu(BuildContext context, Offset pos) async {
    final colors = context.appColors;
    final sessionId = badge.payload['session_id'] as String?;
    final current = badge.payload['selected_model'] as String?;
    if (sessionId == null) return;

    final chosen = await showMenu<String?>(
      context: context,
      color: colors.bgElevated,
      position: RelativeRect.fromLTRB(pos.dx, pos.dy, pos.dx, pos.dy),
      items: [
        for (final opt in _modelOptions)
          PopupMenuItem<String?>(
            value: opt.value ?? '__default__',
            height: 36,
            child: Row(
              children: [
                Icon(
                  opt.value == current
                      ? Icons.check_rounded
                      : Icons.remove,
                  size: 16,
                  color: opt.value == current
                      ? colors.accent
                      : Colors.transparent,
                ),
                const SizedBox(width: 8),
                Text(opt.label, style: TextStyle(color: colors.textPrimary)),
              ],
            ),
          ),
      ],
    );
    if (chosen == null || !context.mounted) return;
    final model = chosen == '__default__' ? null : chosen;
    if (model == current) return;
    await context.read<AppState>().setSessionModel(sessionId, model);
  }
}
