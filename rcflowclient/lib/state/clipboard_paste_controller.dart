import 'dart:async';

import 'package:flutter/foundation.dart';

import '../services/keyboard_state_reconciler.dart';

/// Detects external (dictation-tool) clipboard writes and surfaces the captured
/// text for the active [InputArea] to insert, owned by [AppState].
///
/// Implements the delay-and-detect dance used by Wispr Flow and similar tools
/// (save clipboard X → write recognized Y → Ctrl+V → restore X), committing Y
/// while dropping the restore event.  Extracted from AppState in the Phase 5
/// step-3 carve; AppState delegates [handleClipboardEvent] and re-exposes
/// [externalPasteRequest] / [externalPasteText].
class ClipboardPasteController {
  static const Duration _pasteHoldoff = Duration(milliseconds: 150);
  static const Duration _lateRestoreWindow = Duration(seconds: 2);

  /// Incremented to signal the active InputArea to insert [externalPasteText].
  final ValueNotifier<int> externalPasteRequest = ValueNotifier(0);
  String? externalPasteText;

  String? _pendingPasteText;
  Timer? _pasteHoldoffTimer;
  String? _lastCommittedText;
  DateTime _lastCommitAt = DateTime.fromMillisecondsSinceEpoch(0);

  /// Single entry point for all clipboard change notifications from the
  /// Win32 runner's polling thread. The runner pre-fills `previousText`
  /// with what was on the clipboard immediately before this change, which
  /// is what restore detection keys off of.
  void handleClipboardEvent({
    required String text,
    required String? previousText,
    required bool isOwn,
    required bool isForeground,
    required bool seqJumped,
  }) {
    // Wispr's hotkey holds Ctrl/Alt/Win via global hook; the release can
    // miss RCFlow entirely. Reconcile modifier state with the OS on every
    // clipboard event so stuck modifiers clear once dictation finishes.
    KeyboardStateReconciler.reconcile();

    if (text.isEmpty) return;
    // Skip our own copies — Flutter's TextField copy menu, RCFlow copy
    // buttons, etc.
    if (isOwn) return;

    // In-window restore: incoming event's previousText is the pending Y,
    // which means the clipboard just transitioned Y → text. That's Wispr
    // restoring its saved clipboard right after writing Y. Commit Y, drop
    // this restore event.
    if (_pendingPasteText != null && previousText == _pendingPasteText) {
      _commitPendingPaste();
      return;
    }

    // Late restore: timer already fired and committed Y. A later event
    // reports a transition from Y → text within the late-restore window —
    // drop it.
    if (previousText != null &&
        previousText == _lastCommittedText &&
        DateTime.now().difference(_lastCommitAt) < _lateRestoreWindow) {
      return;
    }

    // External write — buffer for restore detection. Final input-field-focus
    // gate runs at insertion time in InputArea, so background-app writes
    // while RCFlow happens to be foreground but no input is focused don't
    // hijack the field. We deliberately don't gate on isForeground here:
    // Wispr's hotkey timing can leave RCFlow non-foreground at the moment
    // the clipboard is written.
    if (_pendingPasteText != null) {
      _commitPendingPaste();
    }
    _pendingPasteText = text;
    _pasteHoldoffTimer?.cancel();
    _pasteHoldoffTimer = Timer(_pasteHoldoff, _commitPendingPaste);
  }

  void _commitPendingPaste() {
    _pasteHoldoffTimer?.cancel();
    _pasteHoldoffTimer = null;
    final text = _pendingPasteText;
    if (text == null) return;
    _pendingPasteText = null;
    _lastCommittedText = text;
    _lastCommitAt = DateTime.now();
    externalPasteText = text;
    externalPasteRequest.value++;
  }

  void dispose() {
    _pasteHoldoffTimer?.cancel();
    externalPasteRequest.dispose();
  }
}
