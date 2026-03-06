import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/split_tree.dart';
import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';
import 'input_area.dart';
import 'output_display.dart';
import 'pane_header.dart';
import 'session_panel.dart' show TerminalDragData;
import 'terminal_pane.dart';

/// Data carried during a session drag from the sidebar.
class SessionDragData {
  final String sessionId;
  final String workerId;
  final String label;

  const SessionDragData({
    required this.sessionId,
    required this.workerId,
    required this.label,
  });
}

/// Determine which [DropZone] a point falls in using the diagonal method.
DropZone? _hitTestDropZone(Size size, Offset local) {
  if (size.isEmpty) return null;
  final w = size.width;
  final h = size.height;
  final x = local.dx;
  final y = local.dy;

  // Diagonals: y = (h/w)*x and y = h - (h/w)*x
  final diag1 = (h / w) * x; // top-left → bottom-right
  final diag2 = h - (h / w) * x; // bottom-left → top-right

  if (y < diag1 && y < diag2) return DropZone.top;
  if (y >= diag1 && y >= diag2) return DropZone.bottom;
  if (y >= diag1 && y < diag2) return DropZone.left;
  return DropZone.right;
}

/// A single session pane — wraps OutputDisplay + InputArea with an optional
/// PaneHeader (shown in multi-pane mode). Tap to activate.
class SessionPane extends StatefulWidget {
  final PaneState pane;

  const SessionPane({super.key, required this.pane});

  @override
  State<SessionPane> createState() => _SessionPaneState();
}

class _SessionPaneState extends State<SessionPane> {
  DropZone? _hoverZone;

  @override
  Widget build(BuildContext context) {
    final appState = context.watch<AppState>();
    final isActive = appState.activePaneId == widget.pane.paneId;
    final multiPane = appState.paneCount > 1;

    return ChangeNotifierProvider<PaneState>.value(
      value: widget.pane,
      child: DragTarget<Object>(
        onWillAcceptWithDetails: (details) =>
            details.data is SessionDragData || details.data is TerminalDragData,
        onMove: (details) {
          final box = context.findRenderObject() as RenderBox?;
          if (box == null || !box.hasSize) return;
          final local = box.globalToLocal(details.offset);
          final zone = _hitTestDropZone(box.size, local);
          if (zone != _hoverZone) setState(() => _hoverZone = zone);
        },
        onLeave: (_) {
          if (_hoverZone != null) setState(() => _hoverZone = null);
        },
        onAcceptWithDetails: (details) {
          final box = context.findRenderObject() as RenderBox?;
          if (box == null || !box.hasSize) return;
          final local = box.globalToLocal(details.offset);
          final zone = _hitTestDropZone(box.size, local);
          setState(() => _hoverZone = null);
          if (zone == null) return;
          final data = details.data;
          if (data is SessionDragData) {
            appState.splitPaneWithSession(
              widget.pane.paneId,
              zone,
              data.sessionId,
            );
          } else if (data is TerminalDragData) {
            appState.splitPaneWithTerminal(
              widget.pane.paneId,
              zone,
              data.terminalId,
            );
          }
        },
        builder: (context, candidateData, rejectedData) {
          final paneType = appState.getPaneType(widget.pane.paneId);
          final isTerminalPane = paneType == PaneType.terminal;
          final terminalInfo = isTerminalPane
              ? appState.getTerminalPaneInfo(widget.pane.paneId)
              : null;

          return Listener(
            onPointerDown: (_) {
              // Defer to a microtask so the rebuild from notifyListeners()
              // doesn't happen synchronously during pointer event dispatch.
              // This prevents disrupting gesture recognizers and focus
              // handling in child widgets (e.g. TerminalView).
              if (appState.activePaneId != widget.pane.paneId) {
                Future.microtask(
                    () => appState.setActivePane(widget.pane.paneId));
              }
            },
            child: Stack(
              children: [
                Container(
                  decoration: BoxDecoration(
                    border: multiPane
                        ? Border.all(
                            color: isActive
                                ? context.appColors.accent.withAlpha(100)
                                : Colors.transparent,
                            width: 1,
                          )
                        : null,
                  ),
                  child: isTerminalPane && terminalInfo != null
                      ? Column(
                          children: [
                            _TerminalPaneHeader(
                              paneId: widget.pane.paneId,
                              info: terminalInfo,
                              appState: appState,
                            ),
                            Expanded(
                              child: TerminalPane(
                                key: appState.terminalPaneKey(widget.pane.paneId),
                                paneId: widget.pane.paneId,
                                info: terminalInfo,
                                appState: appState,
                              ),
                            ),
                          ],
                        )
                      : Column(
                          children: [
                            const PaneHeader(),
                            const Expanded(child: OutputDisplay()),
                            const InputArea(),
                          ],
                        ),
                ),
                if (_hoverZone != null) _DropZoneOverlay(zone: _hoverZone!),
              ],
            ),
          );
        },
      ),
    );
  }
}

/// Semi-transparent overlay indicating where the new pane will appear.
class _DropZoneOverlay extends StatelessWidget {
  final DropZone zone;

  const _DropZoneOverlay({required this.zone});

  @override
  Widget build(BuildContext context) {
    return Positioned.fill(
      child: IgnorePointer(
        child: Align(
          alignment: switch (zone) {
            DropZone.left => Alignment.centerLeft,
            DropZone.right => Alignment.centerRight,
            DropZone.top => Alignment.topCenter,
            DropZone.bottom => Alignment.bottomCenter,
          },
          child: FractionallySizedBox(
            widthFactor:
                (zone == DropZone.left || zone == DropZone.right) ? 0.5 : 1.0,
            heightFactor:
                (zone == DropZone.top || zone == DropZone.bottom) ? 0.5 : 1.0,
            child: Container(
              decoration: BoxDecoration(
                color: context.appColors.accent.withAlpha(40),
                border: Border.all(color: context.appColors.accent.withAlpha(80), width: 2),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Center(
                child: Icon(
                  switch (zone) {
                    DropZone.left => Icons.arrow_back_rounded,
                    DropZone.right => Icons.arrow_forward_rounded,
                    DropZone.top => Icons.arrow_upward_rounded,
                    DropZone.bottom => Icons.arrow_downward_rounded,
                  },
                  color: context.appColors.accentLight.withAlpha(180),
                  size: 32,
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Header bar for terminal panes (similar to PaneHeader for chat panes).
class _TerminalPaneHeader extends StatelessWidget {
  final String paneId;
  final TerminalSessionInfo info;
  final AppState appState;

  const _TerminalPaneHeader({
    required this.paneId,
    required this.info,
    required this.appState,
  });

  @override
  Widget build(BuildContext context) {
    final worker = appState.getWorker(info.workerId);
    final workerName = worker?.config.name ?? 'Unknown';

    return Container(
      height: 32,
      decoration: BoxDecoration(
        color: context.appColors.bgSurface,
        border: Border(
          bottom: BorderSide(color: context.appColors.divider, width: 1),
        ),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: Row(
        children: [
          Icon(Icons.terminal_rounded,
              color: context.appColors.textSecondary, size: 16),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              '${info.title} \u00B7 $workerName',
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 12,
                fontWeight: FontWeight.w500,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          SizedBox(
            width: 24,
            height: 24,
            child: IconButton(
              padding: EdgeInsets.zero,
              icon: Icon(Icons.close_rounded,
                  color: context.appColors.textMuted, size: 16),
              onPressed: () => appState.closePane(paneId),
              tooltip: 'Close terminal pane',
              constraints: const BoxConstraints(maxWidth: 24, maxHeight: 24),
            ),
          ),
        ],
      ),
    );
  }
}
