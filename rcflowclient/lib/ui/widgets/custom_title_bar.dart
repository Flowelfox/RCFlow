import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:window_manager/window_manager.dart';

import '../../state/app_state.dart';
import '../../theme.dart';

bool get _isDesktop =>
    Platform.isWindows || Platform.isLinux || Platform.isMacOS;

class CustomTitleBar extends StatelessWidget {
  const CustomTitleBar({super.key});

  @override
  Widget build(BuildContext context) {
    if (!_isDesktop) return const SizedBox.shrink();

    final connected = context.select<AppState, bool>((s) => s.connected);
    final connecting = context.select<AppState, bool>((s) => s.connecting);
    final allConnected =
        context.select<AppState, bool>((s) => s.allConnected);


    return Container(
      height: 40,
      decoration: BoxDecoration(
        color: context.appColors.bgBase,
        border: Border(bottom: BorderSide(color: context.appColors.divider, width: 1)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Draggable title area with status dot + "RCFlow"
          Expanded(
            child: GestureDetector(
              onDoubleTap: () async {
                if (await windowManager.isMaximized()) {
                  windowManager.unmaximize();
                } else {
                  windowManager.maximize();
                }
              },
              child: DragToMoveArea(
                child: Padding(
                  // On macOS, leave space for native traffic-light buttons on the left
                  padding: EdgeInsets.only(
                    left: Platform.isMacOS ? 72 : 16,
                    right: Platform.isMacOS ? 16 : 0,
                  ),
                  child: Row(
                    mainAxisAlignment: Platform.isMacOS
                        ? MainAxisAlignment.end
                        : MainAxisAlignment.start,
                    children: [
                      _StatusIndicator(
                        connected: connected,
                        connecting: connecting,
                        allConnected: allConnected,
                      ),
                      const SizedBox(width: 10),
                      Text(
                        'RCFlow',
                        style: TextStyle(
                          fontSize: 15,
                          fontWeight: FontWeight.w600,
                          color: connecting ? context.appColors.textSecondary : context.appColors.textPrimary,
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          ),

          // Window control buttons (hidden on macOS — native traffic lights used instead)
          if (!Platform.isMacOS) const _WindowControls(),
        ],
      ),
    );
  }
}

class _StatusIndicator extends StatelessWidget {
  final bool connected;
  final bool connecting;
  final bool allConnected;

  const _StatusIndicator({
    required this.connected,
    required this.connecting,
    this.allConnected = false,
  });

  @override
  Widget build(BuildContext context) {
    if (connecting) {
      return SizedBox(
        width: 12,
        height: 12,
        child: CircularProgressIndicator(strokeWidth: 1.5, color: context.appColors.accentLight),
      );
    }
    // Green: all workers connected, Amber: partial, Red: none
    final Color dotColor;
    if (!connected) {
      dotColor = context.appColors.errorText;
    } else if (allConnected) {
      dotColor = context.appColors.successText;
    } else {
      dotColor = context.appColors.toolAccent; // amber — partial
    }
    return Container(
      width: 8,
      height: 8,
      decoration: BoxDecoration(
        shape: BoxShape.circle,
        color: dotColor,
        boxShadow: [
          BoxShadow(
            color: dotColor.withAlpha(80),
            blurRadius: 4,
            spreadRadius: 1,
          ),
        ],
      ),
    );
  }
}


class _WindowControls extends StatelessWidget {
  const _WindowControls();

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _WindowButton(
          icon: _MinimizeIcon(),
          onPressed: () => windowManager.minimize(),
        ),
        _MaximizeButton(),
        _WindowButton(
          icon: Icon(Icons.close, size: 16, color: context.appColors.textSecondary),
          hoverColor: context.appColors.errorText,
          hoverIconColor: Colors.white,
          onPressed: () => windowManager.close(),
        ),
      ],
    );
  }
}

class _MaximizeButton extends StatefulWidget {
  @override
  State<_MaximizeButton> createState() => _MaximizeButtonState();
}

class _MaximizeButtonState extends State<_MaximizeButton> with WindowListener {
  bool _isMaximized = false;

  @override
  void initState() {
    super.initState();
    windowManager.addListener(this);
    _checkMaximized();
  }

  @override
  void dispose() {
    windowManager.removeListener(this);
    super.dispose();
  }

  Future<void> _checkMaximized() async {
    final maximized = await windowManager.isMaximized();
    if (mounted && maximized != _isMaximized) {
      setState(() => _isMaximized = maximized);
    }
  }

  @override
  void onWindowMaximize() {
    setState(() => _isMaximized = true);
  }

  @override
  void onWindowUnmaximize() {
    setState(() => _isMaximized = false);
  }

  @override
  Widget build(BuildContext context) {
    return _WindowButton(
      icon: _isMaximized
          ? const _RestoreIcon()
          : const _MaximizeIcon(),
      onPressed: () async {
        if (await windowManager.isMaximized()) {
          windowManager.unmaximize();
        } else {
          windowManager.maximize();
        }
      },
    );
  }
}

class _WindowButton extends StatefulWidget {
  final Widget icon;
  final VoidCallback onPressed;
  final Color? hoverColor;
  final Color? hoverIconColor;

  const _WindowButton({
    required this.icon,
    required this.onPressed,
    this.hoverColor,
    this.hoverIconColor,
  });

  @override
  State<_WindowButton> createState() => _WindowButtonState();
}

class _WindowButtonState extends State<_WindowButton> {
  bool _hovering = false;

  @override
  Widget build(BuildContext context) {
    final isCloseButton = widget.hoverColor != null;
    return MouseRegion(
      onEnter: (_) => setState(() => _hovering = true),
      onExit: (_) => setState(() => _hovering = false),
      child: GestureDetector(
        onTap: widget.onPressed,
        child: AnimatedContainer(
          duration: Duration(milliseconds: 100),
          width: 46,
          color: _hovering
              ? (widget.hoverColor ?? context.appColors.bgOverlay)
              : Colors.transparent,
          child: Center(
            child: _hovering && isCloseButton
                ? IconTheme(
                    data: IconThemeData(
                        color: widget.hoverIconColor ?? context.appColors.textPrimary),
                    child: widget.icon,
                  )
                : widget.icon,
          ),
        ),
      ),
    );
  }
}

class _MinimizeIcon extends StatelessWidget {
  const _MinimizeIcon();

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 16,
      height: 16,
      child: Center(
        child: Container(width: 10, height: 1, color: context.appColors.textSecondary),
      ),
    );
  }
}

class _MaximizeIcon extends StatelessWidget {
  const _MaximizeIcon();

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 16,
      height: 16,
      child: Center(
        child: Container(
          width: 10,
          height: 10,
          decoration: BoxDecoration(
            border: Border.all(color: context.appColors.textSecondary, width: 1),
          ),
        ),
      ),
    );
  }
}

class _RestoreIcon extends StatelessWidget {
  const _RestoreIcon();

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      width: 16,
      height: 16,
      child: Center(
        child: Stack(
          children: [
            Positioned(
              top: 0,
              right: 0,
              child: Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(
                  border: Border.all(color: context.appColors.textSecondary, width: 1),
                ),
              ),
            ),
            Positioned(
              bottom: 0,
              left: 0,
              child: Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(
                  color: context.appColors.bgBase,
                  border: Border.all(color: context.appColors.textSecondary, width: 1),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
