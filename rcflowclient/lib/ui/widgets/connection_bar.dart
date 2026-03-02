import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/split_tree.dart';
import '../../state/app_state.dart';
import '../../theme.dart';
import '../screens/workers_screen.dart';
import 'settings_menu.dart';

bool get _isDesktop =>
    Platform.isWindows || Platform.isLinux || Platform.isMacOS;

class ConnectionBar extends StatelessWidget implements PreferredSizeWidget {
  final VoidCallback? onSessionsTap;
  final bool showSessionsButton;
  final bool showSettingsButton;

  const ConnectionBar({
    super.key,
    this.onSessionsTap,
    this.showSessionsButton = true,
    this.showSettingsButton = true,
  });

  @override
  Size get preferredSize => const Size.fromHeight(kToolbarHeight);

  @override
  Widget build(BuildContext context) {
    final connected = context.select<AppState, bool>((s) => s.connected);
    final connecting = context.select<AppState, bool>((s) => s.connecting);
    final allConnected =
        context.select<AppState, bool>((s) => s.allConnected);

    // Green: all connected, Amber: partial, Red: none
    final Color dotColor;
    if (connecting) {
      dotColor = kAccentLight;
    } else if (!connected) {
      dotColor = kErrorText;
    } else if (allConnected) {
      dotColor = kSuccessText;
    } else {
      dotColor = kToolAccent;
    }

    return AppBar(
      titleSpacing: 16,
      title: GestureDetector(
        onTap: () => context.read<AppState>().activePane.goHome(),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (connecting)
              const SizedBox(
                width: 14,
                height: 14,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: kAccentLight,
                ),
              )
            else
              AnimatedContainer(
                duration: const Duration(milliseconds: 300),
                width: 10,
                height: 10,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: dotColor,
                  boxShadow: [
                    BoxShadow(
                      color: dotColor.withAlpha(100),
                      blurRadius: 6,
                      spreadRadius: 1,
                    ),
                  ],
                ),
              ),
            const SizedBox(width: 12),
            Text(
              connecting ? 'Connecting...' : 'RCFlow',
              style: TextStyle(
                fontSize: 20,
                fontWeight: FontWeight.w700,
                color: connecting ? kTextSecondary : kTextPrimary,
              ),
            ),
          ],
        ),
      ),
      actions: [
        if (connected && _isDesktop)
          PopupMenuButton<SplitAxis>(
            icon: const Icon(Icons.view_column_outlined, color: kTextSecondary),
            tooltip: 'Split pane',
            color: kBgSurface,
            shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(12)),
            onSelected: (axis) {
              final appState = context.read<AppState>();
              appState.splitPane(appState.activePaneId, axis);
            },
            itemBuilder: (_) => [
              const PopupMenuItem(
                value: SplitAxis.horizontal,
                child: Row(
                  children: [
                    Icon(Icons.view_column_outlined,
                        color: kTextSecondary, size: 18),
                    SizedBox(width: 10),
                    Text('Split Right',
                        style: TextStyle(color: kTextPrimary, fontSize: 14)),
                  ],
                ),
              ),
              const PopupMenuItem(
                value: SplitAxis.vertical,
                child: Row(
                  children: [
                    Icon(Icons.view_agenda_outlined,
                        color: kTextSecondary, size: 18),
                    SizedBox(width: 10),
                    Text('Split Down',
                        style: TextStyle(color: kTextPrimary, fontSize: 14)),
                  ],
                ),
              ),
            ],
          ),
        if (showSessionsButton)
          IconButton(
            icon: const Icon(Icons.history_rounded, color: kTextSecondary),
            onPressed: onSessionsTap,
            tooltip: 'Sessions',
          ),
        IconButton(
          icon: const Icon(Icons.dns_outlined, color: kTextSecondary),
          onPressed: () => showWorkersScreen(context),
          tooltip: 'Workers',
        ),
        if (showSettingsButton)
          IconButton(
            icon: const Icon(Icons.settings_outlined, color: kTextSecondary),
            onPressed: () => showSettingsMenu(context),
            tooltip: 'Settings',
          ),
        const SizedBox(width: 4),
      ],
    );
  }
}
