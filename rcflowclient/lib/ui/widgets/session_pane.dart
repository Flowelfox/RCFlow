import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/split_tree.dart';
import '../../models/todo_item.dart';
import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';
import 'input_area.dart';
import 'output_display.dart';
import 'pane_header.dart';
import 'session_panel.dart' show TerminalDragData;
import 'session_panel/task_drag_data.dart';
import 'artifact_pane.dart';
import 'linear_issue_pane.dart';
import 'statistics_pane.dart';
import 'task_pane.dart';
import 'terminal_pane.dart';
import 'todo_panel.dart';
import 'project_panel.dart';

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
            details.data is SessionDragData ||
            details.data is TerminalDragData ||
            details.data is TaskDragData,
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
          } else if (data is TaskDragData) {
            appState.splitPaneWithTask(
              widget.pane.paneId,
              zone,
              data.taskId,
            );
          }
        },
        builder: (context, candidateData, rejectedData) {
          final paneType = appState.getPaneType(widget.pane.paneId);
          final isTerminalPane = paneType == PaneType.terminal;
          final isTaskPane = paneType == PaneType.task;
          final isArtifactPane = paneType == PaneType.artifact;
          final isLinearIssuePane = paneType == PaneType.linearIssue;
          final terminalInfo = isTerminalPane
              ? appState.getTerminalPaneInfo(widget.pane.paneId)
              : null;

          return Listener(
            onPointerDown: (_) {
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
                      : isTaskPane
                          ? TaskPane(
                              paneId: widget.pane.paneId,
                              pane: widget.pane,
                            )
                          : isArtifactPane
                              ? ArtifactPane(
                                  paneId: widget.pane.paneId,
                                  pane: widget.pane,
                                )
                              : isLinearIssuePane
                                  ? LinearIssuePane(
                                      paneId: widget.pane.paneId,
                                      pane: widget.pane,
                                    )
                                  : Column(
                              children: [
                                const PaneHeader(),
                                Expanded(
                                  child: _OutputWithRightPanels(pane: widget.pane),
                                ),
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

/// Wraps [OutputDisplay] with optional resizable right panels (Todo, Worktree).
///
/// Bookmark tabs are always shown on the right edge when any panel is
/// available, styled like sideways page-tabs.  Tapping a tab opens that panel;
/// tapping the active tab closes it.
class _OutputWithRightPanels extends StatefulWidget {
  final PaneState pane;
  const _OutputWithRightPanels({required this.pane});

  @override
  State<_OutputWithRightPanels> createState() =>
      _OutputWithRightPanelsState();
}

class _OutputWithRightPanelsState extends State<_OutputWithRightPanels> {
  bool _dragging = false;

  @override
  Widget build(BuildContext context) {
    final pane = context.watch<PaneState>();
    final hasTodos = pane.todos.isNotEmpty;

    final activePanel = pane.activeRightPanel;
    final panelWidth = pane.rightPanelWidth;

    // OutputDisplay is always the first widget in the Row so its element is
    // never remounted when the right panel is toggled. Previously it sat inside
    // a nested Row (when a panel was open) vs. directly inside the Expanded
    // (when no panel was open), causing Flutter to rebuild and scroll-reset it.
    return Row(
      children: [
        // Main content — stable position so OutputDisplay state is preserved.
        const Expanded(child: OutputDisplay()),
        // Drag handle and panel content (only when a panel is active).
        if (activePanel != null) ...[
          MouseRegion(
            cursor: SystemMouseCursors.resizeColumn,
            child: GestureDetector(
              onHorizontalDragStart: (_) =>
                  setState(() => _dragging = true),
              onHorizontalDragUpdate: (details) {
                final newWidth = panelWidth - details.delta.dx;
                pane.setRightPanelWidth(newWidth);
              },
              onHorizontalDragEnd: (_) =>
                  setState(() => _dragging = false),
              child: Container(
                width: 5,
                color: _dragging
                    ? context.appColors.accent.withAlpha(80)
                    : Colors.transparent,
                child: Center(
                  child: Container(
                    width: 1,
                    height: double.infinity,
                    color: context.appColors.divider,
                  ),
                ),
              ),
            ),
          ),
          SizedBox(
            width: panelWidth,
            child: switch (activePanel) {
              'todo' => const TodoPanel(),
              'statistics' => const StatisticsPane(),
              _ => const ProjectPanel(),
            },
          ),
        ],
        // Bookmark tabs column — always visible so user can open any panel.
        _RightBookmarks(
          hasTodos: hasTodos,
          activePanel: activePanel,
          pane: pane,
        ),
      ],
    );
  }
}

/// Vertical column of sideways bookmark tabs on the right edge.
/// Always rendered so the user can open any panel regardless of content.
class _RightBookmarks extends StatelessWidget {
  final bool hasTodos;
  final String? activePanel;
  final PaneState pane;

  const _RightBookmarks({
    required this.hasTodos,
    required this.activePanel,
    required this.pane,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisAlignment: MainAxisAlignment.center,
      children: [
        _BookmarkTab(
          panelKey: 'todo',
          icon: Icons.checklist_rounded,
          label: _todoLabel(pane),
          activePanel: activePanel,
          iconColor: context.appColors.toolAccent,
          onTap: () => pane.toggleRightPanel('todo'),
        ),
        const SizedBox(height: 4),
        _BookmarkTab(
          panelKey: 'project',
          icon: Icons.folder_outlined,
          label: 'Project',
          activePanel: activePanel,
          iconColor: context.appColors.accent,
          onTap: () => pane.toggleRightPanel('project'),
        ),
        const SizedBox(height: 4),
        _BookmarkTab(
          panelKey: 'statistics',
          icon: Icons.bar_chart_rounded,
          label: 'Stats',
          activePanel: activePanel,
          iconColor: Colors.teal,
          onTap: () => pane.toggleRightPanel('statistics'),
        ),
      ],
    );
  }

  String _todoLabel(PaneState pane) {
    final completed =
        pane.todos.where((t) => t.status == TodoStatus.completed).length;
    return 'Todo $completed/${pane.todos.length}';
  }
}

/// A single sideways bookmark tab.
class _BookmarkTab extends StatelessWidget {
  final String panelKey;
  final IconData icon;
  final String label;
  final String? activePanel;
  final Color iconColor;
  final VoidCallback onTap;

  const _BookmarkTab({
    required this.panelKey,
    required this.icon,
    required this.label,
    required this.activePanel,
    required this.iconColor,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final isActive = activePanel == panelKey;
    return Tooltip(
      message: label,
      child: InkWell(
        onTap: onTap,
        borderRadius:
            const BorderRadius.horizontal(left: Radius.circular(6)),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          padding:
              const EdgeInsets.symmetric(horizontal: 4, vertical: 10),
          decoration: BoxDecoration(
            color: isActive
                ? context.appColors.bgSurface
                : context.appColors.bgBase,
            border: Border(
              left: BorderSide(
                color: isActive
                    ? iconColor.withAlpha(200)
                    : context.appColors.divider,
                width: isActive ? 2 : 1,
              ),
              top: BorderSide(color: context.appColors.divider),
              bottom: BorderSide(color: context.appColors.divider),
            ),
            borderRadius:
                const BorderRadius.horizontal(left: Radius.circular(6)),
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(icon,
                  size: 14,
                  color: isActive ? iconColor : context.appColors.textMuted),
              const SizedBox(height: 4),
              RotatedBox(
                quarterTurns: 1,
                child: Text(
                  label,
                  style: TextStyle(
                    color: isActive
                        ? context.appColors.textPrimary
                        : context.appColors.textMuted,
                    fontSize: 10,
                    fontWeight: isActive
                        ? FontWeight.w600
                        : FontWeight.w500,
                  ),
                ),
              ),
            ],
          ),
        ),
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
