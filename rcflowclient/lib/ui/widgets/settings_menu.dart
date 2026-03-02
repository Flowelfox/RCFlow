import 'dart:io' show Platform;

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/worker_config.dart';
import '../../services/notification_sound_service.dart';
import '../../services/settings_service.dart';
import '../../state/app_state.dart';
import '../../theme.dart';
import '../../services/worker_connection.dart';
import '../dialogs/worker_edit_dialog.dart';
import '../screens/workers_screen.dart';

bool get _isDesktop =>
    Platform.isWindows || Platform.isLinux || Platform.isMacOS;

enum _Section { workers, appearance, notifications, about }

void showSettingsMenu(BuildContext context) {
  if (_isDesktop) {
    showDialog(
      context: context,
      builder: (_) => const _DesktopSettingsDialog(),
    );
  } else {
    showModalBottomSheet(
      context: context,
      isScrollControlled: true,
      builder: (_) => const _MobileSettingsSheet(),
    );
  }
}

// ---------------------------------------------------------------------------
// Desktop: two-column dialog with sidebar nav
// ---------------------------------------------------------------------------

class _DesktopSettingsDialog extends StatefulWidget {
  const _DesktopSettingsDialog();

  @override
  State<_DesktopSettingsDialog> createState() => _DesktopSettingsDialogState();
}

class _DesktopSettingsDialogState extends State<_DesktopSettingsDialog> {
  _Section _selected = _Section.workers;

  @override
  Widget build(BuildContext context) {
    return Dialog(
      backgroundColor: kBgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      child: SizedBox(
        width: 550,
        height: 500,
        child: Row(
          children: [
            // Sidebar
            Container(
              width: 160,
              decoration: const BoxDecoration(
                color: kBgBase,
                borderRadius: BorderRadius.horizontal(left: Radius.circular(16)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Padding(
                    padding: EdgeInsets.fromLTRB(20, 24, 20, 20),
                    child: Text(
                      'Settings',
                      style: TextStyle(
                        color: kTextPrimary,
                        fontSize: 18,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                  for (final section in _Section.values)
                    _SidebarItem(
                      section: section,
                      selected: _selected == section,
                      onTap: () => setState(() => _selected = section),
                    ),
                ],
              ),
            ),
            // Content
            Expanded(
              child: Padding(
                padding: const EdgeInsets.all(24),
                child: SingleChildScrollView(
                  child: _buildSection(_selected),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildSection(_Section section) {
    return switch (section) {
      _Section.workers => _WorkersSection(
          onClose: () => Navigator.of(context).pop(),
        ),
      _Section.appearance => const _AppearanceSection(),
      _Section.notifications => const _NotificationsSection(),
      _Section.about => const _AboutSection(),
    };
  }
}

class _SidebarItem extends StatelessWidget {
  final _Section section;
  final bool selected;
  final VoidCallback onTap;

  const _SidebarItem({
    required this.section,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final (label, icon) = switch (section) {
      _Section.workers => ('Workers', Icons.dns_outlined),
      _Section.appearance => ('Appearance', Icons.palette_outlined),
      _Section.notifications => ('Notifications', Icons.notifications_outlined),
      _Section.about => ('About', Icons.info_outline),
    };

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      child: Material(
        color: selected ? kBgElevated : Colors.transparent,
        borderRadius: BorderRadius.circular(10),
        child: InkWell(
          borderRadius: BorderRadius.circular(10),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            child: Row(
              children: [
                Icon(icon,
                    size: 18,
                    color: selected ? kAccentLight : kTextMuted),
                const SizedBox(width: 10),
                Text(
                  label,
                  style: TextStyle(
                    color: selected ? kTextPrimary : kTextSecondary,
                    fontSize: 14,
                    fontWeight: selected ? FontWeight.w600 : FontWeight.normal,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Mobile: draggable scrollable bottom sheet with all sections
// ---------------------------------------------------------------------------

class _MobileSettingsSheet extends StatelessWidget {
  const _MobileSettingsSheet();

  @override
  Widget build(BuildContext context) {
    return DraggableScrollableSheet(
      initialChildSize: 0.6,
      minChildSize: 0.4,
      maxChildSize: 0.92,
      expand: false,
      builder: (ctx, scrollController) {
        return SingleChildScrollView(
          controller: scrollController,
          padding: EdgeInsets.only(
            bottom: MediaQuery.of(ctx).viewInsets.bottom + 24,
            left: 24,
            right: 24,
            top: 12,
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Center(
                child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(
                    color: kTextMuted.withAlpha(100),
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              const SizedBox(height: 20),
              const Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  'Settings',
                  style: TextStyle(
                    color: kTextPrimary,
                    fontSize: 22,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              const SizedBox(height: 24),
              _WorkersSection(
                onClose: () => Navigator.of(ctx).pop(),
              ),
              const _SectionDivider(),
              const _AppearanceSection(),
              const _SectionDivider(),
              const _NotificationsSection(),
              const _SectionDivider(),
              const _AboutSection(),
            ],
          ),
        );
      },
    );
  }
}

class _SectionDivider extends StatelessWidget {
  const _SectionDivider();

  @override
  Widget build(BuildContext context) {
    return const Padding(
      padding: EdgeInsets.symmetric(vertical: 20),
      child: Divider(color: kDivider, height: 1),
    );
  }
}

// ---------------------------------------------------------------------------
// Section header
// ---------------------------------------------------------------------------

class _SectionHeader extends StatelessWidget {
  final String title;
  final IconData icon;

  const _SectionHeader({required this.title, required this.icon});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 16),
      child: Row(
        children: [
          Icon(icon, color: kAccentLight, size: 20),
          const SizedBox(width: 8),
          Text(
            title,
            style: const TextStyle(
              color: kTextPrimary,
              fontSize: 17,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Workers section (replaces old Connection section)
// ---------------------------------------------------------------------------

class _WorkersSection extends StatelessWidget {
  final VoidCallback onClose;

  const _WorkersSection({required this.onClose});

  @override
  Widget build(BuildContext context) {
    return Consumer<AppState>(
      builder: (ctx, state, _) {
        final total = state.totalWorkerCount;
        final conn = state.connectedWorkerCount;
        final summary = total == 0
            ? 'No workers configured'
            : '$conn of $total connected';

        return Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const _SectionHeader(title: 'Workers', icon: Icons.dns_outlined),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: kBgElevated,
                borderRadius: BorderRadius.circular(14),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Container(
                        width: 10,
                        height: 10,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: conn > 0 ? kSuccessText : kTextMuted,
                        ),
                      ),
                      const SizedBox(width: 10),
                      Text(
                        summary,
                        style: const TextStyle(
                          color: kTextPrimary,
                          fontSize: 15,
                        ),
                      ),
                    ],
                  ),
                  if (_isDesktop) ...[
                    // Desktop: inline worker list + add button
                    if (state.workerConfigs.isNotEmpty) ...[
                      const SizedBox(height: 12),
                      for (final config in state.workerConfigs)
                        _WorkerRow(
                          config: config,
                          worker: state.getWorker(config.id),
                          onEdit: () async {
                            final updated = await showWorkerEditDialog(
                              ctx,
                              existing: config,
                              worker: state.getWorker(config.id),
                            );
                            if (updated != null && ctx.mounted) {
                              state.updateWorker(updated);
                            }
                          },
                        ),
                    ],
                    const SizedBox(height: 8),
                    SizedBox(
                      width: double.infinity,
                      height: 38,
                      child: OutlinedButton.icon(
                        onPressed: () async {
                          final config = await showWorkerEditDialog(
                            ctx,
                            sortOrder: state.workerConfigs.length,
                          );
                          if (config != null && ctx.mounted) {
                            state.addWorker(config);
                          }
                        },
                        icon: const Icon(Icons.add_rounded, size: 18),
                        label: const Text('Add Worker',
                            style: TextStyle(fontSize: 13)),
                        style: OutlinedButton.styleFrom(
                          foregroundColor: kTextSecondary,
                          side: const BorderSide(color: kDivider),
                          shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(10)),
                        ),
                      ),
                    ),
                  ] else ...[
                    // Mobile: button to open full workers screen
                    const SizedBox(height: 12),
                    SizedBox(
                      width: double.infinity,
                      height: 44,
                      child: Builder(
                        builder: (btnContext) => FilledButton.icon(
                          onPressed: () {
                            onClose();
                            Future.microtask(() {
                              if (btnContext.mounted) {
                                showWorkersScreen(btnContext);
                              }
                            });
                          },
                          icon: const Icon(Icons.settings_outlined, size: 18),
                          label: const Text('Manage Workers',
                              style: TextStyle(
                                  fontSize: 14, fontWeight: FontWeight.w600)),
                          style: FilledButton.styleFrom(
                            backgroundColor: kAccent,
                            foregroundColor: Colors.white,
                            shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(12)),
                          ),
                        ),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ],
        );
      },
    );
  }
}

class _WorkerRow extends StatelessWidget {
  final WorkerConfig config;
  final WorkerConnection? worker;
  final VoidCallback onEdit;

  const _WorkerRow({
    required this.config,
    required this.worker,
    required this.onEdit,
  });

  @override
  Widget build(BuildContext context) {
    final status = worker?.status ?? WorkerConnectionStatus.disconnected;
    final statusColor = switch (status) {
      WorkerConnectionStatus.connected => kSuccessText,
      WorkerConnectionStatus.connecting => kToolAccent,
      WorkerConnectionStatus.reconnecting => kToolAccent,
      WorkerConnectionStatus.disconnected => kTextMuted,
    };

    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(
        children: [
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: statusColor,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              config.name,
              style: const TextStyle(
                color: kTextSecondary,
                fontSize: 13,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          SizedBox(
            width: 28,
            height: 28,
            child: IconButton(
              padding: EdgeInsets.zero,
              icon: const Icon(Icons.edit_outlined,
                  color: kTextMuted, size: 16),
              onPressed: onEdit,
              tooltip: 'Edit worker',
              constraints: const BoxConstraints(
                  maxWidth: 28, maxHeight: 28),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Appearance section
// ---------------------------------------------------------------------------

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

  void _save(SettingsService settings) {
    settings.themeMode = _themeMode;
    settings.fontSize = _fontSize;
    settings.compactMode = _compactMode;
  }

  @override
  Widget build(BuildContext context) {
    final settings = context.read<AppState>().settings;

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const _SectionHeader(
            title: 'Appearance', icon: Icons.palette_outlined),
        const Text('Theme',
            style: TextStyle(color: kTextSecondary, fontSize: 13)),
        const SizedBox(height: 8),
        _SegmentedSelector(
          options: const ['system', 'dark', 'light'],
          labels: const ['System', 'Dark', 'Light'],
          selected: _themeMode,
          onChanged: (v) {
            setState(() => _themeMode = v);
            _save(settings);
          },
        ),
        const SizedBox(height: 20),
        const Text('Message font size',
            style: TextStyle(color: kTextSecondary, fontSize: 13)),
        const SizedBox(height: 8),
        _SegmentedSelector(
          options: const ['small', 'medium', 'large'],
          labels: const ['Small', 'Medium', 'Large'],
          selected: _fontSize,
          onChanged: (v) {
            setState(() => _fontSize = v);
            _save(settings);
          },
        ),
        const SizedBox(height: 16),
        SwitchListTile(
          title: const Text('Compact mode',
              style: TextStyle(color: kTextPrimary, fontSize: 15)),
          subtitle: const Text('Reduce padding in message bubbles',
              style: TextStyle(color: kTextMuted, fontSize: 12)),
          value: _compactMode,
          activeTrackColor: kAccent,
          contentPadding: EdgeInsets.zero,
          onChanged: (v) {
            setState(() => _compactMode = v);
            _save(settings);
          },
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Notifications section
// ---------------------------------------------------------------------------

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
  }

  bool get _anySoundEnabled => _soundEnabled || _soundOnCompleteEnabled;

  Future<void> _pickCustomSound(SettingsService settings) async {
    final result = await FilePicker.platform.pickFiles(
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
        const _SectionHeader(
            title: 'Notifications', icon: Icons.notifications_outlined),
        SwitchListTile(
          title: const Text('Sound when done',
              style: TextStyle(color: kTextPrimary, fontSize: 15)),
          subtitle: const Text(
              'Play a sound when work finishes and waiting for input',
              style: TextStyle(color: kTextMuted, fontSize: 12)),
          value: _soundOnCompleteEnabled,
          activeTrackColor: kAccent,
          contentPadding: EdgeInsets.zero,
          onChanged: (v) {
            setState(() => _soundOnCompleteEnabled = v);
            settings.soundOnCompleteEnabled = v;
          },
        ),
        SwitchListTile(
          title: const Text('Sound on message',
              style: TextStyle(color: kTextPrimary, fontSize: 15)),
          subtitle: const Text('Play a sound when a message arrives',
              style: TextStyle(color: kTextMuted, fontSize: 12)),
          value: _soundEnabled,
          activeTrackColor: kAccent,
          contentPadding: EdgeInsets.zero,
          onChanged: (v) {
            setState(() => _soundEnabled = v);
            settings.soundEnabled = v;
          },
        ),
        if (_anySoundEnabled) ...[
          const SizedBox(height: 12),
          const Text('Notification sound',
              style: TextStyle(color: kTextSecondary, fontSize: 13)),
          const SizedBox(height: 8),
          Container(
            decoration: BoxDecoration(
              color: kBgElevated,
              borderRadius: BorderRadius.circular(14),
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
                      icon: const Icon(Icons.folder_open,
                          color: kTextMuted, size: 20),
                      onPressed: () => _pickCustomSound(settings),
                      tooltip: 'Browse...',
                    ),
                  ),
                ],
              ],
            ),
          ),
          if (_customSoundError != null) ...[
            const SizedBox(height: 6),
            Text(
              _customSoundError!,
              style: const TextStyle(color: kErrorText, fontSize: 12),
            ),
          ],
        ],
        if (Platform.isAndroid || Platform.isIOS)
          SwitchListTile(
            title: const Text('Vibrate on message',
                style: TextStyle(color: kTextPrimary, fontSize: 15)),
            subtitle: const Text('Vibrate when a message arrives',
                style: TextStyle(color: kTextMuted, fontSize: 12)),
            value: _vibrateEnabled,
            activeTrackColor: kAccent,
            contentPadding: EdgeInsets.zero,
            onChanged: (v) {
              setState(() => _vibrateEnabled = v);
              settings.vibrateEnabled = v;
            },
          ),
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
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            child: Row(
              children: [
                Icon(
                  selected
                      ? Icons.radio_button_checked
                      : Icons.radio_button_unchecked,
                  color: selected ? kAccentLight : kTextMuted,
                  size: 20,
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    label,
                    style: TextStyle(
                      color: selected ? kTextPrimary : kTextSecondary,
                      fontSize: 14,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                ?trailing,
                if (onPreview != null)
                  IconButton(
                    icon: const Icon(Icons.play_arrow_rounded,
                        color: kTextMuted, size: 22),
                    onPressed: onPreview,
                    tooltip: 'Preview',
                    constraints:
                        const BoxConstraints(minWidth: 36, minHeight: 36),
                    padding: EdgeInsets.zero,
                  ),
              ],
            ),
          ),
        ),
        if (!isLast) const Divider(color: kDivider, height: 1, indent: 42),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// About section
// ---------------------------------------------------------------------------

class _AboutSection extends StatelessWidget {
  const _AboutSection();

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        const _SectionHeader(title: 'About', icon: Icons.info_outline),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: kBgElevated,
            borderRadius: BorderRadius.circular(14),
          ),
          child: const Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'RCFlow Client',
                style: TextStyle(
                  color: kTextPrimary,
                  fontSize: 16,
                  fontWeight: FontWeight.w600,
                ),
              ),
              SizedBox(height: 4),
              Text(
                'v0.1.0',
                style: TextStyle(color: kAccentLight, fontSize: 14),
              ),
              SizedBox(height: 12),
              Text(
                'A client for the RCFlow server — execute actions on your '
                'host machine via natural language prompts.',
                style: TextStyle(color: kTextSecondary, fontSize: 13),
              ),
            ],
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Segmented selector (reusable for theme / font size)
// ---------------------------------------------------------------------------

class _SegmentedSelector extends StatelessWidget {
  final List<String> options;
  final List<String> labels;
  final String selected;
  final ValueChanged<String> onChanged;

  const _SegmentedSelector({
    required this.options,
    required this.labels,
    required this.selected,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: kBgElevated,
        borderRadius: BorderRadius.circular(12),
      ),
      padding: const EdgeInsets.all(4),
      child: Row(
        children: [
          for (var i = 0; i < options.length; i++)
            Expanded(
              child: GestureDetector(
                onTap: () => onChanged(options[i]),
                child: AnimatedContainer(
                  duration: const Duration(milliseconds: 200),
                  padding: const EdgeInsets.symmetric(vertical: 10),
                  decoration: BoxDecoration(
                    color: selected == options[i] ? kAccent : Colors.transparent,
                    borderRadius: BorderRadius.circular(9),
                  ),
                  alignment: Alignment.center,
                  child: Text(
                    labels[i],
                    style: TextStyle(
                      color: selected == options[i]
                          ? Colors.white
                          : kTextSecondary,
                      fontSize: 13,
                      fontWeight: selected == options[i]
                          ? FontWeight.w600
                          : FontWeight.normal,
                    ),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}
