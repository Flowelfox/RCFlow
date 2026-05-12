import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/session_info.dart';
import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';

/// Right-side dockable panel showing git worktree controls for the active
/// session's repository.
///
/// Appears automatically when the active session has [WorktreeInfo] (i.e. has
/// used at least one worktree tool).  The user can list, create, merge, and
/// remove worktrees without leaving the chat.
class WorktreePanel extends StatefulWidget {
  const WorktreePanel({super.key});

  @override
  State<WorktreePanel> createState() => _WorktreePanelState();
}

class _WorktreePanelState extends State<WorktreePanel> {
  List<Map<String, dynamic>>? _worktrees;
  bool _loading = false;
  String? _error;

  /// Composite key of the last worktreeInfo we fetched for.
  /// Changing when workerId, repoPath, or lastAction changes so we
  /// re-fetch automatically on any relevant session_update.
  String? _lastFetchedKey;

  // -------------------------------------------------------------------------
  // HTTP helpers
  // -------------------------------------------------------------------------

  Future<void> _refresh(AppState appState, String workerId, String repoPath) async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final worker = appState.getWorker(workerId);
      if (worker == null) return;
      final result = await worker.ws.listWorktrees(repoPath);
      if (mounted) {
        setState(() {
          _worktrees = (result['worktrees'] as List<dynamic>? ?? [])
              .cast<Map<String, dynamic>>();
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = e.toString());
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _create(
      AppState appState, String workerId, String repoPath) async {
    final params = await _showCreateDialog(context);
    if (params == null) return;
    setState(() => _loading = true);
    try {
      final worker = appState.getWorker(workerId);
      if (worker == null) return;
      await worker.ws.createWorktree(
          branch: params.branch, repoPath: repoPath, base: params.base);
      await _refresh(appState, workerId, repoPath);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Create failed: $e')));
        setState(() => _loading = false);
      }
    }
  }

  Future<void> _merge(AppState appState, String workerId, String repoPath,
      String name) async {
    final message = await _showMergeDialog(context, name);
    if (message == null) return;
    setState(() => _loading = true);
    try {
      final worker = appState.getWorker(workerId);
      if (worker == null) return;
      await worker.ws
          .mergeWorktree(name: name, message: message, repoPath: repoPath);
      await _refresh(appState, workerId, repoPath);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Merge failed: $e')));
        setState(() => _loading = false);
      }
    }
  }

  Future<void> _setWorktree(AppState appState, String workerId, String paneSessionId, Map<String, dynamic> wt) async {
    final path = wt['path'] as String? ?? '';
    final worker = appState.getWorker(workerId);
    if (worker == null) return;
    setState(() => _loading = true);
    try {
      await worker.ws.setSessionWorktree(paneSessionId, path);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Failed to select worktree: $e')));
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _clearWorktree(AppState appState, String workerId, String paneSessionId) async {
    final worker = appState.getWorker(workerId);
    if (worker == null) return;
    setState(() => _loading = true);
    try {
      await worker.ws.setSessionWorktree(paneSessionId, null);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Failed to clear worktree: $e')));
      }
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _remove(AppState appState, String workerId, String repoPath,
      String name) async {
    final confirmed = await _confirmRemove(context, name);
    if (!confirmed) return;
    setState(() => _loading = true);
    try {
      final worker = appState.getWorker(workerId);
      if (worker == null) return;
      await worker.ws.removeWorktree(name: name, repoPath: repoPath);
      await _refresh(appState, workerId, repoPath);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Remove failed: $e')));
        setState(() => _loading = false);
      }
    }
  }

  // -------------------------------------------------------------------------
  // Build
  // -------------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    final pane = context.watch<PaneState>();
    final appState = context.watch<AppState>();
    final worktreeInfo = pane.currentWorktreeInfo;
    final workerId = pane.workerId;
    final sessionId = pane.sessionId;
    final selectedWorktreePath = pane.currentSelectedWorktreePath;

    // Auto-refresh whenever worktreeInfo or worker changes.
    // The key encodes workerId + repoPath + lastAction so any relevant
    // session_update (new/merge/rm/list) triggers a fresh fetch.
    if (worktreeInfo != null && workerId != null && !_loading) {
      final key = '$workerId:${worktreeInfo.repoPath}:${worktreeInfo.lastAction}';
      if (key != _lastFetchedKey) {
        _lastFetchedKey = key;
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) _refresh(appState, workerId, worktreeInfo.repoPath);
        });
      }
    }

    // Empty state — no worktree context yet for this session.
    if (worktreeInfo == null || workerId == null) {
      return _buildNoWorktreeState(context, pane);
    }

    final repoPath = worktreeInfo.repoPath;
    final shortRepo = repoPath.split('/').last;

    return Container(
      color: context.appColors.bgSurface,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Header
          Container(
            height: 36,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            decoration: BoxDecoration(
              border: Border(
                  bottom: BorderSide(color: context.appColors.divider)),
            ),
            child: Row(
              children: [
                Icon(Icons.device_hub_outlined,
                    color: context.appColors.accent, size: 16),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    shortRepo,
                    style: TextStyle(
                      color: context.appColors.textPrimary,
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                // Refresh
                _SmallIconBtn(
                  icon: Icons.refresh,
                  tooltip: 'Refresh',
                  onTap: _loading
                      ? null
                      : () => _refresh(appState, workerId, repoPath),
                ),
                // New worktree
                _SmallIconBtn(
                  icon: Icons.add,
                  tooltip: 'New worktree',
                  onTap: _loading
                      ? null
                      : () => _create(appState, workerId, repoPath),
                ),
                // Close panel
                _SmallIconBtn(
                  icon: Icons.close_rounded,
                  tooltip: 'Hide',
                  onTap: () => pane.toggleRightPanel('worktree'),
                ),
              ],
            ),
          ),
          // Current branch info
          Container(
            padding:
                const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            decoration: BoxDecoration(
              color: context.appColors.accent.withAlpha(10),
              border: Border(
                  bottom: BorderSide(color: context.appColors.divider)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(Icons.call_split,
                        size: 12, color: context.appColors.textMuted),
                    const SizedBox(width: 4),
                    Expanded(
                      child: Text(
                        worktreeInfo.branch != null
                            ? '${worktreeInfo.branch} → ${worktreeInfo.base ?? "main"}'
                            : repoPath,
                        style: TextStyle(
                            color: context.appColors.textSecondary,
                            fontSize: 11),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
                if (selectedWorktreePath != null) ...[
                  const SizedBox(height: 4),
                  Row(
                    children: [
                      Icon(Icons.check_circle, size: 11, color: context.appColors.accent),
                      const SizedBox(width: 4),
                      Expanded(
                        child: Text(
                          'Active: ${selectedWorktreePath.split('/').last}',
                          style: TextStyle(
                            color: context.appColors.accent,
                            fontSize: 10,
                            fontWeight: FontWeight.w600,
                          ),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      if (sessionId != null && workerId != null)
                        _SmallIconBtn(
                          icon: Icons.close,
                          tooltip: 'Clear worktree selection',
                          iconSize: 11,
                          onTap: _loading ? null : () => _clearWorktree(appState, workerId, sessionId),
                        ),
                    ],
                  ),
                ],
              ],
            ),
          ),
          // Worktree list
          Expanded(
            child: _buildList(context, appState, workerId, repoPath,
                selectedWorktreePath: selectedWorktreePath, sessionId: sessionId),
          ),
        ],
      ),
    );
  }

  Widget _buildList(BuildContext context, AppState appState, String workerId,
      String repoPath, {String? selectedWorktreePath, String? sessionId}) {
    if (_loading) {
      return const Padding(
        padding: EdgeInsets.all(12),
        child: LinearProgressIndicator(),
      );
    }
    if (_error != null) {
      return Padding(
        padding: const EdgeInsets.all(12),
        child: Text(_error!,
            style: TextStyle(
                color: context.appColors.errorText, fontSize: 12)),
      );
    }
    if (_worktrees == null) {
      return Center(
        child: TextButton.icon(
          onPressed: () => _refresh(appState, workerId, repoPath),
          icon: const Icon(Icons.refresh, size: 16),
          label: const Text('Load'),
        ),
      );
    }
    if (_worktrees!.isEmpty) {
      return Center(
        child: Text('No worktrees',
            style: TextStyle(
                color: context.appColors.textMuted, fontSize: 12)),
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.symmetric(vertical: 4),
      itemCount: _worktrees!.length,
      itemBuilder: (context, i) {
        final wt = _worktrees![i];
        return _buildItem(context, appState, workerId, repoPath, wt,
            selectedWorktreePath: selectedWorktreePath, sessionId: sessionId);
      },
    );
  }

  Widget _buildItem(BuildContext context, AppState appState, String workerId,
      String repoPath, Map<String, dynamic> wt, {String? selectedWorktreePath, String? sessionId}) {
    final name = wt['name'] as String? ?? '';
    final branch = wt['branch'] as String? ?? '';
    final base = wt['base'] as String? ?? 'main';
    final path = wt['path'] as String? ?? '';
    final isSelected = selectedWorktreePath != null && selectedWorktreePath == path;

    return InkWell(
      onTap: (sessionId != null && workerId.isNotEmpty)
          ? () => isSelected
              ? _clearWorktree(appState, workerId, sessionId)
              : _setWorktree(appState, workerId, sessionId, wt)
          : null,
      child: Container(
        color: isSelected ? context.appColors.accent.withAlpha(18) : null,
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
        child: Row(
          children: [
            Icon(
              isSelected ? Icons.check_circle : Icons.call_split,
              size: 13,
              color: isSelected ? context.appColors.accent : context.appColors.textMuted,
            ),
            const SizedBox(width: 6),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(name,
                      style: TextStyle(
                          color: isSelected
                              ? context.appColors.accent
                              : context.appColors.textPrimary,
                          fontSize: 12,
                          fontWeight: isSelected ? FontWeight.w600 : FontWeight.w500),
                      overflow: TextOverflow.ellipsis),
                  Text('$branch → $base',
                      style: TextStyle(
                          color: context.appColors.textMuted, fontSize: 10),
                      overflow: TextOverflow.ellipsis),
                ],
              ),
            ),
            _SmallIconBtn(
              icon: Icons.merge,
              tooltip: 'Merge into $base',
              iconSize: 15,
              onTap: () => _merge(appState, workerId, repoPath, name),
            ),
            _SmallIconBtn(
              icon: Icons.delete_outline,
              tooltip: 'Remove (discard)',
              iconSize: 15,
              color: context.appColors.errorText,
              onTap: () => _remove(appState, workerId, repoPath, name),
            ),
          ],
        ),
      ),
    );
  }

  // -------------------------------------------------------------------------
  // Empty state
  // -------------------------------------------------------------------------

  Widget _buildNoWorktreeState(BuildContext context, PaneState pane) {
    return Container(
      color: context.appColors.bgSurface,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Header (same chrome as the populated state)
          Container(
            height: 36,
            padding: const EdgeInsets.symmetric(horizontal: 12),
            decoration: BoxDecoration(
              border: Border(
                  bottom: BorderSide(color: context.appColors.divider)),
            ),
            child: Row(
              children: [
                Icon(Icons.device_hub_outlined,
                    color: context.appColors.accent, size: 16),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    'Worktree',
                    style: TextStyle(
                      color: context.appColors.textPrimary,
                      fontSize: 13,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                _SmallIconBtn(
                  icon: Icons.close_rounded,
                  tooltip: 'Hide',
                  onTap: () => pane.toggleRightPanel('worktree'),
                ),
              ],
            ),
          ),
          // Placeholder body
          Expanded(
            child: Center(
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.device_hub_outlined,
                        color: context.appColors.textMuted, size: 36),
                    const SizedBox(height: 12),
                    Text(
                      'No worktree context',
                      style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 6),
                    Text(
                      'Use a worktree tool in this session to start managing branches here.',
                      textAlign: TextAlign.center,
                      style: TextStyle(
                        color: context.appColors.textMuted,
                        fontSize: 12,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  // -------------------------------------------------------------------------
  // Dialogs
  // -------------------------------------------------------------------------

  Future<_CreateParams?> _showCreateDialog(BuildContext context) async {
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
                decoration:
                    const InputDecoration(labelText: 'Base branch'),
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
            style: TextButton.styleFrom(foregroundColor: Colors.red),
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

class _CreateParams {
  final String branch;
  final String base;
  const _CreateParams({required this.branch, required this.base});
}

class _SmallIconBtn extends StatelessWidget {
  final IconData icon;
  final String tooltip;
  final VoidCallback? onTap;
  final double iconSize;
  final Color? color;

  const _SmallIconBtn({
    required this.icon,
    required this.tooltip,
    this.onTap,
    this.iconSize = 14,
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
