import 'package:flutter/foundation.dart';

/// Canonical priority constants matching the server-side ``BadgePriority`` class.
///
/// Lower values appear earlier (leftmost) in the badge bar.
abstract final class BadgePriority {
  static const int status = 0;
  static const int worker = 10;
  static const int agent = 20;
  static const int project = 30;
  static const int worktree = 40;
  static const int caveman = 50;
}

/// Serialisable value object describing a single session badge.
///
/// Received inside ``session_update`` WebSocket messages (``badges`` array)
/// and used by [BadgeBar] + [BadgeRegistry] to render each chip.
@immutable
class BadgeSpec {
  /// Stable type identifier (e.g. ``"status"``, ``"worker"``, ``"caveman"``).
  final String type;

  /// Human-readable text displayed on the chip.
  final String label;

  /// Sort position within the badge bar; lower values appear first.
  final int priority;

  /// Whether the client should display this badge.
  final bool visible;

  /// Whether tapping the badge triggers a client-side action.
  final bool interactive;

  /// Type-specific data; opaque on the wire, interpreted by the renderer.
  final Map<String, dynamic> payload;

  const BadgeSpec({
    required this.type,
    required this.label,
    required this.priority,
    required this.visible,
    required this.interactive,
    this.payload = const {},
  });

  factory BadgeSpec.fromJson(Map<String, dynamic> json) => BadgeSpec(
        type: json['type'] as String? ?? '',
        label: json['label'] as String? ?? '',
        priority: (json['priority'] as num?)?.toInt() ?? 0,
        visible: json['visible'] as bool? ?? true,
        interactive: json['interactive'] as bool? ?? false,
        payload: (json['payload'] as Map<String, dynamic>?) ?? const {},
      );

  Map<String, dynamic> toJson() => {
        'type': type,
        'label': label,
        'priority': priority,
        'visible': visible,
        'interactive': interactive,
        'payload': payload,
      };

  /// Parse a JSON array of badge specs, returning an empty list for null input.
  static List<BadgeSpec> listFromJson(List<dynamic>? list) =>
      list
          ?.map((e) => BadgeSpec.fromJson(e as Map<String, dynamic>))
          .toList() ??
      [];

  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      other is BadgeSpec &&
          runtimeType == other.runtimeType &&
          type == other.type &&
          label == other.label &&
          priority == other.priority &&
          visible == other.visible &&
          interactive == other.interactive;

  @override
  int get hashCode =>
      Object.hash(type, label, priority, visible, interactive);

  @override
  String toString() =>
      'BadgeSpec(type: $type, label: $label, priority: $priority, '
      'visible: $visible, interactive: $interactive)';
}
