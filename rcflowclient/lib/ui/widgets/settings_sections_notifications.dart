part of 'settings_menu.dart';

class _NotificationsSection extends StatefulWidget {
  const _NotificationsSection();

  @override
  State<_NotificationsSection> createState() => _NotificationsSectionState();
}

class _NotificationsSectionState extends State<_NotificationsSection> {
  late bool _soundEnabled;
  late bool _soundOnCompleteEnabled;
  late bool _vibrateEnabled;

  // Completion ("Sound when done") sound.
  late String _completionSound;
  late String _completionCustomPath;
  String? _completionError;

  // Per-message ("Sound on message") sound.
  late String _messageSound;
  late String _messageCustomPath;
  String? _messageError;

  NotificationSoundService? _soundService;
  late bool _toastEnabled;
  late bool _toastBackgroundSessions;
  late bool _toastTasks;
  late bool _toastConnections;

  @override
  void initState() {
    super.initState();
    final appState = context.read<AppState>();
    final settings = appState.settings;
    _soundEnabled = settings.soundEnabled;
    _soundOnCompleteEnabled = settings.soundOnCompleteEnabled;
    _vibrateEnabled = settings.vibrateEnabled;
    _completionSound = settings.completionSound;
    _completionCustomPath = settings.completionCustomSoundPath;
    _messageSound = settings.messageSound;
    _messageCustomPath = settings.messageCustomSoundPath;
    _soundService = appState.soundService;
    _toastEnabled = settings.toastEnabled;
    _toastBackgroundSessions = settings.toastBackgroundSessions;
    _toastTasks = settings.toastTasks;
    _toastConnections = settings.toastConnections;
  }

  String _fileName(String path) {
    if (path.isEmpty) return '';
    final sep = Platform.isWindows ? '\\' : '/';
    return path.split(sep).last;
  }

  /// A settings toggle where only the switch is interactive (no full-row hit
  /// target / hover highlight).
  Widget _switchRow({
    required String title,
    required String subtitle,
    required bool value,
    required ValueChanged<bool> onChanged,
  }) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Expanded(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  title,
                  style: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 15,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  subtitle,
                  style: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 12,
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: 12),
          Switch(
            value: value,
            activeTrackColor: context.appColors.accent,
            onChanged: onChanged,
          ),
        ],
      ),
    );
  }

  /// Opens a file picker, validates the WAV, and applies it to the target slot
  /// (completion when [forCompletion], otherwise the per-message slot).
  Future<void> _pickCustomSound(
    SettingsService settings, {
    required bool forCompletion,
  }) async {
    final result = await FilePicker.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['wav'],
      dialogTitle: 'Select notification sound',
    );
    if (result == null || result.files.isEmpty) return;
    final path = result.files.single.path;
    if (path == null) return;

    final error = await _soundService?.validateCustomSound(path);
    if (error != null) {
      setState(() {
        if (forCompletion) {
          _completionError = error;
        } else {
          _messageError = error;
        }
      });
      return;
    }

    setState(() {
      if (forCompletion) {
        _completionCustomPath = path;
        _completionSound = 'custom';
        _completionError = null;
      } else {
        _messageCustomPath = path;
        _messageSound = 'custom';
        _messageError = null;
      }
    });
    if (forCompletion) {
      settings.completionCustomSoundPath = path;
      settings.completionSound = 'custom';
    } else {
      settings.messageCustomSoundPath = path;
      settings.messageSound = 'custom';
    }
  }

  @override
  Widget build(BuildContext context) {
    final settings = context.read<AppState>().settings;

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SectionHeader(
          title: 'Notifications',
          icon: Icons.notifications_outlined,
        ),

        // --- Sound when done ---
        _soundBlock(
          settings: settings,
          forCompletion: true,
          title: 'Sound when done',
          subtitle: 'Play a sound when the agent finishes and waits for input',
          enabled: _soundOnCompleteEnabled,
          onToggle: (v) {
            setState(() => _soundOnCompleteEnabled = v);
            settings.soundOnCompleteEnabled = v;
          },
          selectedSound: _completionSound,
          customPath: _completionCustomPath,
          error: _completionError,
          onSelectPreset: (id) {
            setState(() => _completionSound = id);
            settings.completionSound = id;
          },
        ),

        // --- Sound on message ---
        _soundBlock(
          settings: settings,
          forCompletion: false,
          title: 'Sound on message',
          subtitle: 'Play a sound for each new message from the agent',
          enabled: _soundEnabled,
          onToggle: (v) {
            setState(() => _soundEnabled = v);
            settings.soundEnabled = v;
          },
          selectedSound: _messageSound,
          customPath: _messageCustomPath,
          error: _messageError,
          onSelectPreset: (id) {
            setState(() => _messageSound = id);
            settings.messageSound = id;
          },
        ),

        if (Platform.isAndroid || Platform.isIOS)
          _switchRow(
            title: 'Vibrate on message',
            subtitle: 'Vibrate when a message arrives',
            value: _vibrateEnabled,
            onChanged: (v) {
              setState(() => _vibrateEnabled = v);
              settings.vibrateEnabled = v;
            },
          ),

        const SizedBox(height: 20),
        Text(
          'Toast Notifications',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 13,
          ),
        ),
        const SizedBox(height: 4),
        _switchRow(
          title: 'Enable toast notifications',
          subtitle: 'Show popup alerts for important events',
          value: _toastEnabled,
          onChanged: (v) {
            setState(() => _toastEnabled = v);
            settings.toastEnabled = v;
          },
        ),
        if (_toastEnabled) ...[
          _switchRow(
            title: 'Background session alerts',
            subtitle: 'Sessions waiting for input, errors, completions',
            value: _toastBackgroundSessions,
            onChanged: (v) {
              setState(() => _toastBackgroundSessions = v);
              settings.toastBackgroundSessions = v;
            },
          ),
          _switchRow(
            title: 'Task updates',
            subtitle: 'Task created or status changed',
            value: _toastTasks,
            onChanged: (v) {
              setState(() => _toastTasks = v);
              settings.toastTasks = v;
            },
          ),
          _switchRow(
            title: 'Connection alerts',
            subtitle: 'Worker connect/disconnect/reconnect events',
            value: _toastConnections,
            onChanged: (v) {
              setState(() => _toastConnections = v);
              settings.toastConnections = v;
            },
          ),
        ],
      ],
    );
  }

  /// A switch row plus, when enabled, an inline sound picker (dropdown +
  /// preview, and a browse button when a custom sound is selected on Windows).
  Widget _soundBlock({
    required SettingsService settings,
    required bool forCompletion,
    required String title,
    required String subtitle,
    required bool enabled,
    required ValueChanged<bool> onToggle,
    required String selectedSound,
    required String customPath,
    required String? error,
    required ValueChanged<String> onSelectPreset,
  }) {
    final supportsCustom = Platform.isWindows;
    final ids = [
      for (final s in defaultSounds) s.id,
      if (supportsCustom) 'custom',
    ];
    final value =
        ids.contains(selectedSound) ? selectedSound : defaultSounds.first.id;

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _switchRow(
          title: title,
          subtitle: subtitle,
          value: enabled,
          onChanged: onToggle,
        ),
        if (enabled) ...[
          Padding(
            padding: const EdgeInsets.only(bottom: 4),
            child: Row(
              children: [
                Expanded(
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: kSpace3),
                    decoration: BoxDecoration(
                      color: context.appColors.bgElevated,
                      borderRadius: BorderRadius.circular(kRadiusLarge),
                    ),
                    child: DropdownButtonHideUnderline(
                      child: DropdownButton<String>(
                        value: value,
                        isExpanded: true,
                        dropdownColor: context.appColors.bgElevated,
                        borderRadius: BorderRadius.circular(kRadiusLarge),
                        style: TextStyle(
                          color: context.appColors.textPrimary,
                          fontSize: 14,
                        ),
                        items: [
                          for (final s in defaultSounds)
                            DropdownMenuItem(
                              value: s.id,
                              child: Text(s.label),
                            ),
                          if (supportsCustom)
                            DropdownMenuItem(
                              value: 'custom',
                              child: Text(
                                customPath.isNotEmpty
                                    ? 'Custom: ${_fileName(customPath)}'
                                    : 'Custom sound…',
                              ),
                            ),
                        ],
                        onChanged: (v) {
                          if (v == null) return;
                          if (v == 'custom') {
                            _pickCustomSound(
                              settings,
                              forCompletion: forCompletion,
                            );
                          } else {
                            onSelectPreset(v);
                          }
                        },
                      ),
                    ),
                  ),
                ),
                if (supportsCustom && value == 'custom')
                  IconButton(
                    icon: Icon(
                      Icons.folder_open,
                      color: context.appColors.textMuted,
                      size: 20,
                    ),
                    tooltip: 'Browse…',
                    onPressed: () => _pickCustomSound(
                      settings,
                      forCompletion: forCompletion,
                    ),
                  ),
                IconButton(
                  icon: Icon(
                    Icons.play_arrow_rounded,
                    color: context.appColors.accentLight,
                    size: 24,
                  ),
                  tooltip: 'Preview',
                  onPressed: (value == 'custom' && customPath.isEmpty)
                      ? null
                      : () => _soundService?.previewSound(
                          value,
                          customPath: customPath,
                        ),
                ),
              ],
            ),
          ),
          if (error != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Text(
                error,
                style: TextStyle(
                  color: context.appColors.errorText,
                  fontSize: 12,
                ),
              ),
            ),
        ],
      ],
    );
  }
}
