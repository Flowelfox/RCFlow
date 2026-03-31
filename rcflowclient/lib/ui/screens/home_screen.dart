import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:window_manager/window_manager.dart';

import '../../models/split_tree.dart';
import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';
import '../dialogs/setup_wizard.dart';
import '../onboarding_keys.dart' as onboarding;
import '../widgets/connection_bar.dart';
import '../widgets/custom_title_bar.dart';
import '../widgets/hotkey_listener.dart';
import '../widgets/input_area.dart';
import '../widgets/onboarding_overlay.dart';
import '../widgets/output_display.dart';
import '../widgets/session_panel.dart';
import '../widgets/settings_menu.dart';
import '../widgets/split_view.dart';
import '../widgets/worker_picker_dialog.dart';

bool get _isDesktop =>
    Platform.isWindows || Platform.isLinux || Platform.isMacOS;

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key});

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with WindowListener {
  static const _defaultFraction = 0.15;
  static const _maxFraction = 0.50;
  static const _minPixels = 180.0;

  double _sidebarFraction = _defaultFraction;

  @override
  void initState() {
    super.initState();
    if (_isDesktop) windowManager.addListener(this);
    WidgetsBinding.instance.addPostFrameCallback((_) => _checkFirstRun());
  }

  Future<void> _checkFirstRun() async {
    final settings = context.read<AppState>().settings;
    if (!settings.setupComplete) {
      final completed = await showSetupWizard(context);
      if (completed && mounted && !settings.onboardingComplete) {
        _startOnboardingTour();
      }
    } else if (!settings.onboardingComplete) {
      _startOnboardingTour();
    }
  }

  void _startOnboardingTour() {
    showOnboardingOverlay(context);
  }

  @override
  void dispose() {
    if (_isDesktop) windowManager.removeListener(this);
    super.dispose();
  }

  @override
  void onWindowFocus() {
    // When the window regains focus, clear stale keyboard modifier state.
    // Without this, modifier keys (Alt, Ctrl, etc.) get "stuck" if the user
    // switched away while holding them (e.g. Alt+Tab, Alt+Win).
    // ignore: invalid_use_of_visible_for_testing_member
    HardwareKeyboard.instance.clearState();
  }

  @override
  Widget build(BuildContext context) {
    if (!_isDesktop) return _buildNonDesktop(context);

    final desktop = HotkeyListener(
      child: LayoutBuilder(
        builder: (context, constraints) {
          final isWide = constraints.maxWidth > 700;
          final totalWidth = constraints.maxWidth;
          final sidebarWidth = (_sidebarFraction * totalWidth).clamp(
            _minPixels,
            totalWidth * _maxFraction,
          );

          if (isWide) {
            return Scaffold(
              body: Consumer<AppState>(
                builder: (context, appState, _) {
                  final showSidebar = appState.sidebarVisible;
                  return Column(
                    children: [
                      const CustomTitleBar(),
                      Expanded(
                        child: Row(
                          children: [
                            if (showSidebar) ...[
                              SizedBox(
                                key: onboarding.sidebarKey,
                                width: sidebarWidth,
                                child: const SessionListPanel(),
                              ),
                              _SidebarDivider(
                                onDrag: (dx) {
                                  setState(() {
                                    final newWidth = (sidebarWidth + dx).clamp(
                                      _minPixels,
                                      totalWidth * _maxFraction,
                                    );
                                    _sidebarFraction = newWidth / totalWidth;
                                  });
                                },
                              ),
                            ],
                            Expanded(
                              key: onboarding.mainContentKey,
                              child: Builder(
                                builder: (context) {
                                  final root = appState.splitRoot;
                                  if (root == null) {
                                    return _WelcomePane(appState: appState);
                                  }
                                  return SplitView(node: root);
                                },
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  );
                },
              ),
            );
          }

          // Desktop narrow: no sidebar, single pane
          return Scaffold(
            body: Consumer<AppState>(
              builder: (context, appState, _) {
                return Column(
                  children: [
                    const CustomTitleBar(),
                    Expanded(
                      child: Builder(
                        builder: (context) {
                          final root = appState.splitRoot;
                          if (root == null) {
                            return _WelcomePane(appState: appState);
                          }
                          return SplitView(node: root);
                        },
                      ),
                    ),
                  ],
                );
              },
            ),
          );
        },
      ),
    );

    if (Platform.isMacOS) {
      return PlatformMenuBar(menus: _buildMacOSMenus(context), child: desktop);
    }
    return desktop;
  }

  /// Builds the native macOS menu bar entries, wired to app actions.
  ///
  /// Uses [PlatformMenuItemGroup] to create visual separator sections —
  /// groups are automatically surrounded by dividers on macOS.
  List<PlatformMenuItem> _buildMacOSMenus(BuildContext context) {
    return [
      // ── RCFlow (app) menu ──────────────────────────────────────────────
      PlatformMenu(
        label: 'RCFlow',
        menus: [
          PlatformMenuItemGroup(
            members: [
              const PlatformProvidedMenuItem(
                type: PlatformProvidedMenuItemType.about,
              ),
            ],
          ),
          PlatformMenuItemGroup(
            members: [
              PlatformMenuItem(
                label: 'Settings…',
                shortcut: const SingleActivator(
                  LogicalKeyboardKey.comma,
                  meta: true,
                ),
                onSelected: () => showSettingsMenu(context),
              ),
            ],
          ),
          PlatformMenuItemGroup(
            members: [
              const PlatformProvidedMenuItem(
                type: PlatformProvidedMenuItemType.servicesSubmenu,
              ),
            ],
          ),
          PlatformMenuItemGroup(
            members: [
              const PlatformProvidedMenuItem(
                type: PlatformProvidedMenuItemType.hide,
              ),
              const PlatformProvidedMenuItem(
                type: PlatformProvidedMenuItemType.hideOtherApplications,
              ),
              const PlatformProvidedMenuItem(
                type: PlatformProvidedMenuItemType.showAllApplications,
              ),
            ],
          ),
          if (PlatformProvidedMenuItem.hasMenu(
            PlatformProvidedMenuItemType.quit,
          ))
            PlatformMenuItemGroup(
              members: [
                const PlatformProvidedMenuItem(
                  type: PlatformProvidedMenuItemType.quit,
                ),
              ],
            ),
        ],
      ),
      // ── File menu ──────────────────────────────────────────────────────
      PlatformMenu(
        label: 'File',
        menus: [
          PlatformMenuItemGroup(
            members: [
              PlatformMenuItem(
                label: 'New Session',
                shortcut: const SingleActivator(
                  LogicalKeyboardKey.keyN,
                  meta: true,
                ),
                onSelected: () =>
                    _newSessionFromMenu(context, context.read<AppState>()),
              ),
            ],
          ),
          PlatformMenuItemGroup(
            members: [
              PlatformMenuItem(
                label: 'Split Right',
                shortcut: const SingleActivator(
                  LogicalKeyboardKey.backslash,
                  meta: true,
                ),
                onSelected: () {
                  final appState = context.read<AppState>();
                  if (!appState.hasNoPanes) {
                    appState.splitPane(
                      appState.activePaneId,
                      SplitAxis.horizontal,
                    );
                  }
                },
              ),
              PlatformMenuItem(
                label: 'Split Down',
                shortcut: const SingleActivator(
                  LogicalKeyboardKey.backslash,
                  meta: true,
                  shift: true,
                ),
                onSelected: () {
                  final appState = context.read<AppState>();
                  if (!appState.hasNoPanes) {
                    appState.splitPane(
                      appState.activePaneId,
                      SplitAxis.vertical,
                    );
                  }
                },
              ),
            ],
          ),
          PlatformMenuItemGroup(
            members: [
              PlatformMenuItem(
                label: 'Close Pane',
                shortcut: const SingleActivator(
                  LogicalKeyboardKey.keyW,
                  meta: true,
                ),
                onSelected: () {
                  final appState = context.read<AppState>();
                  if (!appState.hasNoPanes) {
                    appState.closePane(appState.activePaneId);
                  }
                },
              ),
              PlatformMenuItem(
                label: 'Reopen Closed Pane',
                shortcut: const SingleActivator(
                  LogicalKeyboardKey.keyT,
                  meta: true,
                  shift: true,
                ),
                onSelected: () =>
                    context.read<AppState>().reopenLastClosedPane(),
              ),
            ],
          ),
          PlatformMenuItemGroup(
            members: [
              PlatformMenuItem(
                label: 'Refresh Sessions',
                shortcut: const SingleActivator(
                  LogicalKeyboardKey.keyR,
                  meta: true,
                ),
                onSelected: () => context.read<AppState>().refreshSessions(),
              ),
            ],
          ),
        ],
      ),
      // ── View menu ──────────────────────────────────────────────────────
      PlatformMenu(
        label: 'View',
        menus: [
          PlatformMenuItemGroup(
            members: [
              PlatformMenuItem(
                label: 'Toggle Sidebar',
                shortcut: const SingleActivator(
                  LogicalKeyboardKey.keyB,
                  meta: true,
                ),
                onSelected: () => context.read<AppState>().toggleSidebar(),
              ),
            ],
          ),
          if (PlatformProvidedMenuItem.hasMenu(
            PlatformProvidedMenuItemType.toggleFullScreen,
          ))
            PlatformMenuItemGroup(
              members: [
                const PlatformProvidedMenuItem(
                  type: PlatformProvidedMenuItemType.toggleFullScreen,
                ),
              ],
            ),
        ],
      ),
      // ── Window menu ────────────────────────────────────────────────────
      PlatformMenu(
        label: 'Window',
        menus: [
          PlatformMenuItemGroup(
            members: [
              const PlatformProvidedMenuItem(
                type: PlatformProvidedMenuItemType.minimizeWindow,
              ),
              const PlatformProvidedMenuItem(
                type: PlatformProvidedMenuItemType.zoomWindow,
              ),
            ],
          ),
          PlatformMenuItemGroup(
            members: [
              const PlatformProvidedMenuItem(
                type: PlatformProvidedMenuItemType.arrangeWindowsInFront,
              ),
            ],
          ),
        ],
      ),
    ];
  }

  void _newSessionFromMenu(BuildContext context, AppState appState) {
    final connectedWorkers = appState.workerConfigs.where((c) {
      final w = appState.getWorker(c.id);
      return w?.isConnected ?? false;
    }).toList();

    if (connectedWorkers.length == 1) {
      final pane = appState.ensureChatPane();
      pane.setTargetWorker(connectedWorkers.first.id);
      pane.startNewChat();
      appState.requestInputFocus();
      return;
    }

    showWorkerPickerDialog(context).then((workerId) {
      if (workerId != null && context.mounted) {
        final pane = appState.ensureChatPane();
        pane.setTargetWorker(workerId);
        pane.startNewChat();
        appState.requestInputFocus();
      }
    });
  }

  Widget _buildNonDesktop(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final isWide = constraints.maxWidth > 700;
        final totalWidth = constraints.maxWidth;
        final sidebarWidth = (_sidebarFraction * totalWidth).clamp(
          _minPixels,
          totalWidth * _maxFraction,
        );

        return Scaffold(
          appBar: ConnectionBar(
            onSessionsTap: isWide ? null : () => showSessionSheet(context),
            showSessionsButton: !isWide,
            showSettingsButton: !isWide,
          ),
          body: isWide
              ? Row(
                  children: [
                    SizedBox(
                      width: sidebarWidth,
                      child: const SessionListPanel(),
                    ),
                    _SidebarDivider(
                      onDrag: (dx) {
                        setState(() {
                          final newWidth = (sidebarWidth + dx).clamp(
                            _minPixels,
                            totalWidth * _maxFraction,
                          );
                          _sidebarFraction = newWidth / totalWidth;
                        });
                      },
                    ),
                    Expanded(
                      child: Consumer<AppState>(
                        builder: (context, appState, _) {
                          final root = appState.splitRoot;
                          if (root == null) {
                            return _WelcomePane(appState: appState);
                          }
                          return SplitView(node: root);
                        },
                      ),
                    ),
                  ],
                )
              : Consumer<AppState>(
                  builder: (context, appState, _) {
                    if (appState.hasNoPanes) {
                      return _WelcomePane(appState: appState);
                    }
                    return ChangeNotifierProvider<PaneState>.value(
                      value: appState.activePane,
                      child: const Column(
                        children: [
                          Expanded(child: OutputDisplay()),
                          InputArea(),
                        ],
                      ),
                    );
                  },
                ),
        );
      },
    );
  }
}

/// Shown when all panes have been closed. Provides a way to create a new pane.
class _WelcomePane extends StatelessWidget {
  final AppState appState;

  const _WelcomePane({required this.appState});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.chat_bubble_outline_rounded,
            size: 48,
            color: context.appColors.textMuted,
          ),
          const SizedBox(height: 16),
          Text(
            'No open panes',
            style: TextStyle(
              color: context.appColors.textSecondary,
              fontSize: 18,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 8),
          Text(
            'Start a new chat or select a session from the sidebar',
            style: TextStyle(color: context.appColors.textMuted, fontSize: 14),
          ),
          const SizedBox(height: 24),
          FilledButton.icon(
            style: FilledButton.styleFrom(
              backgroundColor: context.appColors.accent,
              padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 12),
            ),
            icon: const Icon(Icons.add_rounded, color: Colors.white),
            label: const Text(
              'New Chat',
              style: TextStyle(color: Colors.white, fontSize: 14),
            ),
            onPressed: () {
              appState.createNewPane();
            },
          ),
        ],
      ),
    );
  }
}

class _SidebarDivider extends StatefulWidget {
  final ValueChanged<double> onDrag;

  const _SidebarDivider({required this.onDrag});

  @override
  State<_SidebarDivider> createState() => _SidebarDividerState();
}

class _SidebarDividerState extends State<_SidebarDivider> {
  bool _hovering = false;
  bool _dragging = false;

  @override
  Widget build(BuildContext context) {
    final highlighted = _hovering || _dragging;

    return MouseRegion(
      cursor: SystemMouseCursors.resizeColumn,
      onEnter: (_) => setState(() => _hovering = true),
      onExit: (_) => setState(() => _hovering = false),
      child: GestureDetector(
        onPanStart: (_) => setState(() => _dragging = true),
        onPanEnd: (_) => setState(() => _dragging = false),
        onPanCancel: () => setState(() => _dragging = false),
        onPanUpdate: (details) => widget.onDrag(details.delta.dx),
        child: Container(
          width: 6,
          color: Colors.transparent,
          child: Center(
            child: AnimatedContainer(
              duration: const Duration(milliseconds: 150),
              width: highlighted ? 3 : 1,
              height: double.infinity,
              color: highlighted
                  ? context.appColors.accent.withAlpha(180)
                  : context.appColors.divider,
            ),
          ),
        ),
      ),
    );
  }
}
