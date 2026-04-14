import 'package:flutter/material.dart';

import '../../models/badge_spec.dart';

/// A function that builds the visual chip widget for a [BadgeSpec].
typedef BadgeRenderer = Widget Function(BuildContext context, BadgeSpec badge);

/// Singleton registry that maps [BadgeSpec.type] strings to [BadgeRenderer]
/// functions.
///
/// Register renderers once at app startup (see ``_registerBadges`` in
/// ``main.dart``).  Unknown badge types are rendered as a generic grey chip so
/// the app never crashes on new badge types introduced by a newer server.
class BadgeRegistry {
  BadgeRegistry._();

  /// The application-wide singleton instance.
  static final BadgeRegistry instance = BadgeRegistry._();

  final Map<String, BadgeRenderer> _renderers = {};

  /// Register a renderer for [type].  Overwrites any previous registration.
  void register(String type, BadgeRenderer renderer) {
    _renderers[type] = renderer;
  }

  /// Render [badge] using its registered renderer, or fall back to a generic
  /// grey chip for unknown types.
  Widget render(BuildContext context, BadgeSpec badge) {
    final renderer = _renderers[badge.type];
    if (renderer == null) {
      return _GenericBadge(label: badge.label);
    }
    return renderer(context, badge);
  }
}

// ---------------------------------------------------------------------------
// Generic fallback chip
// ---------------------------------------------------------------------------

class _GenericBadge extends StatelessWidget {
  final String label;

  const _GenericBadge({required this.label});

  static const _color = Color(0xFF6B7280); // grey-500

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: _color.withAlpha(25),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: _color.withAlpha(70), width: 0.5),
      ),
      child: Text(
        label,
        style: const TextStyle(
          color: _color,
          fontSize: 10,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}
