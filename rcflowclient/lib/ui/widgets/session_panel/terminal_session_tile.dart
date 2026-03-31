import 'package:flutter/material.dart';

import '../../../state/app_state.dart';
import '../../../theme.dart';
import '../terminal_pane.dart';
import 'drag_data.dart';
import 'helpers.dart';

class TerminalSessionTile extends StatelessWidget {
  final TerminalSessionInfo info;
  final AppState state;
  final bool isConnected;
  final VoidCallback? onSessionSelected;

  const TerminalSessionTile({
    super.key,
    required this.info,
    required this.state,
    required this.isConnected,
    required this.onSessionSelected,
  });

  @override
  Widget build(BuildContext context) {
    final isShownInPane =
        info.paneId != null && state.panes.containsKey(info.paneId);
    final isActivePane =
        isShownInPane && !state.hasNoPanes && state.activePaneId == info.paneId;
    final dimmed = !isConnected;

    final tile = GestureDetector(
      onSecondaryTapUp: (details) =>
          _showTerminalContextMenu(context, details.globalPosition),
      child: Opacity(
        opacity: dimmed ? 0.5 : 1.0,
        child: Container(
          decoration: BoxDecoration(
            color: isActivePane
                ? context.appColors.accent.withAlpha(25)
                : isShownInPane
                ? context.appColors.accent.withAlpha(12)
                : null,
            border: isActivePane
                ? Border(
                    left: BorderSide(color: context.appColors.accent, width: 3),
                  )
                : isShownInPane
                ? Border(
                    left: BorderSide(
                      color: context.appColors.accent.withAlpha(80),
                      width: 2,
                    ),
                  )
                : null,
          ),
          child: ListTile(
            leading: Container(
              width: 30,
              height: 30,
              decoration: BoxDecoration(
                color: info.ended
                    ? context.appColors.bgElevated
                    : const Color(0xFF1A2A1A),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Icon(
                Icons.terminal_rounded,
                color: info.ended
                    ? context.appColors.textMuted
                    : context.appColors.successText,
                size: 16,
              ),
            ),
            title: Text(
              info.title,
              style: TextStyle(
                color: isActivePane
                    ? context.appColors.accentLight
                    : context.appColors.textPrimary,
                fontSize: 12,
                fontWeight: isActivePane ? FontWeight.w600 : FontWeight.w400,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
            subtitle: Text(
              _terminalSubtitle(info),
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 10,
              ),
            ),
            trailing: SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.close_rounded,
                  color: context.appColors.textSecondary,
                  size: 16,
                ),
                tooltip: 'Close terminal',
                onPressed: dimmed ? null : () => _confirmCloseTerminal(context),
              ),
            ),
            dense: true,
            visualDensity: const VisualDensity(vertical: -4),
            contentPadding: const EdgeInsets.only(left: 36, right: 8),
            onTap: dimmed
                ? null
                : () {
                    state.showTerminalInPane(info.terminalId);
                    onSessionSelected?.call();
                  },
            onLongPress: () => _showTerminalRenameDialog(context),
          ),
        ),
      ),
    );

    return Draggable<TerminalDragData>(
      data: TerminalDragData(
        terminalId: info.terminalId,
        workerId: info.workerId,
        label: info.title,
      ),
      feedback: Material(
        color: Colors.transparent,
        child: Container(
          padding: EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          decoration: BoxDecoration(
            color: context.appColors.bgElevated,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(color: context.appColors.accent.withAlpha(120)),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withAlpha(100),
                blurRadius: 8,
                offset: Offset(0, 2),
              ),
            ],
          ),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                Icons.terminal_rounded,
                color: context.appColors.textSecondary,
                size: 14,
              ),
              SizedBox(width: 6),
              ConstrainedBox(
                constraints: BoxConstraints(maxWidth: 180),
                child: Text(
                  info.title,
                  style: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 12,
                    fontWeight: FontWeight.w500,
                    decoration: TextDecoration.none,
                  ),
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
        ),
      ),
      childWhenDragging: Opacity(opacity: 0.4, child: tile),
      child: tile,
    );
  }

  static String _terminalSubtitle(TerminalSessionInfo info) {
    final local = info.createdAt.toLocal();
    final time =
        '${monthAbbr(local.month)} ${local.day}, '
        '${local.hour.toString().padLeft(2, '0')}:'
        '${local.minute.toString().padLeft(2, '0')}';
    return '$time \u00B7 ${info.shortId}';
  }

  void _showTerminalContextMenu(BuildContext context, Offset position) {
    final overlay = Overlay.of(context).context.findRenderObject() as RenderBox;
    showMenu<String>(
      context: context,
      position: RelativeRect.fromRect(
        position & Size(1, 1),
        Offset.zero & overlay.size,
      ),
      color: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      items: [
        PopupMenuItem(
          value: 'rename',
          child: Row(
            children: [
              Icon(
                Icons.edit_outlined,
                color: context.appColors.textSecondary,
                size: 18,
              ),
              SizedBox(width: 8),
              Text(
                'Rename',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
        PopupMenuItem(
          value: 'close',
          child: Row(
            children: [
              Icon(
                Icons.close_rounded,
                color: context.appColors.errorText,
                size: 18,
              ),
              SizedBox(width: 8),
              Text(
                'Close terminal',
                style: TextStyle(color: context.appColors.errorText),
              ),
            ],
          ),
        ),
      ],
    ).then((value) {
      if (!context.mounted) return;
      if (value == 'rename') {
        _showTerminalRenameDialog(context);
      } else if (value == 'close') {
        _confirmCloseTerminal(context);
      }
    });
  }

  void _confirmCloseTerminal(BuildContext context) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text(
          'Close terminal?',
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 18),
        ),
        content: Text(
          'This will kill the terminal session on the server.',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 14,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text(
              'Keep',
              style: TextStyle(color: context.appColors.textSecondary),
            ),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: context.appColors.errorText,
            ),
            onPressed: () {
              Navigator.of(ctx).pop();
              state.closeTerminalSession(info.terminalId);
            },
            child: const Text('Close', style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }

  void _showTerminalRenameDialog(BuildContext context) {
    final controller = TextEditingController(text: info.title);
    void submit(BuildContext ctx) {
      Navigator.of(ctx).pop();
      state.renameTerminal(info.terminalId, controller.text);
    }

    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text(
          'Rename terminal',
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 18),
        ),
        content: TextField(
          controller: controller,
          autofocus: true,
          maxLength: 200,
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 14),
          decoration: InputDecoration(
            hintText: 'Terminal name',
            hintStyle: TextStyle(color: context.appColors.textMuted),
          ),
          onSubmitted: (_) => submit(ctx),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text(
              'Cancel',
              style: TextStyle(color: context.appColors.textSecondary),
            ),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: context.appColors.accent,
            ),
            onPressed: () => submit(ctx),
            child: const Text('Rename', style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }
}
