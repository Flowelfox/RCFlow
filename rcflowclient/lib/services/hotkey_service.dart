import 'dart:convert';

import 'package:flutter/services.dart';

import '../models/hotkey_binding.dart';
import 'settings_service.dart';

class HotkeyService {
  final SettingsService _settings;
  late List<HotkeyBinding> _bindings;

  static const _defaults = <HotkeyBinding>[
    HotkeyBinding(
        action: HotkeyAction.closePane,
        ctrl: true,
        key: LogicalKeyboardKey.keyW),
    HotkeyBinding(
        action: HotkeyAction.newSession,
        ctrl: true,
        key: LogicalKeyboardKey.keyT),
    HotkeyBinding(
        action: HotkeyAction.openSettings,
        ctrl: true,
        alt: true,
        key: LogicalKeyboardKey.keyS),
    HotkeyBinding(
        action: HotkeyAction.focusPaneLeft,
        alt: true,
        key: LogicalKeyboardKey.arrowLeft),
    HotkeyBinding(
        action: HotkeyAction.focusPaneRight,
        alt: true,
        key: LogicalKeyboardKey.arrowRight),
    HotkeyBinding(
        action: HotkeyAction.focusPaneUp,
        alt: true,
        key: LogicalKeyboardKey.arrowUp),
    HotkeyBinding(
        action: HotkeyAction.focusPaneDown,
        alt: true,
        key: LogicalKeyboardKey.arrowDown),
    HotkeyBinding(
        action: HotkeyAction.toggleSidebar,
        ctrl: true,
        key: LogicalKeyboardKey.keyB),
    HotkeyBinding(
        action: HotkeyAction.focusInputArea,
        key: LogicalKeyboardKey.escape),
    HotkeyBinding(
        action: HotkeyAction.nextPane,
        ctrl: true,
        key: LogicalKeyboardKey.tab),
    HotkeyBinding(
        action: HotkeyAction.previousPane,
        ctrl: true,
        shift: true,
        key: LogicalKeyboardKey.tab),
    HotkeyBinding(
        action: HotkeyAction.splitRight,
        ctrl: true,
        key: LogicalKeyboardKey.backslash),
    HotkeyBinding(
        action: HotkeyAction.splitDown,
        ctrl: true,
        shift: true,
        key: LogicalKeyboardKey.backslash),
    HotkeyBinding(
        action: HotkeyAction.openTerminal,
        ctrl: true,
        key: LogicalKeyboardKey.backquote),
    HotkeyBinding(
        action: HotkeyAction.refreshSessions, key: LogicalKeyboardKey.f5),
    HotkeyBinding(
        action: HotkeyAction.reopenLastClosedPane,
        ctrl: true,
        shift: true,
        key: LogicalKeyboardKey.keyT),
  ];

  HotkeyService({required SettingsService settings}) : _settings = settings {
    _bindings = _loadBindings();
  }

  List<HotkeyBinding> get bindings => List.unmodifiable(_bindings);

  HotkeyBinding? bindingFor(HotkeyAction action) {
    for (final b in _bindings) {
      if (b.action == action) return b;
    }
    return null;
  }

  /// Find which action (if any) matches the current key event.
  HotkeyAction? match(KeyEvent event, Set<LogicalKeyboardKey> keysPressed) {
    for (final b in _bindings) {
      if (b.matches(event, keysPressed)) return b.action;
    }
    return null;
  }

  /// Returns the default binding for a given action.
  HotkeyBinding defaultBindingFor(HotkeyAction action) {
    return _defaults.firstWhere((b) => b.action == action);
  }

  /// Update a single binding. Returns a conflicting binding if one exists,
  /// or null on success.
  HotkeyBinding? updateBinding(HotkeyBinding newBinding) {
    for (final existing in _bindings) {
      if (existing.action != newBinding.action &&
          existing.conflictsWith(newBinding)) {
        return existing;
      }
    }
    _bindings.removeWhere((b) => b.action == newBinding.action);
    _bindings.add(newBinding);
    _saveBindings();
    return null;
  }

  void resetBinding(HotkeyAction action) {
    _bindings.removeWhere((b) => b.action == action);
    final def = _defaults.firstWhere((b) => b.action == action);
    _bindings.add(def);
    _saveBindings();
  }

  void resetAllBindings() {
    _bindings = List.of(_defaults);
    _saveBindings();
  }

  List<HotkeyBinding> _loadBindings() {
    final raw = _settings.hotkeyBindings;
    if (raw == null) return List.of(_defaults);
    try {
      final list = jsonDecode(raw) as List<dynamic>;
      final loaded = list
          .map((e) => HotkeyBinding.fromJson(e as Map<String, dynamic>))
          .toList();
      // Merge with defaults: add any new actions that don't exist in saved data
      for (final def in _defaults) {
        if (!loaded.any((b) => b.action == def.action)) {
          loaded.add(def);
        }
      }
      // Remove bindings for actions that no longer exist
      loaded.removeWhere(
          (b) => !HotkeyAction.values.contains(b.action));
      return loaded;
    } catch (_) {
      return List.of(_defaults);
    }
  }

  void _saveBindings() {
    _settings.hotkeyBindings =
        jsonEncode(_bindings.map((b) => b.toJson()).toList());
  }
}
