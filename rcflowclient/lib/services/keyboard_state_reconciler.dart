import 'dart:ffi';
import 'dart:io' show Platform;

import 'package:flutter/services.dart';

// Win32 GetAsyncKeyState via dart:ffi. Used to detect modifier-key state
// drift between Flutter's HardwareKeyboard tracker and the OS — happens
// when a global keyboard hook (e.g. Wispr Flow's activation hotkey) eats
// the Ctrl-up event before Flutter sees it, leaving Ctrl "stuck".
typedef _GetAsyncKeyStateNative = Int16 Function(Int32 vKey);
typedef _GetAsyncKeyStateDart = int Function(int vKey);

class KeyboardStateReconciler {
  static const int _vkShift = 0x10;
  static const int _vkControl = 0x11;
  static const int _vkMenu = 0x12; // Alt
  static const int _vkLWin = 0x5B;
  static const int _vkRWin = 0x5C;

  static _GetAsyncKeyStateDart? _getAsyncKeyState;

  static void _ensureLoaded() {
    if (_getAsyncKeyState != null) return;
    if (!Platform.isWindows) return;
    final user32 = DynamicLibrary.open('user32.dll');
    _getAsyncKeyState = user32
        .lookup<NativeFunction<_GetAsyncKeyStateNative>>('GetAsyncKeyState')
        .asFunction<_GetAsyncKeyStateDart>();
  }

  static bool _osDown(int vk) {
    final fn = _getAsyncKeyState;
    if (fn == null) return false;
    // High bit of result = currently pressed.
    return (fn(vk) & 0x8000) != 0;
  }

  /// Clears Flutter's tracked modifier state if it diverges from the OS
  /// (modifier marked pressed in Flutter but released per OS). No-op on
  /// non-Windows platforms.
  static void reconcile() {
    if (!Platform.isWindows) return;
    _ensureLoaded();
    final hk = HardwareKeyboard.instance;
    final ctrlDrift = hk.isControlPressed && !_osDown(_vkControl);
    final altDrift = hk.isAltPressed && !_osDown(_vkMenu);
    final shiftDrift = hk.isShiftPressed && !_osDown(_vkShift);
    final metaDrift = hk.isMetaPressed &&
        !_osDown(_vkLWin) &&
        !_osDown(_vkRWin);
    if (ctrlDrift || altDrift || shiftDrift || metaDrift) {
      // ignore: invalid_use_of_visible_for_testing_member
      hk.clearState();
    }
  }
}

