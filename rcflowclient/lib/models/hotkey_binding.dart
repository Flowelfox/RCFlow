import 'package:flutter/services.dart';

/// Identifies a bindable action in the app.
enum HotkeyAction {
  closePane,
  newSession,
  openSettings,
  focusPaneLeft,
  focusPaneRight,
  focusPaneUp,
  focusPaneDown,
  toggleSidebar,
  focusInputArea,
  nextPane,
  previousPane,
  splitRight,
  splitDown,
  openTerminal,
  refreshSessions,
  reopenLastClosedPane,
}

/// Human-readable label for a [HotkeyAction].
String hotkeyActionLabel(HotkeyAction action) => switch (action) {
  HotkeyAction.closePane => 'Close Pane',
  HotkeyAction.newSession => 'New Session',
  HotkeyAction.openSettings => 'Open Settings',
  HotkeyAction.focusPaneLeft => 'Focus Left Pane',
  HotkeyAction.focusPaneRight => 'Focus Right Pane',
  HotkeyAction.focusPaneUp => 'Focus Up Pane',
  HotkeyAction.focusPaneDown => 'Focus Down Pane',
  HotkeyAction.toggleSidebar => 'Toggle Sidebar',
  HotkeyAction.focusInputArea => 'Focus Input',
  HotkeyAction.nextPane => 'Next Pane',
  HotkeyAction.previousPane => 'Previous Pane',
  HotkeyAction.splitRight => 'Split Right',
  HotkeyAction.splitDown => 'Split Down',
  HotkeyAction.openTerminal => 'Open Terminal',
  HotkeyAction.refreshSessions => 'Refresh Sessions',
  HotkeyAction.reopenLastClosedPane => 'Reopen Closed Pane',
};

/// Grouped actions for display in settings.
const hotkeyActionGroups = {
  'Pane Management': [
    HotkeyAction.closePane,
    HotkeyAction.reopenLastClosedPane,
    HotkeyAction.splitRight,
    HotkeyAction.splitDown,
  ],
  'Navigation': [
    HotkeyAction.focusPaneLeft,
    HotkeyAction.focusPaneRight,
    HotkeyAction.focusPaneUp,
    HotkeyAction.focusPaneDown,
    HotkeyAction.nextPane,
    HotkeyAction.previousPane,
  ],
  'Sessions': [
    HotkeyAction.newSession,
    HotkeyAction.openTerminal,
    HotkeyAction.refreshSessions,
  ],
  'App': [
    HotkeyAction.openSettings,
    HotkeyAction.toggleSidebar,
    HotkeyAction.focusInputArea,
  ],
};

/// A single hotkey binding: modifier flags + a logical key.
class HotkeyBinding {
  final HotkeyAction action;
  final bool ctrl;
  final bool alt;
  final bool shift;
  final bool meta;
  final LogicalKeyboardKey key;

  const HotkeyBinding({
    required this.action,
    this.ctrl = false,
    this.alt = false,
    this.shift = false,
    this.meta = false,
    required this.key,
  });

  Map<String, dynamic> toJson() => {
    'action': action.name,
    'ctrl': ctrl,
    'alt': alt,
    'shift': shift,
    'meta': meta,
    'keyId': key.keyId,
    'keyLabel': key.keyLabel,
  };

  factory HotkeyBinding.fromJson(Map<String, dynamic> json) {
    return HotkeyBinding(
      action: HotkeyAction.values.byName(json['action'] as String),
      ctrl: json['ctrl'] as bool? ?? false,
      alt: json['alt'] as bool? ?? false,
      shift: json['shift'] as bool? ?? false,
      meta: json['meta'] as bool? ?? false,
      key: LogicalKeyboardKey(json['keyId'] as int),
    );
  }

  /// Human-readable label like "Ctrl+W" or "Alt+Left".
  String get label {
    final parts = <String>[];
    if (ctrl) parts.add('Ctrl');
    if (alt) parts.add('Alt');
    if (shift) parts.add('Shift');
    if (meta) parts.add('Win');
    parts.add(_keyLabel(key));
    return parts.join('+');
  }

  static String _keyLabel(LogicalKeyboardKey k) {
    // Provide nicer labels for common keys
    if (k == LogicalKeyboardKey.arrowLeft) return 'Left';
    if (k == LogicalKeyboardKey.arrowRight) return 'Right';
    if (k == LogicalKeyboardKey.arrowUp) return 'Up';
    if (k == LogicalKeyboardKey.arrowDown) return 'Down';
    if (k == LogicalKeyboardKey.escape) return 'Esc';
    if (k == LogicalKeyboardKey.backquote) return '`';
    if (k == LogicalKeyboardKey.backslash) return '\\';
    if (k == LogicalKeyboardKey.tab) return 'Tab';
    if (k == LogicalKeyboardKey.f5) return 'F5';
    return k.keyLabel;
  }

  /// Check if a KeyEvent matches this binding.
  bool matches(KeyEvent event, Set<LogicalKeyboardKey> keysPressed) {
    if (event.logicalKey != key) return false;
    final isCtrl =
        keysPressed.contains(LogicalKeyboardKey.controlLeft) ||
        keysPressed.contains(LogicalKeyboardKey.controlRight);
    final isAlt =
        keysPressed.contains(LogicalKeyboardKey.altLeft) ||
        keysPressed.contains(LogicalKeyboardKey.altRight);
    final isShift =
        keysPressed.contains(LogicalKeyboardKey.shiftLeft) ||
        keysPressed.contains(LogicalKeyboardKey.shiftRight);
    final isMeta =
        keysPressed.contains(LogicalKeyboardKey.metaLeft) ||
        keysPressed.contains(LogicalKeyboardKey.metaRight);
    return ctrl == isCtrl && alt == isAlt && shift == isShift && meta == isMeta;
  }

  /// Two bindings conflict if they produce the same key combination.
  bool conflictsWith(HotkeyBinding other) {
    return ctrl == other.ctrl &&
        alt == other.alt &&
        shift == other.shift &&
        meta == other.meta &&
        key == other.key;
  }
}
