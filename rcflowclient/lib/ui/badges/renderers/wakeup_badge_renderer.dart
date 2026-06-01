import 'package:flutter/material.dart';

import '../../../models/badge_spec.dart';
import '../badge_chip.dart';
import '../badge_registry.dart';

/// Registers the wakeup badge renderer with [registry].
///
/// The badge appears whenever the session has at least one pending
/// `ScheduleWakeup` call.  Label = HH:MM of the next fire when there is
/// one wake, or `N wakes` when several are queued.  Tapping shows an
/// alert dialog listing the wakes with their full prompts so the user
/// can verify what will run.
void registerWakeupBadge(BadgeRegistry registry) {
  registry.register(
      'wakeup', (context, badge) => _WakeupBadge(badge: badge));
}

class _WakeupBadge extends StatelessWidget {
  final BadgeSpec badge;

  const _WakeupBadge({required this.badge});

  @override
  Widget build(BuildContext context) {
    final wakes = badge.payload['wakes'] as List<dynamic>? ?? const [];
    final chip = BadgeChip(
      label: badge.label,
      icon: Icons.alarm_rounded,
    );
    if (wakes.isEmpty) return chip;
    return InkWell(
      onTap: () => _showWakeList(context, wakes),
      borderRadius: BorderRadius.circular(12),
      child: chip,
    );
  }

  void _showWakeList(BuildContext context, List<dynamic> wakes) {
    showDialog<void>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Scheduled wakes'),
        content: SizedBox(
          width: 480,
          child: ListView.separated(
            shrinkWrap: true,
            itemCount: wakes.length,
            separatorBuilder: (_, _) => const Divider(height: 1),
            itemBuilder: (_, i) {
              final w = wakes[i] as Map<String, dynamic>;
              final reason = (w['reason'] as String?) ?? '';
              final prompt = (w['prompt'] as String?) ?? '';
              final fireAtRaw = w['fire_at'] as String?;
              final fireAt = fireAtRaw != null
                  ? DateTime.tryParse(fireAtRaw)?.toLocal()
                  : null;
              return ListTile(
                title: Text(reason.isEmpty ? 'Wake' : reason),
                subtitle: Text(
                  '${fireAt != null ? _formatFireAt(fireAt) : '?'}\n$prompt',
                  maxLines: 4,
                  overflow: TextOverflow.ellipsis,
                ),
              );
            },
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: const Text('Close'),
          ),
        ],
      ),
    );
  }

  String _formatFireAt(DateTime fireAt) {
    final now = DateTime.now();
    final diff = fireAt.difference(now);
    if (diff.isNegative) return 'fires now';
    if (diff.inMinutes < 1) return 'fires in ${diff.inSeconds}s';
    if (diff.inMinutes < 60) return 'fires in ${diff.inMinutes}m';
    return 'fires at ${fireAt.hour.toString().padLeft(2, '0')}:'
        '${fireAt.minute.toString().padLeft(2, '0')}';
  }
}
