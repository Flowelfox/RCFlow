import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';

/// Right-side dockable panel showing project information for the active session.
///
/// **Global mode** (no @ProjectName used yet): shows a tip prompting the user
/// to use @syntax to attach a project.
///
/// **Project mode** (@ProjectName has been used): shows the attached project
/// name, its git worktrees (create / merge / remove), and a list of artifacts
/// discovered under that project directory.
class ProjectPanel extends StatefulWidget {
  const ProjectPanel({super.key});

  @override
  State<ProjectPanel> createState() => _ProjectPanelState();
}

class _ProjectPanelState extends State<ProjectPanel> {
  List<Map<String, dynamic>>? _worktrees;
  List<Map<String, dynamic>>? _artifacts;
  bool _loadingWorktrees = false;
  bool _loadingArtifacts = false;
  String? _worktreesError;
  String? _artifactsError;

  /// Cache key: reloads when the project path or workerId changes.
  String? _lastFetchedKey;

  // Section order and collapse state
  final List<String> _sectionOrder = ['worktrees', 'artifacts'];
  final Map<String, bool> _sectionCollapsed = {
    'worktrees': false,
    'artifacts': false,
  };

  // ---------------------------------------------------------------------------
  // Data fetching
  // ---------------------------------------------------------------------------

  Future<void> _refresh(
      AppState appState, String workerId, String projectPath) async {
    // Only show loading spinners for data not yet populated from cache.
    // When cached data is already present this becomes a silent background
    // refresh — the user sees the stale data immediately while fresh data
    // replaces it, instead of a blank loading state.
    if (mounted) {
      setState(() {
        if (_worktrees == null) _loadingWorktrees = true;
        if (_artifacts == null) _loadingArtifacts = true;
        _worktreesError = null;
        _artifactsError = null;
      });
    }

    final worker = appState.getWorker(workerId);
    if (worker == null) {
      if (mounted) {
        setState(() {
          _loadingWorktrees = false;
          _loadingArtifacts = false;
        });
      }
      return;
    }

    final projectName = projectPath.split('/').last;

    // Local captures so we can persist to cache even if the widget is
    // disposed before the fetch completes (mounted check would block it).
    List<Map<String, dynamic>>? fetchedWorktrees;
    List<Map<String, dynamic>>? fetchedArtifacts;

    // Fetch worktrees and artifacts in parallel
    await Future.wait([
      worker.ws.listWorktrees(projectPath).then((result) {
        fetchedWorktrees = (result['worktrees'] as List<dynamic>? ?? [])
            .cast<Map<String, dynamic>>();
        if (mounted) {
          setState(() {
            _worktrees = fetchedWorktrees;
            _loadingWorktrees = false;
          });
        }
      }).catchError((e) {
        if (mounted) {
          setState(() {
            _worktreesError = e.toString();
            _loadingWorktrees = false;
          });
        }
      }),
      worker.ws.listProjectArtifacts(projectName).then((result) {
        fetchedArtifacts = (result['artifacts'] as List<dynamic>? ?? [])
            .cast<Map<String, dynamic>>();
        if (mounted) {
          setState(() {
            _artifacts = fetchedArtifacts;
            _loadingArtifacts = false;
          });
        }
      }).catchError((e) {
        if (mounted) {
          setState(() {
            _artifactsError = e.toString();
            _loadingArtifacts = false;
          });
        }
      }),
    ]);

    // Always persist freshly fetched data to cache — even when the widget
    // was disposed during the fetch — so the next open shows data immediately
    // without a loading spinner.  Uses the appState parameter directly to
    // avoid needing a BuildContext after an await.
    if (fetchedWorktrees != null || fetchedArtifacts != null) {
      appState.setProjectDataCache(
        '$workerId:$projectPath',
        worktrees: fetchedWorktrees,
        artifacts: fetchedArtifacts,
      );
    }
  }

  Future<void> _create(
      AppState appState, String workerId, String projectPath) async {
    final params = await _showCreateDialog(context);
    if (params == null) return;
    setState(() => _loadingWorktrees = true);
    try {
      final worker = appState.getWorker(workerId);
      if (worker == null) return;
      await worker.ws.createWorktree(
          branch: params.branch, repoPath: projectPath, base: params.base);
      await _refreshWorktrees(appState, workerId, projectPath);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Create failed: $e')));
        setState(() => _loadingWorktrees = false);
      }
    }
  }

  Future<void> _refreshWorktrees(
      AppState appState, String workerId, String projectPath) async {
    setState(() {
      _loadingWorktrees = true;
      _worktreesError = null;
    });
    try {
      final worker = appState.getWorker(workerId);
      if (worker == null) return;
      final result = await worker.ws.listWorktrees(projectPath);
      if (mounted) {
        setState(() {
          _worktrees = (result['worktrees'] as List<dynamic>? ?? [])
              .cast<Map<String, dynamic>>();
        });
      }
    } catch (e) {
      if (mounted) setState(() => _worktreesError = e.toString());
    } finally {
      if (mounted) setState(() => _loadingWorktrees = false);
    }
  }

  Future<void> _merge(AppState appState, String workerId, String projectPath,
      String name) async {
    final message = await _showMergeDialog(context, name);
    if (message == null) return;
    setState(() => _loadingWorktrees = true);
    try {
      final worker = appState.getWorker(workerId);
      if (worker == null) return;
      await worker.ws
          .mergeWorktree(name: name, message: message, repoPath: projectPath);
      await _refreshWorktrees(appState, workerId, projectPath);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Merge failed: $e')));
        setState(() => _loadingWorktrees = false);
      }
    }
  }

  Future<void> _remove(AppState appState, String workerId, String projectPath,
      String name) async {
    final confirmed = await _confirmRemove(context, name);
    if (!confirmed) return;
    setState(() => _loadingWorktrees = true);
    try {
      final worker = appState.getWorker(workerId);
      if (worker == null) return;
      await worker.ws.removeWorktree(name: name, repoPath: projectPath);
      await _refreshWorktrees(appState, workerId, projectPath);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text('Remove failed: $e')));
        setState(() => _loadingWorktrees = false);
      }
    }
  }

  Future<void> _setWorktree(AppState appState, String workerId,
      String sessionId, Map<String, dynamic> wt) async {
    final path = wt['path'] as String? ?? '';
    final worker = appState.getWorker(workerId);
    if (worker == null) return;
    setState(() => _loadingWorktrees = true);
    try {
      await worker.ws.setSessionWorktree(sessionId, path);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Failed to select worktree: $e')));
      }
    } finally {
      if (mounted) setState(() => _loadingWorktrees = false);
    }
  }

  Future<void> _clearWorktree(
      AppState appState, String workerId, String sessionId) async {
    final worker = appState.getWorker(workerId);
    if (worker == null) return;
    setState(() => _loadingWorktrees = true);
    try {
      await worker.ws.setSessionWorktree(sessionId, null);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(content: Text('Failed to clear worktree: $e')));
      }
    } finally {
      if (mounted) setState(() => _loadingWorktrees = false);
    }
  }

  // ---------------------------------------------------------------------------
  // Build
  // ---------------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    final pane = context.watch<PaneState>();
    final appState = context.watch<AppState>();
    final mainProjectPath = pane.effectiveProjectPath;
    final workerId = pane.workerId ?? appState.defaultWorkerId;
    final sessionId = pane.sessionId;
    final selectedWorktreePath = pane.currentSelectedWorktreePath;

    // Guard: no worker connected yet
    if (workerId == null) {
      return _buildGlobalState(context, pane);
    }

    // Auto-refresh when project path, worker, OR worktree operation changes.
    final worktreeLastAction = pane.currentWorktreeInfo?.lastAction;
    if (mainProjectPath != null) {
      final fetchKey =
          '$workerId:$mainProjectPath:${worktreeLastAction ?? ''}';
      final cacheKey = '$workerId:$mainProjectPath';
      if (fetchKey != _lastFetchedKey &&
          !_loadingWorktrees &&
          !_loadingArtifacts) {
        _lastFetchedKey = fetchKey;
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (!mounted) return;
          final cached = appState.getProjectDataCache(cacheKey);
          if (cached != null) {
            setState(() {
              _worktrees ??= cached.worktrees;
              _artifacts ??= cached.artifacts;
            });
          }
          _refresh(appState, workerId, mainProjectPath);
        });
      }
    }

    // Global mode — no project attached yet.
    if (mainProjectPath == null) {
      return _buildGlobalState(context, pane);
    }

    final projectName = mainProjectPath.split('/').last;

    return Container(
      color: context.appColors.bgSurface,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          // Header
          _buildHeader(context, pane, projectName, appState, workerId,
              mainProjectPath),
          // Sections — collapsible and reorderable
          Expanded(
            child: SingleChildScrollView(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  for (int i = 0; i < _sectionOrder.length; i++)
                    _buildSection(
                      context,
                      sectionId: _sectionOrder[i],
                      index: i,
                      appState: appState,
                      workerId: workerId,
                      mainProjectPath: mainProjectPath,
                      selectedWorktreePath: selectedWorktreePath,
                      sessionId: sessionId,
                    ),
                ],
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildSection(
    BuildContext context, {
    required String sectionId,
    required int index,
    required AppState appState,
    required String workerId,
    required String mainProjectPath,
    required String? selectedWorktreePath,
    required String? sessionId,
  }) {
    final collapsed = _sectionCollapsed[sectionId] ?? false;
    final IconData icon;
    final String label;
    Widget? trailing;
    Widget body;

    if (sectionId == 'worktrees') {
      icon = Icons.device_hub_outlined;
      label = 'Worktrees';
      trailing = Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          _SmallIconBtn(
            icon: Icons.refresh,
            tooltip: 'Refresh',
            onTap: (_loadingWorktrees || _loadingArtifacts)
                ? null
                : () => _refresh(appState, workerId, mainProjectPath),
          ),
          _SmallIconBtn(
            icon: Icons.add,
            tooltip: 'New worktree',
            onTap: _loadingWorktrees
                ? null
                : () => _create(appState, workerId, mainProjectPath),
          ),
        ],
      );
      body = Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (selectedWorktreePath != null)
            _buildActiveWorktreeBar(
                context, appState, workerId, sessionId, selectedWorktreePath),
          SizedBox(
            height: 160,
            child: _buildWorktreeList(context, appState, workerId,
                mainProjectPath, selectedWorktreePath, sessionId),
          ),
        ],
      );
    } else {
      icon = Icons.insert_drive_file_outlined;
      label = 'Artifacts';
      trailing = null;
      body = SizedBox(
        height: 200,
        child: _buildArtifactList(context),
      );
    }

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _buildCollapsibleSectionHeader(
          context,
          icon: icon,
          label: label,
          sectionId: sectionId,
          index: index,
          trailing: trailing,
        ),
        AnimatedSize(
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeInOut,
          child: collapsed ? const SizedBox.shrink() : body,
        ),
        Divider(height: 1, color: context.appColors.divider),
      ],
    );
  }

  Widget _buildCollapsibleSectionHeader(
    BuildContext context, {
    required IconData icon,
    required String label,
    required String sectionId,
    required int index,
    Widget? trailing,
  }) {
    final collapsed = _sectionCollapsed[sectionId] ?? false;
    final isFirst = index == 0;
    final isLast = index == _sectionOrder.length - 1;

    return GestureDetector(
      onTap: () =>
          setState(() => _sectionCollapsed[sectionId] = !collapsed),
      child: Container(
        height: 28,
        padding: const EdgeInsets.symmetric(horizontal: 10),
        decoration: BoxDecoration(
          color: context.appColors.accent.withAlpha(8),
          border:
              Border(bottom: BorderSide(color: context.appColors.divider)),
        ),
        child: Row(
          children: [
            AnimatedRotation(
              turns: collapsed ? -0.25 : 0,
              duration: const Duration(milliseconds: 150),
              child: Icon(Icons.expand_more,
                  size: 14, color: context.appColors.textMuted),
            ),
            const SizedBox(width: 4),
            Icon(icon, size: 12, color: context.appColors.textMuted),
            const SizedBox(width: 5),
            Text(
              label,
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 11,
                fontWeight: FontWeight.w600,
                letterSpacing: 0.5,
              ),
            ),
            const Spacer(),
            if (trailing != null) trailing,
            _SmallIconBtn(
              icon: Icons.arrow_upward,
              tooltip: 'Move section up',
              iconSize: 12,
              onTap: isFirst
                  ? null
                  : () => setState(() {
                        final tmp = _sectionOrder[index - 1];
                        _sectionOrder[index - 1] = _sectionOrder[index];
                        _sectionOrder[index] = tmp;
                      }),
            ),
            _SmallIconBtn(
              icon: Icons.arrow_downward,
              tooltip: 'Move section down',
              iconSize: 12,
              onTap: isLast
                  ? null
                  : () => setState(() {
                        final tmp = _sectionOrder[index + 1];
                        _sectionOrder[index + 1] = _sectionOrder[index];
                        _sectionOrder[index] = tmp;
                      }),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildHeader(BuildContext context, PaneState pane, String projectName,
      AppState appState, String workerId, String projectPath) {
    return Container(
      height: 36,
      padding: const EdgeInsets.symmetric(horizontal: 12),
      decoration: BoxDecoration(
        border:
            Border(bottom: BorderSide(color: context.appColors.divider)),
      ),
      child: Row(
        children: [
          Icon(Icons.folder_outlined,
              color: context.appColors.accent, size: 16),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              projectName,
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
                fontWeight: FontWeight.w600,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          _SmallIconBtn(
            icon: Icons.close_rounded,
            tooltip: 'Hide',
            onTap: () => pane.toggleRightPanel('project'),
          ),
        ],
      ),
    );
  }

  Widget _buildActiveWorktreeBar(BuildContext context, AppState appState,
      String workerId, String? sessionId, String selectedWorktreePath) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
      decoration: BoxDecoration(
        color: context.appColors.accent.withAlpha(10),
        border:
            Border(bottom: BorderSide(color: context.appColors.divider)),
      ),
      child: Row(
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
          if (sessionId != null)
            _SmallIconBtn(
              icon: Icons.close,
              tooltip: 'Clear worktree selection',
              iconSize: 11,
              onTap: _loadingWorktrees
                  ? null
                  : () => _clearWorktree(appState, workerId, sessionId),
            ),
        ],
      ),
    );
  }

  Widget _buildWorktreeList(
      BuildContext context,
      AppState appState,
      String workerId,
      String projectPath,
      String? selectedWorktreePath,
      String? sessionId) {
    if (_loadingWorktrees) {
      return Align(
        alignment: Alignment.topCenter,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: LinearProgressIndicator(
            backgroundColor: context.appColors.bgElevated,
          ),
        ),
      );
    }
    if (_worktreesError != null) {
      return Padding(
        padding: const EdgeInsets.all(12),
        child: Text(_worktreesError!,
            style: TextStyle(
                color: context.appColors.errorText, fontSize: 11)),
      );
    }
    if (_worktrees == null) {
      return Center(
        child: TextButton.icon(
          onPressed: () => _refreshWorktrees(appState, workerId, projectPath),
          icon: const Icon(Icons.refresh, size: 14),
          label: const Text('Load'),
        ),
      );
    }
    if (_worktrees!.isEmpty) {
      return Center(
        child: Text('No worktrees',
            style: TextStyle(
                color: context.appColors.textMuted, fontSize: 11)),
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.symmetric(vertical: 2),
      itemCount: _worktrees!.length,
      itemBuilder: (context, i) {
        final wt = _worktrees![i];
        final name = wt['name'] as String? ?? '';
        final branch = wt['branch'] as String? ?? '';
        final base = wt['base'] as String? ?? 'main';
        final path = wt['path'] as String? ?? '';
        final isSelected =
            selectedWorktreePath != null && selectedWorktreePath == path;
        return InkWell(
          onTap: (sessionId != null && workerId.isNotEmpty)
              ? () => isSelected
                  ? _clearWorktree(appState, workerId, sessionId)
                  : _setWorktree(appState, workerId, sessionId, wt)
              : null,
          child: Container(
            color: isSelected ? context.appColors.accent.withAlpha(18) : null,
            padding:
                const EdgeInsets.symmetric(horizontal: 10, vertical: 3),
            child: Row(
              children: [
                Icon(
                  isSelected ? Icons.check_circle : Icons.call_split,
                  size: 12,
                  color: isSelected
                      ? context.appColors.accent
                      : context.appColors.textMuted,
                ),
                const SizedBox(width: 5),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(name,
                          style: TextStyle(
                              color: isSelected
                                  ? context.appColors.accent
                                  : context.appColors.textPrimary,
                              fontSize: 11,
                              fontWeight: isSelected
                                  ? FontWeight.w600
                                  : FontWeight.w500),
                          overflow: TextOverflow.ellipsis),
                      Text('$branch → $base',
                          style: TextStyle(
                              color: context.appColors.textMuted,
                              fontSize: 10),
                          overflow: TextOverflow.ellipsis),
                    ],
                  ),
                ),
                _SmallIconBtn(
                  icon: Icons.merge,
                  tooltip: 'Merge into $base',
                  iconSize: 13,
                  onTap: () =>
                      _merge(appState, workerId, projectPath, name),
                ),
                _SmallIconBtn(
                  icon: Icons.delete_outline,
                  tooltip: 'Remove (discard)',
                  iconSize: 13,
                  color: context.appColors.errorText,
                  onTap: () =>
                      _remove(appState, workerId, projectPath, name),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  Widget _buildArtifactList(BuildContext context) {
    if (_loadingArtifacts) {
      return Align(
        alignment: Alignment.topCenter,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: LinearProgressIndicator(
            backgroundColor: context.appColors.bgElevated,
          ),
        ),
      );
    }
    if (_artifactsError != null) {
      return Padding(
        padding: const EdgeInsets.all(12),
        child: Text(_artifactsError!,
            style: TextStyle(
                color: context.appColors.errorText, fontSize: 11)),
      );
    }
    if (_artifacts == null || _artifacts!.isEmpty) {
      return Center(
        child: Text(
          _artifacts == null ? 'Not loaded' : 'No artifacts',
          style: TextStyle(
              color: context.appColors.textMuted, fontSize: 11),
        ),
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.symmetric(vertical: 2),
      itemCount: _artifacts!.length,
      itemBuilder: (context, i) {
        final a = _artifacts![i];
        final name = a['file_name'] as String? ?? '';
        final ext = a['file_extension'] as String? ?? '';
        final mime = a['mime_type'] as String? ?? '';
        final size = a['file_size'] as int? ?? 0;
        return Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          child: Row(
            children: [
              Icon(_fileIcon(ext, mime),
                  size: 13, color: context.appColors.textMuted),
              const SizedBox(width: 6),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(name,
                        style: TextStyle(
                            color: context.appColors.textPrimary,
                            fontSize: 11,
                            fontWeight: FontWeight.w500),
                        overflow: TextOverflow.ellipsis),
                    Text(_formatSize(size),
                        style: TextStyle(
                            color: context.appColors.textMuted,
                            fontSize: 10)),
                  ],
                ),
              ),
            ],
          ),
        );
      },
    );
  }

  // ---------------------------------------------------------------------------
  // Global (no project) state
  // ---------------------------------------------------------------------------

  Widget _buildGlobalState(BuildContext context, PaneState pane) {
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
                Icon(Icons.folder_outlined,
                    color: context.appColors.accent, size: 16),
                const SizedBox(width: 6),
                Expanded(
                  child: Text(
                    'Project',
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
                  onTap: () => pane.toggleRightPanel('project'),
                ),
              ],
            ),
          ),
          // Tip body
          Expanded(
            child: Center(
              child: Padding(
                padding: const EdgeInsets.all(20),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.folder_open_outlined,
                        color: context.appColors.textMuted, size: 36),
                    const SizedBox(height: 12),
                    Text(
                      'No project attached',
                      style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 8),
                    Text(
                      'Type @ProjectName in the input field to attach a project. Worktrees and artifacts will appear here.',
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

  // ---------------------------------------------------------------------------
  // Dialogs
  // ---------------------------------------------------------------------------

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

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  IconData _fileIcon(String ext, String mime) {
    if (mime.startsWith('image/')) return Icons.image_outlined;
    if (mime.startsWith('video/')) return Icons.videocam_outlined;
    if (mime.startsWith('audio/')) return Icons.audiotrack_outlined;
    switch (ext.toLowerCase()) {
      case '.py':
        return Icons.code;
      case '.dart':
        return Icons.code;
      case '.js':
      case '.ts':
      case '.jsx':
      case '.tsx':
        return Icons.code;
      case '.json':
      case '.yaml':
      case '.yml':
      case '.toml':
        return Icons.data_object_outlined;
      case '.md':
      case '.txt':
        return Icons.description_outlined;
      case '.pdf':
        return Icons.picture_as_pdf_outlined;
      default:
        return Icons.insert_drive_file_outlined;
    }
  }

  String _formatSize(int bytes) {
    if (bytes < 1024) return '$bytes B';
    if (bytes < 1024 * 1024) return '${(bytes / 1024).toStringAsFixed(1)} KB';
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
  }
}

// ---------------------------------------------------------------------------
// Private helpers
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
