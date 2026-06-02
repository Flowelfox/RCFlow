part of 'input_area.dart';

class _AttachmentChip extends StatelessWidget {
  final String name;
  final String mimeType;
  final VoidCallback? onRemove;

  const _AttachmentChip({
    required this.name,
    required this.mimeType,
    this.onRemove,
  });

  static bool _isImage(String mime) => mime.startsWith('image/');

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: kSpace2, vertical: kSpace1),
      decoration: BoxDecoration(
        color: context.appColors.bgElevated,
        borderRadius: BorderRadius.circular(kRadiusSmall),
        border: Border.all(color: context.appColors.divider),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            _isImage(mimeType)
                ? Icons.image_rounded
                : Icons.insert_drive_file_rounded,
            size: 13,
            color: context.appColors.textMuted,
          ),
          const SizedBox(width: 5),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 160),
            child: Text(
              name,
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 12,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (onRemove != null) ...[
            const SizedBox(width: 4),
            GestureDetector(
              onTap: onRemove,
              child: Icon(
                Icons.close_rounded,
                size: 13,
                color: context.appColors.textMuted,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _WorkerChip extends StatelessWidget {
  final String label;
  final List<WorkerConfig> workers;
  final void Function(String workerId) onSelected;

  const _WorkerChip({
    required this.label,
    required this.workers,
    required this.onSelected,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () {
        final RenderBox box = context.findRenderObject() as RenderBox;
        final offset = box.localToGlobal(Offset.zero);
        showMenu<String>(
          context: context,
          position: RelativeRect.fromLTRB(
            offset.dx,
            offset.dy - (workers.length * 40 + 8),
            offset.dx + box.size.width,
            offset.dy,
          ),
          color: context.appColors.bgSurface,
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(kRadiusMedium),
          ),
          items: workers
              .map(
                (w) => PopupMenuItem<String>(
                  value: w.id,
                  height: 40,
                  child: Text(
                    w.name,
                    style: TextStyle(
                      color: context.appColors.textPrimary,
                      fontSize: 13,
                    ),
                  ),
                ),
              )
              .toList(),
        ).then((id) {
          if (id != null) onSelected(id);
        });
      },
      child: BadgeChip(
        label: label,
        icon: Icons.dns_outlined,
        trailing: BadgeChip.neutralDropdownCaret(context),
      ),
    );
  }
}

class _ProjectChip extends StatelessWidget {
  final String name;
  final String? error;
  final VoidCallback onClear;

  const _ProjectChip({required this.name, this.error, required this.onClear});

  // Neutral by default; error state uses project badge's red accent.
  static const _errorColor = Color(0xFFEF4444); // red-500

  @override
  Widget build(BuildContext context) {
    final hasError = error != null;
    final trailingColor =
        hasError ? _errorColor.withAlpha(180) : context.appColors.textMuted;
    return Tooltip(
      message: error ?? '',
      child: BadgeChip(
        color: hasError ? _errorColor : null,
        label: name,
        icon: hasError ? Icons.error_outline_rounded : Icons.folder_outlined,
        trailing: GestureDetector(
          onTap: onClear,
          child: Icon(Icons.close, size: 14, color: trailingColor),
        ),
      ),
    );
  }
}

/// Chip displayed above the input field representing a selected tool mention.
/// Mirrors _ProjectChip but uses a build icon and does not have an error state.
class _ToolChip extends StatelessWidget {
  final String name;
  final VoidCallback onClear;

  const _ToolChip({required this.name, required this.onClear});

  @override
  Widget build(BuildContext context) {
    return BadgeChip(
      label: name,
      icon: Icons.build_outlined,
      trailing: GestureDetector(
        onTap: onClear,
        child: Icon(Icons.close, size: 14, color: context.appColors.textMuted),
      ),
    );
  }
}

/// Chip displayed above the input field before session creation allowing the
/// user to pre-select a git worktree.  Shows the selected worktree name when
/// one is chosen, or a button to open the worktree picker dropdown.
class _WorktreeChip extends StatelessWidget {
  final String? selectedPath;
  // ValueGetter so the popup menu reads the current list at open-time, not
  // the stale value captured when the widget was last constructed.
  final List<Map<String, dynamic>>? Function() getWorktrees;
  final bool loading;
  final VoidCallback onOpen;
  final void Function(String path) onSelect;
  final VoidCallback onClear;
  final Future<void> Function()? onCreateWorktree;

  const _WorktreeChip({
    required this.selectedPath,
    required this.getWorktrees,
    required this.loading,
    required this.onOpen,
    required this.onSelect,
    required this.onClear,
    this.onCreateWorktree,
  });

  @override
  Widget build(BuildContext context) {
    final label = selectedPath != null
        ? selectedPath!.split('/').last
        : 'Worktree';

    return GestureDetector(
      onTap: () {
        onOpen();
        // Defer the menu until the frame after onOpen so that the fresh fetch
        // can complete; but we still open immediately with whatever data is
        // already cached so the picker is not sluggish.
        WidgetsBinding.instance.addPostFrameCallback((_) {
          final box = context.findRenderObject() as RenderBox?;
          if (box == null || !context.mounted) return;
          final offset = box.localToGlobal(Offset.zero);
          // Read the current worktree list at menu-open time, not the stale
          // value captured when this widget was last constructed.
          final currentWorktrees = getWorktrees();
          final items = <PopupMenuEntry<String>>[
            PopupMenuItem<String>(
              value: '__none__',
              height: 36,
              child: Text(
                'No worktree (default)',
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 12,
                ),
              ),
            ),
            const PopupMenuDivider(),
            if (currentWorktrees == null || currentWorktrees.isEmpty)
              PopupMenuItem<String>(
                enabled: false,
                height: 36,
                child: Text(
                  currentWorktrees == null ? 'Loading…' : 'No worktrees',
                  style: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 12,
                  ),
                ),
              )
            else
              ...currentWorktrees.map((wt) {
                final name = wt['name'] as String? ?? '';
                final branch = wt['branch'] as String? ?? '';
                final path = wt['path'] as String? ?? '';
                final isSelected = selectedPath == path;
                return PopupMenuItem<String>(
                  value: path,
                  height: 44,
                  child: Row(
                    children: [
                      Icon(
                        isSelected ? Icons.check_circle : Icons.call_split,
                        size: 14,
                        color: isSelected
                            ? context.appColors.accent
                            : context.appColors.textMuted,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(
                              name,
                              style: TextStyle(
                                color: isSelected
                                    ? context.appColors.accent
                                    : context.appColors.textPrimary,
                                fontSize: 12,
                                fontWeight: isSelected
                                    ? FontWeight.w600
                                    : FontWeight.w500,
                              ),
                              overflow: TextOverflow.ellipsis,
                            ),
                            if (branch.isNotEmpty)
                              Text(
                                branch,
                                style: TextStyle(
                                  color: context.appColors.textMuted,
                                  fontSize: 10,
                                ),
                                overflow: TextOverflow.ellipsis,
                              ),
                          ],
                        ),
                      ),
                    ],
                  ),
                );
              }),
            if (onCreateWorktree != null) ...[
              const PopupMenuDivider(),
              PopupMenuItem<String>(
                value: '__create__',
                height: 36,
                child: Row(
                  children: [
                    Icon(
                      Icons.add,
                      size: 14,
                      color: context.appColors.textSecondary,
                    ),
                    const SizedBox(width: 8),
                    Text(
                      'Create worktree',
                      style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 12,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ];

          showMenu<String>(
            context: context,
            position: RelativeRect.fromLTRB(
              offset.dx,
              offset.dy - (items.length * 40.0 + 16),
              offset.dx + box.size.width,
              offset.dy,
            ),
            color: context.appColors.bgSurface,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(kRadiusMedium),
            ),
            items: items,
          ).then((value) {
            if (value == null) return;
            if (value == '__none__') {
              onClear();
            } else if (value == '__create__') {
              onCreateWorktree?.call();
            } else {
              onSelect(value);
            }
          });
        });
      },
      child: BadgeChip(
        label: label,
        icon: Icons.account_tree_outlined,
        trailing: _trailing(context),
      ),
    );
  }

  Widget _trailing(BuildContext context) {
    if (loading) {
      return SizedBox(
        width: 12,
        height: 12,
        child: CircularProgressIndicator(
          strokeWidth: 1.5,
          color: context.appColors.textMuted,
        ),
      );
    }
    if (selectedPath != null) {
      return GestureDetector(
        onTap: onClear,
        behavior: HitTestBehavior.opaque,
        child: Icon(Icons.close, size: 14, color: context.appColors.textMuted),
      );
    }
    return BadgeChip.neutralDropdownCaret(context);
  }
}
