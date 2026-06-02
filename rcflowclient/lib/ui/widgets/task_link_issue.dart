part of 'task_pane.dart';

class _LinkIssueDialog extends StatefulWidget {
  final AppState appState;
  final TaskInfo task;

  const _LinkIssueDialog({required this.appState, required this.task});

  @override
  State<_LinkIssueDialog> createState() => _LinkIssueDialogState();
}

class _LinkIssueDialogState extends State<_LinkIssueDialog> {
  final TextEditingController _searchController = TextEditingController();
  String _query = '';
  bool _linking = false;

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  List<LinearIssueInfo> _filtered(List<LinearIssueInfo> issues) {
    if (_query.isEmpty) return issues;
    final q = _query.toLowerCase();
    return issues
        .where(
          (i) =>
              i.title.toLowerCase().contains(q) ||
              i.identifier.toLowerCase().contains(q),
        )
        .toList();
  }

  Future<void> _link(LinearIssueInfo issue) async {
    setState(() => _linking = true);
    final worker = widget.appState.getWorker(issue.workerId);
    if (worker == null) {
      setState(() => _linking = false);
      return;
    }
    try {
      await worker.ws.linkLinearIssueToTask(issue.id, widget.task.taskId);
      if (mounted) Navigator.of(context).pop();
    } catch (e) {
      if (mounted) {
        widget.appState.addSystemMessage(
          'Failed to link issue: $e',
          isError: true,
        );
        setState(() => _linking = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final unlinked = widget.appState.unlinkedLinearIssues;
    final filtered = _filtered(unlinked);

    return Dialog(
      backgroundColor: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(kRadiusLarge)),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 480, maxHeight: 500),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 20, 20, 12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Link a Linear Issue',
                    style: TextStyle(
                      color: context.appColors.textPrimary,
                      fontSize: 16,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _searchController,
                    onChanged: (v) => setState(() => _query = v),
                    autofocus: true,
                    style: TextStyle(
                      color: context.appColors.textPrimary,
                      fontSize: 13,
                    ),
                    decoration: InputDecoration(
                      hintText: 'Search issues...',
                      hintStyle: TextStyle(
                        color: context.appColors.textMuted,
                        fontSize: 13,
                      ),
                      prefixIcon: Icon(
                        Icons.search_rounded,
                        color: context.appColors.textMuted,
                        size: 16,
                      ),
                      filled: true,
                      fillColor: context.appColors.bgElevated,
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: 12,
                        vertical: 8,
                      ),
                      border: OutlineInputBorder(
                        borderSide: BorderSide.none,
                        borderRadius: BorderRadius.circular(8),
                      ),
                      enabledBorder: OutlineInputBorder(
                        borderSide: BorderSide.none,
                        borderRadius: BorderRadius.circular(8),
                      ),
                      focusedBorder: OutlineInputBorder(
                        borderSide: BorderSide(
                          color: context.appColors.accent,
                          width: 1,
                        ),
                        borderRadius: BorderRadius.circular(8),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const Divider(height: 1),
            if (unlinked.isEmpty)
              Padding(
                padding: const EdgeInsets.all(24),
                child: Center(
                  child: Text(
                    'No unlinked issues available.\nSync from Linear first.',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 13,
                    ),
                  ),
                ),
              )
            else if (filtered.isEmpty)
              Padding(
                padding: const EdgeInsets.all(24),
                child: Center(
                  child: Text(
                    'No issues match your search.',
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 13,
                    ),
                  ),
                ),
              )
            else
              Flexible(
                child: ListView.separated(
                  shrinkWrap: true,
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  itemCount: filtered.length,
                  separatorBuilder: (ctx, i) => const SizedBox(height: 0),
                  itemBuilder: (context, index) {
                    final issue = filtered[index];
                    return ListTile(
                      dense: true,
                      visualDensity: const VisualDensity(vertical: -2),
                      enabled: !_linking,
                      leading: Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 5,
                          vertical: 2,
                        ),
                        decoration: BoxDecoration(
                          color: context.appColors.bgElevated,
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(
                            color: context.appColors.divider,
                            width: 0.5,
                          ),
                        ),
                        child: Text(
                          issue.identifier,
                          style: TextStyle(
                            color: context.appColors.textMuted,
                            fontSize: 10,
                            fontWeight: FontWeight.w600,
                            fontFamily: 'monospace',
                          ),
                        ),
                      ),
                      title: Text(
                        issue.title,
                        style: TextStyle(
                          color: context.appColors.textPrimary,
                          fontSize: 13,
                        ),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                      ),
                      subtitle: Text(
                        issue.stateName,
                        style: TextStyle(
                          color: context.appColors.textMuted,
                          fontSize: 11,
                        ),
                      ),
                      onTap: () => _link(issue),
                    );
                  },
                ),
              ),
            const Divider(height: 1),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
              child: Row(
                mainAxisAlignment: MainAxisAlignment.end,
                children: [
                  TextButton(
                    onPressed: _linking
                        ? null
                        : () => Navigator.of(context).pop(),
                    child: Text(
                      'Cancel',
                      style: TextStyle(color: context.appColors.textSecondary),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
