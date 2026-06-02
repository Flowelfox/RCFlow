import '../models/app_notification.dart';
import '../services/notification_service.dart';
import '../services/settings_service.dart';

/// Category of a toast, used to gate display against the per-category
/// user settings.
enum ToastCategory { connection, task, session }

/// Settings-gated wrapper around [NotificationService] for in-app toasts,
/// owned by [AppState].  Drops the toast when toasts are disabled globally
/// or for its [ToastCategory]; otherwise forwards to the notification service.
/// Extracted from AppState in the Phase 5 step-3 carve.
class ToastNotifier {
  ToastNotifier(this._settings, this._notifications);

  final SettingsService _settings;
  final NotificationService _notifications;

  void show({
    required NotificationLevel level,
    required String title,
    String? body,
    required ToastCategory category,
    String? actionLabel,
    void Function()? onAction,
  }) {
    if (!_settings.toastEnabled) return;
    switch (category) {
      case ToastCategory.connection:
        if (!_settings.toastConnections) return;
      case ToastCategory.task:
        if (!_settings.toastTasks) return;
      case ToastCategory.session:
        if (!_settings.toastBackgroundSessions) return;
    }
    _notifications.show(
      level: level,
      title: title,
      body: body,
      actionLabel: actionLabel,
      onAction: onAction,
    );
  }
}
