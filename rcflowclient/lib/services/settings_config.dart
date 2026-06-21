/// Typed configuration value objects for each settings domain.
///
/// Each config is an immutable value class with a [copyWith] method.
/// [SettingsService] exposes typed load/save methods using these classes,
/// replacing ad-hoc direct getter/setter access for cohesive setting groups.
///
/// Migration path: new code writes to typed configs; old code continues using
/// individual getters/setters until migration is complete.
library;

// ---------------------------------------------------------------------------
// AppearanceConfig
// ---------------------------------------------------------------------------

class AppearanceConfig {
  final String themeMode;
  final String fontSize;
  final bool compactMode;

  const AppearanceConfig({
    this.themeMode = 'dark',
    this.fontSize = 'medium',
    this.compactMode = false,
  });

  AppearanceConfig copyWith({
    String? themeMode,
    String? fontSize,
    bool? compactMode,
  }) => AppearanceConfig(
    themeMode: themeMode ?? this.themeMode,
    fontSize: fontSize ?? this.fontSize,
    compactMode: compactMode ?? this.compactMode,
  );

  @override
  bool operator ==(Object other) =>
      other is AppearanceConfig &&
      other.themeMode == themeMode &&
      other.fontSize == fontSize &&
      other.compactMode == compactMode;

  @override
  int get hashCode => Object.hash(themeMode, fontSize, compactMode);
}

// ---------------------------------------------------------------------------
// SoundConfig
// ---------------------------------------------------------------------------

class SoundConfig {
  /// Play a sound on each new assistant message ("Sound on message").
  final bool enabled;

  /// Play the completion sound when the agent finishes a turn ("Sound when
  /// done").
  final bool soundOnComplete;

  /// Vibrate on mobile when a message arrives.
  final bool vibrateEnabled;

  /// Completion sound: named preset (e.g. 'gentle_chime') or 'custom'.
  final String completionSound;

  /// Absolute path to the completion custom audio file, or empty.
  final String completionCustomSoundPath;

  /// Per-message sound: named preset or 'custom'.
  final String messageSound;

  /// Absolute path to the per-message custom audio file, or empty.
  final String messageCustomSoundPath;

  const SoundConfig({
    this.enabled = false,
    this.soundOnComplete = true,
    this.vibrateEnabled = true,
    this.completionSound = 'gentle_chime',
    this.completionCustomSoundPath = '',
    this.messageSound = 'soft_ping',
    this.messageCustomSoundPath = '',
  });

  SoundConfig copyWith({
    bool? enabled,
    bool? soundOnComplete,
    bool? vibrateEnabled,
    String? completionSound,
    String? completionCustomSoundPath,
    String? messageSound,
    String? messageCustomSoundPath,
  }) => SoundConfig(
    enabled: enabled ?? this.enabled,
    soundOnComplete: soundOnComplete ?? this.soundOnComplete,
    vibrateEnabled: vibrateEnabled ?? this.vibrateEnabled,
    completionSound: completionSound ?? this.completionSound,
    completionCustomSoundPath:
        completionCustomSoundPath ?? this.completionCustomSoundPath,
    messageSound: messageSound ?? this.messageSound,
    messageCustomSoundPath:
        messageCustomSoundPath ?? this.messageCustomSoundPath,
  );

  @override
  bool operator ==(Object other) =>
      other is SoundConfig &&
      other.enabled == enabled &&
      other.soundOnComplete == soundOnComplete &&
      other.vibrateEnabled == vibrateEnabled &&
      other.completionSound == completionSound &&
      other.completionCustomSoundPath == completionCustomSoundPath &&
      other.messageSound == messageSound &&
      other.messageCustomSoundPath == messageCustomSoundPath;

  @override
  int get hashCode => Object.hash(
    enabled,
    soundOnComplete,
    vibrateEnabled,
    completionSound,
    completionCustomSoundPath,
    messageSound,
    messageCustomSoundPath,
  );
}

// ---------------------------------------------------------------------------
// ToastConfig
// ---------------------------------------------------------------------------

class ToastConfig {
  /// Master switch — when false, no toasts are shown regardless of sub-flags.
  final bool enabled;

  /// Show toasts for background session activity (awaiting input, errors, etc.)
  final bool backgroundSessions;

  /// Show toasts for task status changes (created, updated, failed).
  final bool tasks;

  /// Show toasts for worker connection events (lost, reconnected, failed).
  final bool connections;

  const ToastConfig({
    this.enabled = true,
    this.backgroundSessions = true,
    this.tasks = true,
    this.connections = true,
  });

  ToastConfig copyWith({
    bool? enabled,
    bool? backgroundSessions,
    bool? tasks,
    bool? connections,
  }) => ToastConfig(
    enabled: enabled ?? this.enabled,
    backgroundSessions: backgroundSessions ?? this.backgroundSessions,
    tasks: tasks ?? this.tasks,
    connections: connections ?? this.connections,
  );

  @override
  bool operator ==(Object other) =>
      other is ToastConfig &&
      other.enabled == enabled &&
      other.backgroundSessions == backgroundSessions &&
      other.tasks == tasks &&
      other.connections == connections;

  @override
  int get hashCode =>
      Object.hash(enabled, backgroundSessions, tasks, connections);
}

// ---------------------------------------------------------------------------
// TerminalConfig
// ---------------------------------------------------------------------------

class TerminalConfig {
  final int scrollback;
  final String colorScheme;
  final String cursorStyle;
  final double fontSize;
  final String fontFamily;

  const TerminalConfig({
    this.scrollback = 10000,
    this.colorScheme = 'default',
    this.cursorStyle = 'block',
    this.fontSize = 14.0,
    this.fontFamily = '',
  });

  TerminalConfig copyWith({
    int? scrollback,
    String? colorScheme,
    String? cursorStyle,
    double? fontSize,
    String? fontFamily,
  }) => TerminalConfig(
    scrollback: scrollback ?? this.scrollback,
    colorScheme: colorScheme ?? this.colorScheme,
    cursorStyle: cursorStyle ?? this.cursorStyle,
    fontSize: fontSize ?? this.fontSize,
    fontFamily: fontFamily ?? this.fontFamily,
  );

  @override
  bool operator ==(Object other) =>
      other is TerminalConfig &&
      other.scrollback == scrollback &&
      other.colorScheme == colorScheme &&
      other.cursorStyle == cursorStyle &&
      other.fontSize == fontSize &&
      other.fontFamily == fontFamily;

  @override
  int get hashCode =>
      Object.hash(scrollback, colorScheme, cursorStyle, fontSize, fontFamily);
}
