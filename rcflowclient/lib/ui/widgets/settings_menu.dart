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
import '../../theme/spacing.dart';

part 'settings_sections_workers.dart';
part 'settings_sections_appearance.dart';
part 'settings_sections_notifications.dart';
part 'settings_sections_hotkeys.dart';
part 'settings_sections_about.dart';
part 'settings_shared.dart';

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
                padding: const EdgeInsets.all(kSpace5),
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
      padding: EdgeInsets.symmetric(horizontal: kSpace2, vertical: 2),
      child: Material(
        color: selected ? context.appColors.bgElevated : Colors.transparent,
        borderRadius: BorderRadius.circular(kRadiusMedium),
        child: InkWell(
          borderRadius: BorderRadius.circular(kRadiusMedium),
          onTap: onTap,
          child: Padding(
            padding: EdgeInsets.symmetric(horizontal: kSpace3, vertical: 10),
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
