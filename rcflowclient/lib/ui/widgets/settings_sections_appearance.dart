part of 'settings_menu.dart';

class _AppearanceSection extends StatefulWidget {
  const _AppearanceSection();

  @override
  State<_AppearanceSection> createState() => _AppearanceSectionState();
}

class _AppearanceSectionState extends State<_AppearanceSection> {
  late String _themeMode;
  late String _fontSize;
  late bool _compactMode;

  @override
  void initState() {
    super.initState();
    final settings = context.read<AppState>().settings;
    _themeMode = settings.themeMode;
    _fontSize = settings.fontSize;
    _compactMode = settings.compactMode;
  }

  void _save() {
    context.read<AppState>().updateAppearance(
      themeMode: _themeMode,
      fontSize: _fontSize,
      compactMode: _compactMode,
    );
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SectionHeader(title: 'Appearance', icon: Icons.palette_outlined),
        Text(
          'Theme',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 13,
          ),
        ),
        const SizedBox(height: 8),
        _SegmentedSelector(
          options: const ['system', 'dark', 'light'],
          labels: ['System', 'Dark', 'Light'],
          selected: _themeMode,
          onChanged: (v) {
            setState(() => _themeMode = v);
            _save();
          },
        ),
        SizedBox(height: 20),
        Text(
          'Message font size',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 13,
          ),
        ),
        const SizedBox(height: 8),
        _SegmentedSelector(
          options: const ['small', 'medium', 'large'],
          labels: const ['Small', 'Medium', 'Large'],
          selected: _fontSize,
          onChanged: (v) {
            setState(() => _fontSize = v);
            _save();
          },
        ),
        SizedBox(height: 16),
        SwitchListTile(
          title: Text(
            'Compact mode',
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 15,
            ),
          ),
          subtitle: Text(
            'Reduce padding in message bubbles',
            style: TextStyle(color: context.appColors.textMuted, fontSize: 12),
          ),
          value: _compactMode,
          activeTrackColor: context.appColors.accent,
          contentPadding: EdgeInsets.zero,
          onChanged: (v) {
            setState(() => _compactMode = v);
            _save();
          },
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Notifications section
// ---------------------------------------------------------------------------
