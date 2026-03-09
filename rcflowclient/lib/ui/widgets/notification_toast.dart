import 'package:flutter/material.dart';

import '../../models/app_notification.dart';
import '../../theme.dart';

class NotificationToast extends StatelessWidget {
  final AppNotification notification;
  final VoidCallback onDismiss;
  final bool compact;

  const NotificationToast({
    super.key,
    required this.notification,
    required this.onDismiss,
    this.compact = false,
  });

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;

    final (Color accentColor, IconData icon) = switch (notification.level) {
      NotificationLevel.info => (colors.accent, Icons.info_outline_rounded),
      NotificationLevel.warning => (colors.toolAccent, Icons.warning_amber_rounded),
      NotificationLevel.error => (colors.errorText, Icons.error_outline_rounded),
      NotificationLevel.success => (colors.successText, Icons.check_circle_outline_rounded),
    };

    final bgColor = switch (notification.level) {
      NotificationLevel.error => colors.errorBg,
      NotificationLevel.success => colors.successBg,
      _ => colors.bgElevated,
    };

    return Semantics(
      liveRegion: true,
      child: Material(
        color: Colors.transparent,
        child: Container(
          width: compact ? null : 360,
          decoration: BoxDecoration(
            color: bgColor,
            borderRadius: BorderRadius.circular(compact ? 8 : 10),
            border: Border(
              left: BorderSide(color: accentColor, width: 3),
            ),
            boxShadow: compact
                ? null
                : [
                    BoxShadow(
                      color: Colors.black.withAlpha(60),
                      blurRadius: 12,
                      offset: const Offset(0, 4),
                    ),
                  ],
          ),
          child: InkWell(
            borderRadius: BorderRadius.circular(compact ? 8 : 10),
            onTap: notification.onAction != null
                ? () {
                    notification.onAction!();
                    onDismiss();
                  }
                : null,
            child: Padding(
              padding: EdgeInsets.symmetric(
                horizontal: compact ? 8 : 12,
                vertical: compact ? 6 : 10,
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Padding(
                    padding: const EdgeInsets.only(top: 1),
                    child: Icon(icon, color: accentColor, size: compact ? 14 : 18),
                  ),
                  SizedBox(width: compact ? 6 : 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        Text(
                          notification.title,
                          style: TextStyle(
                            color: colors.textPrimary,
                            fontSize: compact ? 11 : 13,
                            fontWeight: FontWeight.w600,
                          ),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                        if (notification.body != null) ...[
                          const SizedBox(height: 1),
                          Text(
                            notification.body!,
                            style: TextStyle(
                              color: colors.textSecondary,
                              fontSize: compact ? 10 : 12,
                            ),
                            maxLines: compact ? 1 : 2,
                            overflow: TextOverflow.ellipsis,
                          ),
                        ],
                      ],
                    ),
                  ),
                  const SizedBox(width: 2),
                  SizedBox(
                    width: compact ? 18 : 24,
                    height: compact ? 18 : 24,
                    child: IconButton(
                      padding: EdgeInsets.zero,
                      iconSize: compact ? 12 : 16,
                      icon: Icon(Icons.close, color: colors.textMuted),
                      tooltip: 'Dismiss notification',
                      onPressed: onDismiss,
                      constraints: BoxConstraints(
                        maxWidth: compact ? 18 : 24,
                        maxHeight: compact ? 18 : 24,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
