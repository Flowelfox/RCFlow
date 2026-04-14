import 'dart:io' show Platform;

import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../models/hotkey_binding.dart';
import '../../models/worker_config.dart';
import '../../services/notification_sound_service.dart';
import '../../services/settings_service.dart';
import '../../state/app_state.dart';
import '../../theme.dart';
import '../../services/worker_connection.dart';
import '../dialogs/setup_wizard.dart';
import '../dialogs/worker_edit_dialog.dart';
import '../screens/workers_screen.dart';
import 'onboarding_overlay.dart';

bool get _isDesktop =>
    Platform.isWindows || Platform.isLinux || Platform.isMacOS;

enum _Section { workers, appearance, hotkeys, notifications, about }

void showSettingsMenu(BuildContext context) {
  if (_isDesktop) {
    showDialog(
      context: context,
      builder: (_) => const _DesktopSettingsDialog(),
    );
  } else if (Platform.isAndroid) {
    Navigator.of(
      context,
    ).push(MaterialPageRoute(builder: (_) => _AndroidSettingsPage()));
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
      backgroundColor: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      child: SizedBox(
        width: 550,
        height: 500,
        child: Row(
          children: [
            // Sidebar
            Container(
              width: 160,
              decoration: BoxDecoration(
                color: context.appColors.bgBase,
                borderRadius: BorderRadius.horizontal(
                  left: Radius.circular(16),
                ),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Padding(
                    padding: EdgeInsets.fromLTRB(20, 24, 20, 20),
                    child: Text(
                      'Settings',
                      style: TextStyle(
                        color: context.appColors.textPrimary,
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
                child: SingleChildScrollView(child: _buildSection(_selected)),
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
      _Section.hotkeys => const _HotkeysSection(),
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
      _Section.hotkeys => ('Hotkeys', Icons.keyboard_outlined),
      _Section.notifications => ('Notifications', Icons.notifications_outlined),
      _Section.about => ('About', Icons.info_outline),
    };

    return Padding(
      padding: EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      child: Material(
        color: selected ? context.appColors.bgElevated : Colors.transparent,
        borderRadius: BorderRadius.circular(10),
        child: InkWell(
          borderRadius: BorderRadius.circular(10),
          onTap: onTap,
          child: Padding(
            padding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            child: Row(
              children: [
                Icon(
                  icon,
                  size: 18,
                  color: selected
                      ? context.appColors.accentLight
                      : context.appColors.textMuted,
                ),
                SizedBox(width: 10),
                Text(
                  label,
                  style: TextStyle(
                    color: selected
                        ? context.appColors.textPrimary
                        : context.appColors.textSecondary,
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
                    color: context.appColors.textMuted.withAlpha(100),
                    borderRadius: BorderRadius.circular(2),
                  ),
                ),
              ),
              SizedBox(height: 20),
              Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  'Settings',
                  style: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 22,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
              const SizedBox(height: 24),
              _WorkersSection(onClose: () => Navigator.of(ctx).pop()),
              _SectionDivider(),
              _AppearanceSection(),
              if (_isDesktop) ...[_SectionDivider(), _HotkeysSection()],
              _SectionDivider(),
              _NotificationsSection(),
              _SectionDivider(),
              _AboutSection(),
            ],
          ),
        );
      },
    );
  }
}

// ---------------------------------------------------------------------------
// Android: full-screen settings page
// ---------------------------------------------------------------------------

class _AndroidSettingsPage extends StatelessWidget {
  const _AndroidSettingsPage();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: context.appColors.bgBase,
      appBar: AppBar(
        backgroundColor: context.appColors.bgSurface,
        foregroundColor: context.appColors.textPrimary,
        title: Text(
          'Settings',
          style: TextStyle(
            color: context.appColors.textPrimary,
            fontSize: 20,
            fontWeight: FontWeight.w700,
          ),
        ),
        elevation: 0,
      ),
      body: SingleChildScrollView(
        padding: EdgeInsets.only(
          bottom: MediaQuery.of(context).viewInsets.bottom + 24,
          left: 24,
          right: 24,
          top: 24,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            _WorkersSection(onClose: () => Navigator.of(context).pop()),
            _SectionDivider(),
            _AppearanceSection(),
            _SectionDivider(),
            _NotificationsSection(),
            _SectionDivider(),
            _AboutSection(),
          ],
        ),
      ),
    );
  }
}

/// The scrollable body of the Android settings screen.
///
/// Exported so [AndroidShell] can embed it in its own [Scaffold] without
/// duplicating the AppBar.  Identical content to [_AndroidSettingsPage.body].
class AndroidSettingsBody extends StatelessWidget {
  const AndroidSettingsBody({super.key});

  @override
  Widget build(BuildContext context) {
    return SingleChildScrollView(
      padding: EdgeInsets.only(
        bottom: MediaQuery.of(context).viewInsets.bottom + 24,
        left: 24,
        right: 24,
        top: 24,
      ),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          _WorkersSection(onClose: () => Navigator.of(context).maybePop()),
          _SectionDivider(),
          _AppearanceSection(),
          _SectionDivider(),
          _NotificationsSection(),
          _SectionDivider(),
          _AboutSection(),
        ],
      ),
    );
  }
}

class _SectionDivider extends StatelessWidget {
  const _SectionDivider();

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.symmetric(vertical: 20),
      child: Divider(color: context.appColors.divider, height: 1),
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
      padding: EdgeInsets.only(bottom: 16),
      child: Row(
        children: [
          Icon(icon, color: context.appColors.accentLight, size: 20),
          SizedBox(width: 8),
          Text(
            title,
            style: TextStyle(
              color: context.appColors.textPrimary,
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
            _SectionHeader(title: 'Workers', icon: Icons.dns_outlined),
            Container(
              width: double.infinity,
              padding: EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: context.appColors.bgElevated,
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
                          color: conn > 0
                              ? context.appColors.successText
                              : context.appColors.textMuted,
                        ),
                      ),
                      SizedBox(width: 10),
                      Text(
                        summary,
                        style: TextStyle(
                          color: context.appColors.textPrimary,
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
                        icon: Icon(Icons.add_rounded, size: 18),
                        label: Text(
                          'Add Worker',
                          style: TextStyle(fontSize: 13),
                        ),
                        style: OutlinedButton.styleFrom(
                          foregroundColor: context.appColors.textSecondary,
                          side: BorderSide(color: context.appColors.divider),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(10),
                          ),
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
                          icon: Icon(Icons.settings_outlined, size: 18),
                          label: Text(
                            'Manage Workers',
                            style: TextStyle(
                              fontSize: 14,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                          style: FilledButton.styleFrom(
                            backgroundColor: context.appColors.accent,
                            foregroundColor: Colors.white,
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(12),
                            ),
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
      WorkerConnectionStatus.connected => context.appColors.successText,
      WorkerConnectionStatus.connecting => context.appColors.toolAccent,
      WorkerConnectionStatus.reconnecting => context.appColors.toolAccent,
      WorkerConnectionStatus.disconnected => context.appColors.textMuted,
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
          SizedBox(width: 8),
          Expanded(
            child: Text(
              config.name,
              style: TextStyle(
                color: context.appColors.textSecondary,
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
              icon: Icon(
                Icons.edit_outlined,
                color: context.appColors.textMuted,
                size: 16,
              ),
              onPressed: onEdit,
              tooltip: 'Edit worker',
              constraints: const BoxConstraints(maxWidth: 28, maxHeight: 28),
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

class _HotkeysSection extends StatefulWidget {
  const _HotkeysSection();

  @override
  State<_HotkeysSection> createState() => _HotkeysSectionState();
}

class _HotkeysSectionState extends State<_HotkeysSection> {
  HotkeyAction? _recordingAction;
  String? _conflictError;

  @override
  Widget build(BuildContext context) {
    final service = context.read<AppState>().hotkeyService;

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SectionHeader(title: 'Hotkeys', icon: Icons.keyboard_outlined),
        for (final entry in hotkeyActionGroups.entries) ...[
          Padding(
            padding: const EdgeInsets.only(top: 8, bottom: 6),
            child: Text(
              entry.key,
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 11,
                fontWeight: FontWeight.w600,
                letterSpacing: 0.5,
              ),
            ),
          ),
          for (final action in entry.value)
            _HotkeyRow(
              label: hotkeyActionLabel(action),
              binding: service.bindingFor(action)!,
              isDefault: _isDefault(service, action),
              isRecording: _recordingAction == action,
              error: _recordingAction == action ? _conflictError : null,
              onStartRecording: () {
                setState(() {
                  _recordingAction = action;
                  _conflictError = null;
                });
              },
              onCancelRecording: () {
                setState(() {
                  _recordingAction = null;
                  _conflictError = null;
                });
              },
              onNewBinding: (binding) {
                final conflict = service.updateBinding(binding);
                if (conflict != null) {
                  setState(() {
                    _conflictError =
                        'Conflicts with ${hotkeyActionLabel(conflict.action)}';
                  });
                } else {
                  setState(() {
                    _recordingAction = null;
                    _conflictError = null;
                  });
                }
              },
              onReset: () {
                service.resetBinding(action);
                setState(() {
                  _recordingAction = null;
                  _conflictError = null;
                });
              },
            ),
        ],
        const SizedBox(height: 16),
        SizedBox(
          width: double.infinity,
          height: 34,
          child: OutlinedButton(
            onPressed: () {
              service.resetAllBindings();
              setState(() {
                _recordingAction = null;
                _conflictError = null;
              });
            },
            style: OutlinedButton.styleFrom(
              foregroundColor: context.appColors.textSecondary,
              side: BorderSide(color: context.appColors.divider),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(10),
              ),
            ),
            child: Text(
              'Reset All to Defaults',
              style: TextStyle(fontSize: 12),
            ),
          ),
        ),
      ],
    );
  }

  bool _isDefault(dynamic service, HotkeyAction action) {
    final current = service.bindingFor(action) as HotkeyBinding?;
    final def = service.defaultBindingFor(action) as HotkeyBinding;
    if (current == null) return true;
    return current.ctrl == def.ctrl &&
        current.alt == def.alt &&
        current.shift == def.shift &&
        current.meta == def.meta &&
        current.key == def.key;
  }
}

class _HotkeyRow extends StatelessWidget {
  final String label;
  final HotkeyBinding binding;
  final bool isDefault;
  final bool isRecording;
  final String? error;
  final VoidCallback onStartRecording;
  final VoidCallback onCancelRecording;
  final void Function(HotkeyBinding) onNewBinding;
  final VoidCallback onReset;

  const _HotkeyRow({
    required this.label,
    required this.binding,
    required this.isDefault,
    required this.isRecording,
    required this.error,
    required this.onStartRecording,
    required this.onCancelRecording,
    required this.onNewBinding,
    required this.onReset,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 2),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  label,
                  style: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 13,
                  ),
                ),
              ),
              if (isRecording)
                _HotkeyRecorder(
                  action: binding.action,
                  onNewBinding: onNewBinding,
                  onCancel: onCancelRecording,
                )
              else
                InkWell(
                  borderRadius: BorderRadius.circular(6),
                  onTap: onStartRecording,
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 4,
                    ),
                    decoration: BoxDecoration(
                      color: context.appColors.bgElevated,
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: Text(
                      binding.label,
                      style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 12,
                        fontFamily: 'monospace',
                      ),
                    ),
                  ),
                ),
              const SizedBox(width: 4),
              SizedBox(
                width: 24,
                height: 24,
                child: isDefault
                    ? const SizedBox.shrink()
                    : IconButton(
                        padding: EdgeInsets.zero,
                        iconSize: 14,
                        icon: Icon(
                          Icons.restart_alt_rounded,
                          color: context.appColors.textMuted,
                        ),
                        tooltip: 'Reset to default',
                        onPressed: onReset,
                        constraints: const BoxConstraints(
                          maxWidth: 24,
                          maxHeight: 24,
                        ),
                      ),
              ),
            ],
          ),
          if (error != null)
            Padding(
              padding: const EdgeInsets.only(top: 2, bottom: 4),
              child: Text(
                error!,
                style: TextStyle(
                  color: context.appColors.errorText,
                  fontSize: 11,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// Captures a new key combination when in recording mode.
class _HotkeyRecorder extends StatefulWidget {
  final HotkeyAction action;
  final void Function(HotkeyBinding) onNewBinding;
  final VoidCallback onCancel;

  const _HotkeyRecorder({
    required this.action,
    required this.onNewBinding,
    required this.onCancel,
  });

  @override
  State<_HotkeyRecorder> createState() => _HotkeyRecorderState();
}

class _HotkeyRecorderState extends State<_HotkeyRecorder> {
  final FocusNode _focusNode = FocusNode();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _focusNode.requestFocus();
    });
  }

  @override
  void dispose() {
    _focusNode.dispose();
    super.dispose();
  }

  static final _modifierKeys = {
    LogicalKeyboardKey.controlLeft,
    LogicalKeyboardKey.controlRight,
    LogicalKeyboardKey.altLeft,
    LogicalKeyboardKey.altRight,
    LogicalKeyboardKey.shiftLeft,
    LogicalKeyboardKey.shiftRight,
    LogicalKeyboardKey.metaLeft,
    LogicalKeyboardKey.metaRight,
  };

  KeyEventResult _onKeyEvent(FocusNode node, KeyEvent event) {
    if (event is! KeyDownEvent) return KeyEventResult.handled;

    // Escape cancels recording
    if (event.logicalKey == LogicalKeyboardKey.escape) {
      widget.onCancel();
      return KeyEventResult.handled;
    }

    // Ignore pure modifier presses (wait for the actual key)
    if (_modifierKeys.contains(event.logicalKey)) {
      return KeyEventResult.handled;
    }

    final pressed = HardwareKeyboard.instance.logicalKeysPressed;
    final isCtrl =
        pressed.contains(LogicalKeyboardKey.controlLeft) ||
        pressed.contains(LogicalKeyboardKey.controlRight);
    final isAlt =
        pressed.contains(LogicalKeyboardKey.altLeft) ||
        pressed.contains(LogicalKeyboardKey.altRight);
    final isShift =
        pressed.contains(LogicalKeyboardKey.shiftLeft) ||
        pressed.contains(LogicalKeyboardKey.shiftRight);
    final isMeta =
        pressed.contains(LogicalKeyboardKey.metaLeft) ||
        pressed.contains(LogicalKeyboardKey.metaRight);

    widget.onNewBinding(
      HotkeyBinding(
        action: widget.action,
        ctrl: isCtrl,
        alt: isAlt,
        shift: isShift,
        meta: isMeta,
        key: event.logicalKey,
      ),
    );

    return KeyEventResult.handled;
  }

  @override
  Widget build(BuildContext context) {
    return Focus(
      focusNode: _focusNode,
      onKeyEvent: _onKeyEvent,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: context.appColors.bgElevated,
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: context.appColors.accent, width: 1.5),
        ),
        child: Text(
          'Press keys...',
          style: TextStyle(
            color: context.appColors.accentLight,
            fontSize: 12,
            fontStyle: FontStyle.italic,
          ),
        ),
      ),
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
    final appState = context.read<AppState>();
    final version = appState.settings.currentVersion ?? '';

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SectionHeader(title: 'About', icon: Icons.info_outline),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(16),
          decoration: BoxDecoration(
            color: context.appColors.bgElevated,
            borderRadius: BorderRadius.circular(14),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'RCFlow Client',
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 16,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                version.isEmpty ? 'Unknown version' : 'v$version',
                style: TextStyle(
                  color: context.appColors.accentLight,
                  fontSize: 14,
                ),
              ),
              const SizedBox(height: 12),
              Text(
                'A client for the RCFlow server — execute actions on your '
                'host machine via natural language prompts.',
                style: TextStyle(
                  color: context.appColors.textSecondary,
                  fontSize: 13,
                ),
              ),
              const SizedBox(height: 16),
              // ── Update status ───────────────────────────────────────────
              ListenableBuilder(
                listenable: appState.updateService,
                builder: (ctx, _) {
                  final svc = appState.updateService;
                  if (svc.isChecking) {
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: Row(
                        children: [
                          SizedBox(
                            width: 14,
                            height: 14,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: context.appColors.textMuted,
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text(
                            'Checking for updates…',
                            style: TextStyle(
                              color: context.appColors.textMuted,
                              fontSize: 13,
                            ),
                          ),
                        ],
                      ),
                    );
                  }

                  if (svc.hasError) {
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: Row(
                        children: [
                          Icon(
                            Icons.error_outline,
                            size: 16,
                            color: context.appColors.errorText,
                          ),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Text(
                              'Update check failed',
                              style: TextStyle(
                                color: context.appColors.errorText,
                                fontSize: 13,
                              ),
                            ),
                          ),
                          TextButton(
                            onPressed: svc.checkForUpdates,
                            style: TextButton.styleFrom(
                              foregroundColor: context.appColors.textMuted,
                              padding: const EdgeInsets.symmetric(
                                horizontal: 8,
                              ),
                              minimumSize: Size.zero,
                              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                            ),
                            child: const Text(
                              'Retry',
                              style: TextStyle(fontSize: 12),
                            ),
                          ),
                        ],
                      ),
                    );
                  }

                  if (svc.updateAvailable) {
                    final latest = svc.latestVersion!;
                    final url = svc.latestDownloadUrl ?? svc.latestReleaseUrl;
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: Row(
                        children: [
                          Icon(
                            Icons.new_releases_outlined,
                            size: 16,
                            color: context.appColors.accent,
                          ),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Text(
                              'v$latest available',
                              style: TextStyle(
                                color: context.appColors.accent,
                                fontSize: 13,
                              ),
                            ),
                          ),
                          if (url != null)
                            TextButton(
                              onPressed: () => launchUrl(
                                Uri.parse(url),
                                mode: LaunchMode.externalApplication,
                              ),
                              style: TextButton.styleFrom(
                                foregroundColor: context.appColors.accent,
                                padding: const EdgeInsets.symmetric(
                                  horizontal: 8,
                                ),
                                minimumSize: Size.zero,
                                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                              ),
                              child: const Text(
                                'Update',
                                style: TextStyle(fontSize: 12),
                              ),
                            ),
                        ],
                      ),
                    );
                  }

                  // No update available (or not yet checked) — show button.
                  final upToDate = svc.latestVersion != null;
                  return Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Row(
                      children: [
                        if (upToDate) ...[
                          Icon(
                            Icons.check_circle_outline,
                            size: 16,
                            color: context.appColors.successText,
                          ),
                          const SizedBox(width: 6),
                          Text(
                            'Up to date',
                            style: TextStyle(
                              color: context.appColors.successText,
                              fontSize: 13,
                            ),
                          ),
                          const SizedBox(width: 8),
                        ],
                        TextButton(
                          onPressed: svc.checkForUpdates,
                          style: TextButton.styleFrom(
                            foregroundColor: context.appColors.textMuted,
                            padding: const EdgeInsets.symmetric(horizontal: 8),
                            minimumSize: Size.zero,
                            tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                          ),
                          child: Text(
                            upToDate ? 'Check again' : 'Check for updates',
                            style: const TextStyle(fontSize: 12),
                          ),
                        ),
                      ],
                    ),
                  );
                },
              ),
              // ── Action buttons ──────────────────────────────────────────
              Row(
                children: [
                  OutlinedButton.icon(
                    onPressed: () {
                      // Capture navigator before pop — dialog context dies.
                      final nav = Navigator.of(context);
                      nav.pop();
                      Future.delayed(const Duration(milliseconds: 200), () {
                        final ctx = nav.context;
                        if (ctx.mounted) {
                          showSetupWizard(ctx);
                        }
                      });
                    },
                    icon: const Icon(Icons.rocket_launch_outlined, size: 18),
                    label: const Text('Setup Wizard'),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: context.appColors.textSecondary,
                      side: BorderSide(color: context.appColors.divider),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(10),
                      ),
                      padding: const EdgeInsets.symmetric(
                        horizontal: 14,
                        vertical: 10,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  OutlinedButton.icon(
                    onPressed: () {
                      // Capture overlay + settings before pop — the dialog
                      // context becomes unmounted once Navigator.pop() runs.
                      final overlay = Overlay.of(context);
                      final settings = context.read<AppState>().settings;
                      settings.onboardingComplete = false;
                      Navigator.of(context).pop();
                      // Delayed so the dialog fully closes first. The
                      // captured overlay & settings stay valid.
                      Future.delayed(const Duration(milliseconds: 200), () {
                        final ctx = overlay.context;
                        if (ctx.mounted) {
                          showOnboardingOverlay(
                            ctx,
                            overlay: overlay,
                            settings: settings,
                          );
                        }
                      });
                    },
                    icon: const Icon(Icons.tour_outlined, size: 18),
                    label: const Text('Replay Tour'),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: context.appColors.textSecondary,
                      side: BorderSide(color: context.appColors.divider),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(10),
                      ),
                      padding: const EdgeInsets.symmetric(
                        horizontal: 14,
                        vertical: 10,
                      ),
                    ),
                  ),
                ],
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
        color: context.appColors.bgElevated,
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
                  duration: Duration(milliseconds: 200),
                  padding: EdgeInsets.symmetric(vertical: 10),
                  decoration: BoxDecoration(
                    color: selected == options[i]
                        ? context.appColors.accent
                        : Colors.transparent,
                    borderRadius: BorderRadius.circular(9),
                  ),
                  alignment: Alignment.center,
                  child: Text(
                    labels[i],
                    style: TextStyle(
                      color: selected == options[i]
                          ? Colors.white
                          : context.appColors.textSecondary,
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
