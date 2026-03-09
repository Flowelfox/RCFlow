import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../models/hotkey_binding.dart';
import '../../models/split_tree.dart';
import '../../state/app_state.dart';
import 'settings_menu.dart';
import 'worker_picker_dialog.dart';

/// Wraps the main content area and intercepts global hotkeys via
/// [HardwareKeyboard] so they work even when a terminal pane has focus.
class HotkeyListener extends StatefulWidget {
  final Widget child;
  const HotkeyListener({super.key, required this.child});

  @override
  State<HotkeyListener> createState() => _HotkeyListenerState();
}

class _HotkeyListenerState extends State<HotkeyListener> {
  @override
  void initState() {
    super.initState();
    // HardwareKeyboard handlers fire BEFORE the focus tree, so hotkeys
    // work even when a TextField or terminal has focus.
    HardwareKeyboard.instance.addHandler(_hardwareKeyHandler);
  }

  @override
  void dispose() {
    HardwareKeyboard.instance.removeHandler(_hardwareKeyHandler);
    super.dispose();
  }

  bool _hardwareKeyHandler(KeyEvent event) {
    if (event is! KeyDownEvent) return false;

    // Don't handle hotkeys when a dialog/overlay is open
    if (!mounted) return false;
    final nav = Navigator.maybeOf(context, rootNavigator: true);
    if (nav != null && nav.canPop()) return false;

    final appState = context.read<AppState>();
    final action = appState.hotkeyService.match(
      event,
      HardwareKeyboard.instance.logicalKeysPressed,
    );
    if (action == null) return false;

    _executeAction(action, appState);
    return true; // consumed — prevents child widgets from seeing it
  }

  bool _executeAction(HotkeyAction action, AppState appState) {
    switch (action) {
      case HotkeyAction.closePane:
        if (!appState.hasNoPanes) {
          appState.closePane(appState.activePaneId);
        }
        return true;

      case HotkeyAction.newSession:
        _showWorkerPicker(appState);
        return true;

      case HotkeyAction.openSettings:
        showSettingsMenu(context);
        return true;

      case HotkeyAction.focusPaneLeft:
        appState.focusAdjacentPane(AxisDirection.left);
        return true;

      case HotkeyAction.focusPaneRight:
        appState.focusAdjacentPane(AxisDirection.right);
        return true;

      case HotkeyAction.focusPaneUp:
        appState.focusAdjacentPane(AxisDirection.up);
        return true;

      case HotkeyAction.focusPaneDown:
        appState.focusAdjacentPane(AxisDirection.down);
        return true;

      case HotkeyAction.toggleSidebar:
        appState.toggleSidebar();
        return true;

      case HotkeyAction.nextPane:
        appState.cyclePaneFocus(forward: true);
        return true;

      case HotkeyAction.previousPane:
        appState.cyclePaneFocus(forward: false);
        return true;

      case HotkeyAction.splitRight:
        if (!appState.hasNoPanes) {
          appState.splitPane(appState.activePaneId, SplitAxis.horizontal);
        }
        return true;

      case HotkeyAction.splitDown:
        if (!appState.hasNoPanes) {
          appState.splitPane(appState.activePaneId, SplitAxis.vertical);
        }
        return true;

      case HotkeyAction.openTerminal:
        final wid = appState.defaultWorkerId;
        if (wid != null) appState.openTerminal(wid);
        return true;

      case HotkeyAction.refreshSessions:
        appState.refreshSessions();
        return true;

      case HotkeyAction.focusInputArea:
        appState.requestInputFocus();
        return true;

      case HotkeyAction.reopenLastClosedPane:
        appState.reopenLastClosedPane();
        return true;
    }
  }

  void _showWorkerPicker(AppState appState) {
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

  @override
  Widget build(BuildContext context) {
    return widget.child;
  }
}
