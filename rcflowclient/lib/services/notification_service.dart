import 'dart:async';

import 'package:flutter/foundation.dart';

import '../models/app_notification.dart';

class NotificationService extends ChangeNotifier {
  static const _maxVisible = 5;

  final List<AppNotification> _notifications = [];
  final Map<String, Timer> _timers = {};

  List<AppNotification> get notifications => List.unmodifiable(_notifications);

  void show({
    required NotificationLevel level,
    required String title,
    String? body,
    String? actionLabel,
    VoidCallback? onAction,
    Duration duration = const Duration(seconds: 5),
  }) {
    final notification = AppNotification(
      level: level,
      title: title,
      body: body,
      actionLabel: actionLabel,
      onAction: onAction,
      duration: duration,
    );

    _notifications.add(notification);

    // Evict oldest non-error if over limit
    while (_notifications.length > _maxVisible) {
      final idx = _notifications.indexWhere(
        (n) => n.level != NotificationLevel.error,
      );
      if (idx >= 0) {
        _removeAt(idx);
      } else {
        _removeAt(0);
      }
    }

    // Auto-dismiss timer
    _timers[notification.id] = Timer(duration, () {
      dismiss(notification.id);
    });

    notifyListeners();
  }

  void dismiss(String id) {
    final idx = _notifications.indexWhere((n) => n.id == id);
    if (idx < 0) return;
    _removeAt(idx);
    notifyListeners();
  }

  void _removeAt(int idx) {
    final n = _notifications.removeAt(idx);
    _timers.remove(n.id)?.cancel();
  }

  @override
  void dispose() {
    for (final timer in _timers.values) {
      timer.cancel();
    }
    _timers.clear();
    _notifications.clear();
    super.dispose();
  }
}
