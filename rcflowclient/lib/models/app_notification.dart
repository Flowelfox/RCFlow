import 'dart:math';

import 'package:flutter/foundation.dart';

enum NotificationLevel { info, warning, error, success }

class AppNotification {
  final String id;
  final NotificationLevel level;
  final String title;
  final String? body;
  final String? actionLabel;
  final VoidCallback? onAction;
  final DateTime createdAt;
  final Duration duration;

  AppNotification({
    String? id,
    required this.level,
    required this.title,
    this.body,
    this.actionLabel,
    this.onAction,
    DateTime? createdAt,
    this.duration = const Duration(seconds: 5),
  })  : id = id ?? _generateId(),
        createdAt = createdAt ?? DateTime.now();

  static String _generateId() {
    final rng = Random.secure();
    final bytes = List.generate(8, (_) => rng.nextInt(256));
    return bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
  }
}
