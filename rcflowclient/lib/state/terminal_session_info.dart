import 'dart:async';
import 'dart:typed_data';

import 'package:xterm/xterm.dart';

/// Persistent terminal session tracked by AppState.
///
/// Holds the xterm [Terminal] and [TerminalController] objects so they survive
/// pane close/reopen. The server-side PTY stays alive when the pane is closed;
/// only killed when [closeTerminalSession] is called.
class TerminalSessionInfo {
  final String terminalId;
  final String workerId;
  final DateTime createdAt;

  /// Display name (user-renamable).
  String title;

  /// Which pane is currently showing this terminal (null if hidden).
  String? paneId;

  /// xterm objects that survive pane lifecycle.
  final Terminal terminal;
  final TerminalController controller;

  /// Whether the server-side PTY has been created.
  bool connected = false;

  /// Whether the server-side PTY has ended.
  bool ended = false;

  /// Subscriptions for when the terminal is attached to a pane.
  StreamSubscription<Uint8List>? outputSub;
  StreamSubscription<Map<String, dynamic>>? controlSub;

  int lastCols = 0;
  int lastRows = 0;

  TerminalSessionInfo({
    required this.terminalId,
    required this.workerId,
    required this.title,
    required int maxLines,
    this.paneId,
  }) : createdAt = DateTime.now(),
       terminal = Terminal(maxLines: maxLines),
       controller = TerminalController();

  String get shortId =>
      terminalId.length >= 8 ? '${terminalId.substring(0, 8)}...' : terminalId;
}

/// Legacy alias kept for backward compatibility with AppState._terminalPanes.
/// TODO: remove once all references are migrated.
typedef TerminalPaneInfo = TerminalSessionInfo;
