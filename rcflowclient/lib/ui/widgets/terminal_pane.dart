import 'dart:async';
import 'dart:convert';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:xterm/xterm.dart';

import '../../services/terminal_service.dart';
import '../../state/app_state.dart';
import '../../theme.dart';
import 'terminal_themes.dart';

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
  })  : createdAt = DateTime.now(),
        terminal = Terminal(maxLines: maxLines),
        controller = TerminalController();

  String get shortId => terminalId.length >= 8
      ? '${terminalId.substring(0, 8)}...'
      : terminalId;
}

/// Legacy alias kept for backward compatibility with AppState._terminalPanes.
/// TODO: remove once all references are migrated.
typedef TerminalPaneInfo = TerminalSessionInfo;

/// A pane that hosts an interactive terminal session.
///
/// Receives a [TerminalSessionInfo] from AppState rather than creating its own
/// Terminal/Controller, so the terminal buffer survives pane close/reopen.
class TerminalPane extends StatefulWidget {
  final String paneId;
  final TerminalSessionInfo info;
  final AppState appState;

  const TerminalPane({
    super.key,
    required this.paneId,
    required this.info,
    required this.appState,
  });

  @override
  State<TerminalPane> createState() => _TerminalPaneState();
}

class _TerminalPaneState extends State<TerminalPane> {
  late FocusNode _focusNode;

  TerminalSessionInfo get _info => widget.info;
  Terminal get _terminal => _info.terminal;
  TerminalController get _terminalController => _info.controller;

  @override
  void initState() {
    super.initState();
    _focusNode = FocusNode(debugLabel: 'TerminalPane-${widget.paneId}');

    // Set up output handler (idempotent — safe to call on reattach).
    _terminal.onOutput = (data) {
      final service = _getTerminalService();
      if (service != null && _info.connected) {
        service.sendInput(
          _info.terminalId,
          Uint8List.fromList(utf8.encode(data)),
        );
      }
    };

    _terminal.onResize = (cols, rows, pixelWidth, pixelHeight) {
      if (cols != _info.lastCols || rows != _info.lastRows) {
        _info.lastCols = cols;
        _info.lastRows = rows;
        _sendResize(cols, rows);
      }
    };

    // If not yet connected to server, start the connection.
    // If already connected (reattach), just re-register for output.
    if (!_info.connected && !_info.ended) {
      _connectTerminal();
    } else if (_info.connected) {
      _reattach();
    }
  }

  TerminalService? _getTerminalService() {
    final worker = widget.appState.getWorker(_info.workerId);
    return worker?.terminalService;
  }

  /// Re-register for output on an already-connected terminal (reattach).
  void _reattach() {
    final service = _getTerminalService();
    if (service == null) return;

    final outputController = service.registerTerminal(_info.terminalId);
    _info.outputSub?.cancel();
    _info.outputSub = outputController.stream.listen((data) {
      _terminal.write(utf8.decode(data, allowMalformed: true));
    });

    _info.controlSub?.cancel();
    _info.controlSub = service.controlMessages.listen((msg) {
      if (msg['terminal_id'] != _info.terminalId) return;
      _handleControlMessage(msg);
    });
  }

  Future<void> _connectTerminal() async {
    final worker = widget.appState.getWorker(_info.workerId);
    if (worker == null) return;

    try {
      await worker.ensureTerminalConnected();
    } catch (e) {
      _terminal.write('\r\n\x1b[31m[Failed to connect: $e]\x1b[0m\r\n');
      return;
    }

    final service = worker.terminalService;

    final outputController = service.registerTerminal(_info.terminalId);
    _info.outputSub?.cancel();
    _info.outputSub = outputController.stream.listen((data) {
      _terminal.write(utf8.decode(data, allowMalformed: true));
    });

    _info.controlSub?.cancel();
    _info.controlSub = service.controlMessages.listen((msg) {
      if (msg['terminal_id'] != _info.terminalId) return;
      _handleControlMessage(msg);
    });

    service.sendControl({
      'type': 'create',
      'terminal_id': _info.terminalId,
      'cols': 80,
      'rows': 24,
    });
  }

  void _handleControlMessage(Map<String, dynamic> msg) {
    final type = msg['type'] as String?;
    if (type == 'created') {
      _info.connected = true;
      // Send actual terminal dimensions now that we're connected.
      // The initial create message uses defaults (80x24), and the xterm
      // widget's onResize may have fired before connected was true (dropping
      // the resize). Sync the real size so programs like "ls" see the
      // correct column width.
      if (_info.lastCols > 0 && _info.lastRows > 0) {
        _sendResize(_info.lastCols, _info.lastRows);
      }
      if (mounted) setState(() {});
    } else if (type == 'closed') {
      _info.connected = false;
      _info.ended = true;
      if (mounted) setState(() {});
      final reason = msg['reason'] as String? ?? 'unknown';
      final exitCode = msg['exit_code'];
      _terminal.write(
        '\r\n\x1b[2m[Terminal session ended: $reason'
        '${exitCode != null ? ' (exit code $exitCode)' : ''}]\x1b[0m\r\n',
      );
    } else if (type == 'error') {
      _terminal.write(
        '\r\n\x1b[31m[Error: ${msg['message']}]\x1b[0m\r\n',
      );
    }
  }

  void _sendResize(int cols, int rows) {
    final service = _getTerminalService();
    if (service != null && _info.connected) {
      service.sendControl({
        'type': 'resize',
        'terminal_id': _info.terminalId,
        'cols': cols,
        'rows': rows,
      });
    }
  }

  @override
  void didUpdateWidget(TerminalPane oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.appState.activePaneId == widget.paneId && !_focusNode.hasFocus) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && !_focusNode.hasFocus) {
          _focusNode.requestFocus();
        }
      });
    }
  }

  @override
  void dispose() {
    _focusNode.dispose();

    // Only unregister from output stream — do NOT send close command.
    // The PTY stays alive server-side. It's only killed when
    // AppState.closeTerminalSession() is called.
    _info.outputSub?.cancel();
    _info.outputSub = null;
    _info.controlSub?.cancel();
    _info.controlSub = null;

    final service = _getTerminalService();
    service?.unregisterTerminal(_info.terminalId);

    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final settings = widget.appState.settings;
    final schemeName = settings.terminalColorScheme;
    final theme =
        terminalColorSchemes[schemeName] ?? terminalColorSchemes['default']!;

    final cursorType = switch (settings.terminalCursorStyle) {
      'underline' => TerminalCursorType.underline,
      'bar' => TerminalCursorType.verticalBar,
      _ => TerminalCursorType.block,
    };

    final isDesktop = defaultTargetPlatform == TargetPlatform.linux ||
        defaultTargetPlatform == TargetPlatform.macOS ||
        defaultTargetPlatform == TargetPlatform.windows;

    return Container(
      color: theme.background,
      child: TerminalView(
        _terminal,
        controller: _terminalController,
        focusNode: _focusNode,
        theme: theme,
        textStyle: TerminalStyle(
          fontSize: settings.terminalFontSize,
          fontFamily: settings.terminalFontFamily,
        ),
        cursorType: cursorType,
        autofocus: true,
        hardwareKeyboardOnly: isDesktop,
        onSecondaryTapDown: (details, offset) {
          _showContextMenu(context, details.globalPosition);
        },
      ),
    );
  }

  void _showContextMenu(BuildContext context, Offset position) {
    final overlay =
        Overlay.of(context).context.findRenderObject() as RenderBox;
    showMenu<String>(
      context: context,
      position: RelativeRect.fromRect(
        position & const Size(1, 1),
        Offset.zero & overlay.size,
      ),
      color: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      items: [
        PopupMenuItem(
          value: 'copy',
          child: Row(
            children: [
              Icon(Icons.copy_rounded,
                  color: context.appColors.textSecondary, size: 18),
              const SizedBox(width: 8),
              Text('Copy',
                  style: TextStyle(color: context.appColors.textPrimary)),
            ],
          ),
        ),
        PopupMenuItem(
          value: 'paste',
          child: Row(
            children: [
              Icon(Icons.paste_rounded,
                  color: context.appColors.textSecondary, size: 18),
              const SizedBox(width: 8),
              Text('Paste',
                  style: TextStyle(color: context.appColors.textPrimary)),
            ],
          ),
        ),
      ],
    ).then((value) async {
      if (value == 'copy') {
        final selected = _terminalController.selection;
        if (selected != null) {
          final text = _terminal.buffer.getText(selected);
          await Clipboard.setData(ClipboardData(text: text));
        }
      } else if (value == 'paste') {
        final data = await Clipboard.getData(Clipboard.kTextPlain);
        if (data?.text != null && data!.text!.isNotEmpty) {
          final service = _getTerminalService();
          if (service != null && _info.connected) {
            service.sendInput(
              _info.terminalId,
              Uint8List.fromList(utf8.encode(data.text!)),
            );
          }
        }
      }
    });
  }
}
