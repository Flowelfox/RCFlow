import 'package:flutter/material.dart';

import '../../../models/session_info.dart';
import '../../../theme.dart';
import 'spinning_icon.dart';

class SessionLeadingIcon extends StatelessWidget {
  final SessionInfo session;

  const SessionLeadingIcon({super.key, required this.session});

  @override
  Widget build(BuildContext context) {
    final icon = _sessionIcon(session);
    final color = _sessionIconColor(context, session);
    final bgColor = _sessionIconBg(context, session);
    final isAwaitingPermission =
        session.activityState == 'awaiting_permission' &&
            (session.status == 'active' || session.status == 'executing');
    final isProcessing = session.isProcessing &&
        !isAwaitingPermission &&
        (session.status == 'active' || session.status == 'executing');

    return Container(
      width: 30,
      height: 30,
      decoration: BoxDecoration(
        color: bgColor,
        borderRadius: BorderRadius.circular(8),
      ),
      child: isProcessing
          ? SpinningIcon(icon: icon, color: color, size: 16)
          : Icon(icon, color: color, size: 16),
    );
  }

  static IconData _sessionIcon(SessionInfo s) {
    if (s.activityState == 'awaiting_permission' &&
        (s.status == 'active' || s.status == 'executing')) {
      return Icons.shield_outlined;
    }
    if ((s.status == 'active' || s.status == 'executing') &&
        !s.isProcessing) {
      return Icons.chat_bubble_outline_rounded;
    }
    return switch (s.status) {
      'active' || 'executing' => Icons.sync_rounded,
      'paused' => Icons.pause_circle_outline_rounded,
      'completed' => Icons.check_circle_outline_rounded,
      'failed' => Icons.cancel_outlined,
      'cancelled' => Icons.stop_circle_outlined,
      _ => Icons.circle_outlined,
    };
  }

  static Color _sessionIconColor(BuildContext context, SessionInfo s) {
    if (s.activityState == 'awaiting_permission' &&
        (s.status == 'active' || s.status == 'executing')) {
      return context.appColors.toolAccent;
    }
    if ((s.status == 'active' || s.status == 'executing') &&
        !s.isProcessing) {
      return context.appColors.accentLight;
    }
    return switch (s.status) {
      'active' || 'executing' => context.appColors.toolAccent,
      'paused' => context.appColors.accentLight,
      'completed' => context.appColors.successText,
      'failed' => context.appColors.errorText,
      'cancelled' => context.appColors.textMuted,
      _ => context.appColors.textMuted,
    };
  }

  static Color _sessionIconBg(BuildContext context, SessionInfo s) {
    if (s.activityState == 'awaiting_permission' &&
        (s.status == 'active' || s.status == 'executing')) {
      return Color(0xFF2A2000);
    }
    if ((s.status == 'active' || s.status == 'executing') &&
        !s.isProcessing) {
      return Color(0xFF112233);
    }
    return switch (s.status) {
      'active' || 'executing' => Color(0xFF2A2311),
      'paused' => Color(0xFF112233),
      'completed' => context.appColors.successBg,
      'failed' => context.appColors.errorBg,
      'cancelled' => context.appColors.bgElevated,
      _ => context.appColors.bgElevated,
    };
  }
}
