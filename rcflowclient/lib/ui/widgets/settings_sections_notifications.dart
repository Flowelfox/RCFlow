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
  late String _selectedSound;
  late String _customSoundPath;
  String? _customSoundError;
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
    _selectedSound = settings.notificationSound;
    _customSoundPath = settings.customSoundPath;
    _soundService = appState.soundService;
    _toastEnabled = settings.toastEnabled;
    _toastBackgroundSessions = settings.toastBackgroundSessions;
    _toastTasks = settings.toastTasks;
    _toastConnections = settings.toastConnections;
  }

  bool get _anySoundEnabled => _soundEnabled || _soundOnCompleteEnabled;

  Future<void> _pickCustomSound(SettingsService settings) async {
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
      setState(() => _customSoundError = error);
      return;
    }

    setState(() {
      _customSoundPath = path;
      _customSoundError = null;
      _selectedSound = 'custom';
    });
    settings.customSoundPath = path;
    settings.notificationSound = 'custom';
  }

  String get _customFileName {
    if (_customSoundPath.isEmpty) return '';
    final sep = Platform.isWindows ? '\\' : '/';
    return _customSoundPath.split(sep).last;
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
        SwitchListTile(
          title: Text(
            'Sound when done',
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 15,
            ),
          ),
          subtitle: Text(
            'Play a sound when work finishes and waiting for input',
            style: TextStyle(color: context.appColors.textMuted, fontSize: 12),
          ),
          value: _soundOnCompleteEnabled,
          activeTrackColor: context.appColors.accent,
          contentPadding: EdgeInsets.zero,
          onChanged: (v) {
            setState(() => _soundOnCompleteEnabled = v);
            settings.soundOnCompleteEnabled = v;
          },
        ),
        SwitchListTile(
          title: Text(
            'Sound on message',
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 15,
            ),
          ),
          subtitle: Text(
            'Play a sound when a message arrives',
            style: TextStyle(color: context.appColors.textMuted, fontSize: 12),
          ),
          value: _soundEnabled,
          activeTrackColor: context.appColors.accent,
          contentPadding: EdgeInsets.zero,
          onChanged: (v) {
            setState(() => _soundEnabled = v);
            settings.soundEnabled = v;
          },
        ),
        if (_anySoundEnabled) ...[
          SizedBox(height: 12),
          Text(
            'Notification sound',
            style: TextStyle(
              color: context.appColors.textSecondary,
              fontSize: 13,
            ),
          ),
          SizedBox(height: 8),
          Container(
            decoration: BoxDecoration(
              color: context.appColors.bgElevated,
              borderRadius: BorderRadius.circular(kRadiusLarge),
            ),
            clipBehavior: Clip.antiAlias,
            child: Column(
              children: [
                for (final sound in defaultSounds)
                  _SoundOption(
                    label: sound.label,
                    selected: _selectedSound == sound.id,
                    isLast: false,
                    onTap: () {
                      setState(() => _selectedSound = sound.id);
                      settings.notificationSound = sound.id;
                    },
                    onPreview: () => _soundService?.previewSound(sound.id),
                  ),
                if (Platform.isWindows) ...[
                  _SoundOption(
                    label: _customSoundPath.isNotEmpty
                        ? 'Custom: $_customFileName'
                        : 'Custom sound...',
                    selected: _selectedSound == 'custom',
                    isLast: true,
                    onTap: () {
                      if (_customSoundPath.isNotEmpty) {
                        setState(() => _selectedSound = 'custom');
                        settings.notificationSound = 'custom';
                      } else {
                        _pickCustomSound(settings);
                      }
                    },
                    onPreview: _customSoundPath.isNotEmpty
                        ? () => _soundService?.previewSound('custom')
                        : null,
                    trailing: IconButton(
                      icon: Icon(
                        Icons.folder_open,
                        color: context.appColors.textMuted,
                        size: 20,
                      ),
                      onPressed: () => _pickCustomSound(settings),
                      tooltip: 'Browse...',
                    ),
                  ),
                ],
              ],
            ),
          ),
          if (_customSoundError != null) ...[
            SizedBox(height: 6),
            Text(
              _customSoundError!,
              style: TextStyle(
                color: context.appColors.errorText,
                fontSize: 12,
              ),
            ),
          ],
        ],
        if (Platform.isAndroid || Platform.isIOS)
          SwitchListTile(
            title: Text(
              'Vibrate on message',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 15,
              ),
            ),
            subtitle: Text(
              'Vibrate when a message arrives',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 12,
              ),
            ),
            value: _vibrateEnabled,
            activeTrackColor: context.appColors.accent,
            contentPadding: EdgeInsets.zero,
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
        SwitchListTile(
          title: Text(
            'Enable toast notifications',
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 15,
            ),
          ),
          subtitle: Text(
            'Show popup alerts for important events',
            style: TextStyle(color: context.appColors.textMuted, fontSize: 12),
          ),
          value: _toastEnabled,
          activeTrackColor: context.appColors.accent,
          contentPadding: EdgeInsets.zero,
          onChanged: (v) {
            setState(() => _toastEnabled = v);
            settings.toastEnabled = v;
          },
        ),
        if (_toastEnabled) ...[
          SwitchListTile(
            title: Text(
              'Background session alerts',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 15,
              ),
            ),
            subtitle: Text(
              'Sessions waiting for input, errors, completions',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 12,
              ),
            ),
            value: _toastBackgroundSessions,
            activeTrackColor: context.appColors.accent,
            contentPadding: EdgeInsets.zero,
            onChanged: (v) {
              setState(() => _toastBackgroundSessions = v);
              settings.toastBackgroundSessions = v;
            },
          ),
          SwitchListTile(
            title: Text(
              'Task updates',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 15,
              ),
            ),
            subtitle: Text(
              'Task created or status changed',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 12,
              ),
            ),
            value: _toastTasks,
            activeTrackColor: context.appColors.accent,
            contentPadding: EdgeInsets.zero,
            onChanged: (v) {
              setState(() => _toastTasks = v);
              settings.toastTasks = v;
            },
          ),
          SwitchListTile(
            title: Text(
              'Connection alerts',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 15,
              ),
            ),
            subtitle: Text(
              'Worker connect/disconnect/reconnect events',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 12,
              ),
            ),
            value: _toastConnections,
            activeTrackColor: context.appColors.accent,
            contentPadding: EdgeInsets.zero,
            onChanged: (v) {
              setState(() => _toastConnections = v);
              settings.toastConnections = v;
            },
          ),
        ],
      ],
    );
  }
}

class _SoundOption extends StatelessWidget {
  final String label;
  final bool selected;
  final bool isLast;
  final VoidCallback onTap;
  final VoidCallback? onPreview;
  final Widget? trailing;

  const _SoundOption({
    required this.label,
    required this.selected,
    required this.isLast,
    required this.onTap,
    this.onPreview,
    this.trailing,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        InkWell(
          onTap: onTap,
          child: Padding(
            padding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            child: Row(
              children: [
                Icon(
                  selected
                      ? Icons.radio_button_checked
                      : Icons.radio_button_unchecked,
                  color: selected
                      ? context.appColors.accentLight
                      : context.appColors.textMuted,
                  size: 20,
                ),
                SizedBox(width: 10),
                Expanded(
                  child: Text(
                    label,
                    style: TextStyle(
                      color: selected
                          ? context.appColors.textPrimary
                          : context.appColors.textSecondary,
                      fontSize: 14,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                ?trailing,
                if (onPreview != null)
                  IconButton(
                    icon: Icon(
                      Icons.play_arrow_rounded,
                      color: context.appColors.textMuted,
                      size: 22,
                    ),
                    onPressed: onPreview,
                    tooltip: 'Preview',
                    constraints: BoxConstraints(minWidth: 36, minHeight: 36),
                    padding: EdgeInsets.zero,
                  ),
              ],
            ),
          ),
        ),
        if (!isLast)
          Divider(color: context.appColors.divider, height: 1, indent: 42),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Hotkeys section (desktop only)
// ---------------------------------------------------------------------------
