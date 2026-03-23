import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';
import 'package:window_manager/window_manager.dart';

import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';
import '../widgets/connection_bar.dart';
import '../widgets/custom_title_bar.dart';
import '../widgets/hotkey_listener.dart';
import '../widgets/input_area.dart';
import '../widgets/output_display.dart';
import '../widgets/session_panel.dart';
import '../widgets/split_view.dart';

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

    return HotkeyListener(
      child: LayoutBuilder(
        builder: (context, constraints) {
          final isWide = constraints.maxWidth > 700;
          final totalWidth = constraints.maxWidth;
          final sidebarWidth = (_sidebarFraction * totalWidth)
              .clamp(_minPixels, totalWidth * _maxFraction);

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
                                width: sidebarWidth,
                                child: const SessionListPanel(),
                              ),
                              _SidebarDivider(
                                onDrag: (dx) {
                                  setState(() {
                                    final newWidth = (sidebarWidth + dx).clamp(
                                        _minPixels,
                                        totalWidth * _maxFraction);
                                    _sidebarFraction = newWidth / totalWidth;
                                  });
                                },
                              ),
                            ],
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
  }

  Widget _buildNonDesktop(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final isWide = constraints.maxWidth > 700;
        final totalWidth = constraints.maxWidth;
        final sidebarWidth = (_sidebarFraction * totalWidth)
            .clamp(_minPixels, totalWidth * _maxFraction);

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
                          final newWidth = (sidebarWidth + dx)
                              .clamp(_minPixels, totalWidth * _maxFraction);
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
            style: TextStyle(
              color: context.appColors.textMuted,
              fontSize: 14,
            ),
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
