import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../state/app_state.dart';
import '../../../theme.dart';

/// Sidebar panel for git worktree management.
///
/// Groups worktrees by worker + repository path, derived from sessions that
/// have used a worktree tool. Each group shows the live worktree list fetched
/// from the server, with controls to create, merge, or remove worktrees.
class WorktreeListPanel extends StatefulWidget {
  const WorktreeListPanel({super.key});

  @override
  State<WorktreeListPanel> createState() => _WorktreeListPanelState();
}

class _WorktreeListPanelState extends State<WorktreeListPanel> {
  // "workerId:repoPath" -> live list fetched from server
  final Map<String, List<Map<String, dynamic>>> _worktrees = {};
  final Map<String, bool> _loading = {};
  final Map<String, String?> _errors = {};

  // -------------------------------------------------------------------------
  // HTTP helpers
  // -------------------------------------------------------------------------

  Future<void> _refresh(
      AppState state, String workerId, String repoPath) async {
    final key = _key(workerId, repoPath);
    setState(() {
      _loading[key] = true;
      _errors[key] = null;
    });
    try {
      final worker = state.getWorker(workerId);
      if (worker == null) return;
      final result = await worker.ws.listWorktrees(repoPath);
      final list = (result['worktrees'] as List<dynamic>? ?? [])
          .cast<Map<String, dynamic>>();
      if (mounted) setState(() => _worktrees[key] = list);
    } catch (e) {
      if (mounted) setState(() => _errors[key] = e.toString());
    } finally {
      if (mounted) setState(() => _loading[key] = false);
    }
  }

  Future<void> _create(
      AppState state, String workerId, String repoPath) async {
    final result = await _showCreateDialog(context, repoPath);
    if (result == null) return;
    final key = _key(workerId, repoPath);
    setState(() => _loading[key] = true);
    try {
      final worker = state.getWorker(workerId);
      if (worker == null) return;
      await worker.ws.createWorktree(
        branch: result.branch,
        repoPath: repoPath,
        base: result.base,
      );
      await _refresh(state, workerId, repoPath);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Create failed: $e')),
        );
        setState(() => _loading[key] = false);
      }
    }
  }

  Future<void> _merge(AppState state, String workerId, String repoPath,
      String name) async {
    final message = await _showMergeDialog(context, name);
    if (message == null) return;
    final key = _key(workerId, repoPath);
    setState(() => _loading[key] = true);
    try {
      final worker = state.getWorker(workerId);
      if (worker == null) return;
      await worker.ws.mergeWorktree(
          name: name, message: message, repoPath: repoPath);
      await _refresh(state, workerId, repoPath);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Merge failed: $e')),
        );
        setState(() => _loading[key] = false);
      }
    }
  }

  Future<void> _remove(AppState state, String workerId, String repoPath,
      String name) async {
    final confirmed = await _confirmRemove(context, name);
    if (!confirmed) return;
    final key = _key(workerId, repoPath);
    setState(() => _loading[key] = true);
    try {
      final worker = state.getWorker(workerId);
      if (worker == null) return;
      await worker.ws.removeWorktree(name: name, repoPath: repoPath);
      await _refresh(state, workerId, repoPath);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Remove failed: $e')),
        );
        setState(() => _loading[key] = false);
      }
    }
  }

  // -------------------------------------------------------------------------
  // Build
  // -------------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return Consumer<AppState>(
      builder: (context, state, _) {
        // Collect unique (workerId, repoPath) groups from sessions.
        final seen = <String>{};
        final groups = <({String workerId, String repoPath})>[];
        for (final session in state.sessions) {
          final wt = session.worktreeInfo;
          if (wt == null || wt.repoPath.isEmpty) continue;
          final k = _key(session.workerId, wt.repoPath);
          if (seen.add(k)) {
            groups.add((workerId: session.workerId, repoPath: wt.repoPath));
          }
        }

        if (groups.isEmpty) {
          return Center(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(Icons.device_hub_outlined,
                    color: context.appColors.textMuted, size: 40),
                const SizedBox(height: 12),
                Text('No worktrees yet',
                    style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 16,
                        fontWeight: FontWeight.w600)),
                const SizedBox(height: 4),
                Text('Worktrees appear here once\na session uses worktree tools',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                        color: context.appColors.textMuted, fontSize: 13)),
              ],
            ),
          );
        }

        return ListView.builder(
          padding: const EdgeInsets.only(bottom: 16),
          itemCount: groups.length,
          itemBuilder: (context, i) {
            final g = groups[i];
            return _buildGroup(context, state, g.workerId, g.repoPath);
          },
        );
      },
    );
  }

  Widget _buildGroup(
      BuildContext context, AppState state, String workerId, String repoPath) {
    final key = _key(workerId, repoPath);
    final isLoading = _loading[key] ?? false;
    final error = _errors[key];
    final list = _worktrees[key];
    final workerConfig = state.workerConfigs
        .where((c) => c.id == workerId)
        .firstOrNull;
    final workerName = workerConfig?.name ?? workerId;
    final shortRepo = repoPath.split('/').last;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Section header
        Padding(
          padding: const EdgeInsets.fromLTRB(12, 12, 8, 4),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(workerName,
                        style: TextStyle(
                            color: context.appColors.textSecondary,
                            fontSize: 12,
                            fontWeight: FontWeight.w500)),
                    Text(shortRepo,
                        style: TextStyle(
                            color: context.appColors.textPrimary,
                            fontSize: 13,
                            fontWeight: FontWeight.w600),
                        overflow: TextOverflow.ellipsis),
                  ],
                ),
              ),
              // Refresh
              _IconBtn(
                icon: Icons.refresh,
                tooltip: 'Refresh',
                onTap: isLoading ? null : () => _refresh(state, workerId, repoPath),
              ),
              // New worktree
              _IconBtn(
                icon: Icons.add,
                tooltip: 'New worktree',
                onTap: isLoading ? null : () => _create(state, workerId, repoPath),
              ),
            ],
          ),
        ),
        if (isLoading)
          const Padding(
            padding: EdgeInsets.symmetric(horizontal: 16, vertical: 8),
            child: LinearProgressIndicator(),
          )
        else if (error != null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            child: Text(error,
                style: TextStyle(
                    color: context.appColors.errorText, fontSize: 12)),
          )
        else if (list == null)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            child: TextButton.icon(
              onPressed: () => _refresh(state, workerId, repoPath),
              icon: const Icon(Icons.refresh, size: 16),
              label: const Text('Load worktrees'),
            ),
          )
        else if (list.isEmpty)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
            child: Text('No worktrees',
                style: TextStyle(
                    color: context.appColors.textMuted, fontSize: 12)),
          )
        else
          ...list.map((wt) => _buildWorktreeItem(
              context, state, workerId, repoPath, wt)),
        const Divider(height: 1),
      ],
    );
  }

  Widget _buildWorktreeItem(BuildContext context, AppState state,
      String workerId, String repoPath, Map<String, dynamic> wt) {
    final name = wt['name'] as String? ?? '';
    final branch = wt['branch'] as String? ?? '';
    final base = wt['base'] as String? ?? 'main';

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 2),
      child: Row(
        children: [
          Icon(Icons.call_split, size: 14, color: context.appColors.textMuted),
          const SizedBox(width: 6),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(name,
                    style: TextStyle(
                        color: context.appColors.textPrimary,
                        fontSize: 13,
                        fontWeight: FontWeight.w500),
                    overflow: TextOverflow.ellipsis),
                Text('$branch → $base',
                    style: TextStyle(
                        color: context.appColors.textMuted, fontSize: 11),
                    overflow: TextOverflow.ellipsis),
              ],
            ),
          ),
          // Merge
          _IconBtn(
            icon: Icons.merge,
            tooltip: 'Merge into $base',
            iconSize: 16,
            onTap: () => _merge(state, workerId, repoPath, name),
          ),
          // Remove
          _IconBtn(
            icon: Icons.delete_outline,
            tooltip: 'Remove (discard)',
            iconSize: 16,
            color: context.appColors.errorText,
            onTap: () => _remove(state, workerId, repoPath, name),
          ),
        ],
      ),
    );
  }

  // -------------------------------------------------------------------------
  // Dialogs
  // -------------------------------------------------------------------------

  Future<_CreateParams?> _showCreateDialog(
      BuildContext context, String repoPath) async {
    final branchCtrl = TextEditingController();
    final baseCtrl = TextEditingController(text: 'main');
    final formKey = GlobalKey<FormState>();

    return showDialog<_CreateParams>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('New Worktree'),
        content: Form(
          key: formKey,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextFormField(
                controller: branchCtrl,
                decoration: const InputDecoration(
                    labelText: 'Branch',
                    hintText: 'feature/PROJ-123/description'),
                validator: (v) =>
                    (v == null || v.trim().isEmpty) ? 'Required' : null,
                autofocus: true,
              ),
              const SizedBox(height: 8),
              TextFormField(
                controller: baseCtrl,
                decoration: const InputDecoration(labelText: 'Base branch'),
                validator: (v) =>
                    (v == null || v.trim().isEmpty) ? 'Required' : null,
              ),
            ],
          ),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Cancel')),
          TextButton(
            onPressed: () {
              if (formKey.currentState!.validate()) {
                Navigator.pop(
                    ctx,
                    _CreateParams(
                        branch: branchCtrl.text.trim(),
                        base: baseCtrl.text.trim()));
              }
            },
            child: const Text('Create'),
          ),
        ],
      ),
    );
  }

  Future<String?> _showMergeDialog(BuildContext context, String name) async {
    final msgCtrl = TextEditingController();
    final formKey = GlobalKey<FormState>();

    return showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text('Merge "$name"'),
        content: Form(
          key: formKey,
          child: TextFormField(
            controller: msgCtrl,
            decoration:
                const InputDecoration(labelText: 'Commit message'),
            validator: (v) =>
                (v == null || v.trim().isEmpty) ? 'Required' : null,
            autofocus: true,
            maxLines: 3,
          ),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('Cancel')),
          TextButton(
            onPressed: () {
              if (formKey.currentState!.validate()) {
                Navigator.pop(ctx, msgCtrl.text.trim());
              }
            },
            child: const Text('Merge'),
          ),
        ],
      ),
    );
  }

  Future<bool> _confirmRemove(BuildContext context, String name) async {
    final result = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Remove Worktree'),
        content: Text(
            'Remove "$name" and delete its branch? This cannot be undone.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel')),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            style:
                TextButton.styleFrom(foregroundColor: Colors.red),
            child: const Text('Remove'),
          ),
        ],
      ),
    );
    return result ?? false;
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

String _key(String workerId, String repoPath) => '$workerId:$repoPath';

class _CreateParams {
  final String branch;
  final String base;
  const _CreateParams({required this.branch, required this.base});
}

class _IconBtn extends StatelessWidget {
  final IconData icon;
  final String tooltip;
  final VoidCallback? onTap;
  final double iconSize;
  final Color? color;

  const _IconBtn({
    required this.icon,
    required this.tooltip,
    this.onTap,
    this.iconSize = 18,
    this.color,
  });

  @override
  Widget build(BuildContext context) {
    return Tooltip(
      message: tooltip,
      child: InkWell(
        borderRadius: BorderRadius.circular(4),
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(4),
          child: Icon(
            icon,
            size: iconSize,
            color: onTap == null
                ? context.appColors.textMuted.withValues(alpha: 0.4)
                : (color ?? context.appColors.textMuted),
          ),
        ),
      ),
    );
  }
}
