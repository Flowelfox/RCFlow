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
  static const _minPixels = 150.0;

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
    return LayoutBuilder(
      builder: (context, constraints) {
        final isWide = constraints.maxWidth > 700;
        final totalWidth = constraints.maxWidth;
        final sidebarWidth = (_sidebarFraction * totalWidth)
            .clamp(_minPixels, totalWidth * _maxFraction);

        if (isWide && _isDesktop) {
          return Scaffold(
            body: Column(
              children: [
                const CustomTitleBar(),
                Expanded(
                  child: Row(
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
                            return SplitView(node: appState.splitRoot);
                          },
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          );
        }

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
                          return SplitView(node: appState.splitRoot);
                        },
                      ),
                    ),
                  ],
                )
              : ChangeNotifierProvider<PaneState>.value(
                  value: context.read<AppState>().activePane,
                  child: const Column(
                    children: [
                      Expanded(child: OutputDisplay()),
                      InputArea(),
                    ],
                  ),
                ),
        );
      },
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
              color: highlighted ? kAccent.withAlpha(180) : kDivider,
            ),
          ),
        ),
      ),
    );
  }
}
