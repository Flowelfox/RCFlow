import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/artifact_info.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import 'helpers.dart';

/// Artifact list panel for the sidebar Artifacts tab.
///
/// Groups artifacts by worker, then by project within each worker.
/// Artifacts not belonging to any project are shown under "Other".
class ArtifactListPanel extends StatefulWidget {
  final VoidCallback? onArtifactSelected;

  const ArtifactListPanel({super.key, this.onArtifactSelected});

  @override
  State<ArtifactListPanel> createState() => _ArtifactListPanelState();
}

class _ArtifactListPanelState extends State<ArtifactListPanel> {
  final TextEditingController _searchController = TextEditingController();
  String _searchQuery = '';
  bool _groupByProject = true;
  final Set<String> _expandedWorkers = {};
  final Set<String> _expandedProjects = {};
  bool _initialized = false;
  bool _rechecking = false;

  @override
  void initState() {
    super.initState();
    final settings = Provider.of<AppState>(context, listen: false).settings;
    _searchQuery = settings.artifactsFilterSearch;
    _searchController.text = _searchQuery;
    _groupByProject = settings.artifactsGroupByProject;
    final savedWorkers = settings.artifactsExpandedWorkers;
    final savedProjects = settings.artifactsExpandedProjects;
    if (savedWorkers != null) {
      _expandedWorkers.addAll(savedWorkers);
      _initialized = true;
    }
    if (savedProjects != null) {
      _expandedProjects.addAll(savedProjects);
    }
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  void _saveFilters() {
    final settings = Provider.of<AppState>(context, listen: false).settings;
    settings.artifactsFilterSearch = _searchQuery;
  }

  Future<void> _recheckArtifacts(AppState state) async {
    if (_rechecking) return;
    setState(() => _rechecking = true);
    try {
      final workers = state.workerConfigs
          .map((c) => state.getWorker(c.id))
          .where((w) => w != null && w.isConnected);
      await Future.wait(workers.map((w) => w!.ws.recheckArtifacts()));
    } catch (e) {
      if (mounted) {
        state.addSystemMessage('Artifact recheck failed: $e', isError: true);
      }
    } finally {
      if (mounted) setState(() => _rechecking = false);
    }
  }

  void _saveExpandedState() {
    final settings = Provider.of<AppState>(context, listen: false).settings;
    settings.artifactsExpandedWorkers = _expandedWorkers.toList();
    settings.artifactsExpandedProjects = _expandedProjects.toList();
  }

  List<ArtifactInfo> _filterArtifacts(List<ArtifactInfo> artifacts) {
    if (_searchQuery.isEmpty) return artifacts;
    final query = _searchQuery.toLowerCase();
    return artifacts.where((a) {
      return a.fileName.toLowerCase().contains(query) ||
          a.filePath.toLowerCase().contains(query) ||
          a.workerName.toLowerCase().contains(query);
    }).toList();
  }

  /// Build a grouped structure: workerId -> projectName -> artifacts.
  /// The key for "Other" (no project) is null.
  Map<String, Map<String?, List<ArtifactInfo>>> _groupArtifacts(
    List<ArtifactInfo> artifacts,
  ) {
    final grouped = <String, Map<String?, List<ArtifactInfo>>>{};
    for (final a in artifacts) {
      grouped
          .putIfAbsent(a.workerId, () => {})
          .putIfAbsent(a.projectName, () => [])
          .add(a);
    }
    return grouped;
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<AppState>(
      builder: (context, state, _) {
        final artifacts = state.artifacts;

        if (artifacts.isEmpty) {
          return Center(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(
                  Icons.article_outlined,
                  color: context.appColors.textMuted,
                  size: 40,
                ),
                const SizedBox(height: 12),
                Text(
                  'No artifacts yet',
                  style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 16,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 4),
                Text(
                  'Artifacts are extracted from\nsession messages',
                  textAlign: TextAlign.center,
                  style: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 13,
                  ),
                ),
              ],
            ),
          );
        }

        final filtered = _filterArtifacts(artifacts);
        final grouped = _groupArtifacts(filtered);
        final workerConfigs = state.workerConfigs;
        final hasMultipleWorkers = workerConfigs.length > 1;

        // Auto-expand all workers & projects on first build (no saved state)
        if (!_initialized) {
          _initialized = true;
          for (final config in workerConfigs) {
            _expandedWorkers.add(config.id);
          }
          // Expand all project groups
          for (final entry in grouped.entries) {
            for (final projectName in entry.value.keys) {
              _expandedProjects.add(_projectKey(entry.key, projectName));
            }
          }
          _saveExpandedState();
        }

        return Column(
          children: [
            _buildFilterBar(context),
            Expanded(
              child: filtered.isEmpty && _searchQuery.isNotEmpty
                  ? _buildNoResults(context)
                  : _buildGroupedList(
                      context,
                      state,
                      grouped,
                      workerConfigs,
                      hasMultipleWorkers,
                    ),
            ),
          ],
        );
      },
    );
  }

  /// Stable key for a project group: "workerId:projectName" or "workerId:_other".
  String _projectKey(String workerId, String? projectName) =>
      '$workerId:${projectName ?? '_other'}';

  Widget _buildGroupedList(
    BuildContext context,
    AppState state,
    Map<String, Map<String?, List<ArtifactInfo>>> grouped,
    List workerConfigs,
    bool hasMultipleWorkers,
  ) {
    final children = <Widget>[];

    // Sort workers by config order
    final workerOrder = <String, int>{};
    for (var i = 0; i < workerConfigs.length; i++) {
      workerOrder[workerConfigs[i].id] = i;
    }
    final sortedWorkerIds = grouped.keys.toList()
      ..sort(
        (a, b) => (workerOrder[a] ?? 999).compareTo(workerOrder[b] ?? 999),
      );

    for (final workerId in sortedWorkerIds) {
      final projectMap = grouped[workerId]!;
      final workerName = _findWorkerName(workerConfigs, workerId);
      final workerArtifactCount = projectMap.values.fold<int>(
        0,
        (sum, list) => sum + list.length,
      );
      final workerExpanded = _expandedWorkers.contains(workerId);

      if (hasMultipleWorkers) {
        children.add(
          _WorkerHeader(
            workerName: workerName,
            artifactCount: workerArtifactCount,
            expanded: workerExpanded,
            onToggle: () {
              setState(() {
                if (workerExpanded) {
                  _expandedWorkers.remove(workerId);
                } else {
                  _expandedWorkers.add(workerId);
                }
              });
              _saveExpandedState();
            },
          ),
        );
      }

      if (!hasMultipleWorkers || workerExpanded) {
        // Sort projects: named projects alphabetically, then "Other" last
        final projectNames = projectMap.keys.toList()
          ..sort((a, b) {
            if (a == null && b == null) return 0;
            if (a == null) return 1;
            if (b == null) return -1;
            return a.toLowerCase().compareTo(b.toLowerCase());
          });

        if (_groupByProject) {
          for (final projectName in projectNames) {
            final projectArtifacts = projectMap[projectName]!;
            final pKey = _projectKey(workerId, projectName);
            final projectExpanded = _expandedProjects.contains(pKey);

            children.add(
              _ProjectHeader(
                projectName: projectName,
                artifactCount: projectArtifacts.length,
                expanded: projectExpanded,
                indented: hasMultipleWorkers,
                onToggle: () {
                  setState(() {
                    if (projectExpanded) {
                      _expandedProjects.remove(pKey);
                    } else {
                      _expandedProjects.add(pKey);
                    }
                  });
                  _saveExpandedState();
                },
              ),
            );

            if (projectExpanded) {
              for (final artifact in projectArtifacts) {
                children.add(
                  _ArtifactTile(
                    artifact: artifact,
                    state: state,
                    onArtifactSelected: widget.onArtifactSelected,
                    indented: hasMultipleWorkers,
                  ),
                );
              }
            }
          }
        } else {
          // Flat mode: all artifacts under this worker shown without project headers
          final allArtifacts =
              projectNames.expand((p) => projectMap[p]!).toList()..sort(
                (a, b) => (b.discoveredAt ?? DateTime(2000)).compareTo(
                  a.discoveredAt ?? DateTime(2000),
                ),
              );
          for (final artifact in allArtifacts) {
            children.add(
              _ArtifactTile(
                artifact: artifact,
                state: state,
                onArtifactSelected: widget.onArtifactSelected,
                indented: hasMultipleWorkers,
              ),
            );
          }
        }
      }
    }

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: 4),
      children: children,
    );
  }

  String _findWorkerName(List workerConfigs, String workerId) {
    for (final config in workerConfigs) {
      if (config.id == workerId) return config.name;
    }
    return workerId;
  }

  Widget _buildFilterBar(BuildContext context) {
    final state = Provider.of<AppState>(context, listen: false);
    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 8, 10, 4),
      child: SizedBox(
        height: 30,
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: _searchController,
                onChanged: (v) {
                  setState(() => _searchQuery = v);
                  _saveFilters();
                },
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 12,
                ),
                decoration: InputDecoration(
                  hintText: 'Search artifacts...',
                  hintStyle: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 12,
                  ),
                  prefixIcon: Padding(
                    padding: const EdgeInsets.only(left: 8, right: 4),
                    child: Icon(
                      Icons.search_rounded,
                      color: context.appColors.textMuted,
                      size: 16,
                    ),
                  ),
                  prefixIconConstraints: const BoxConstraints(
                    maxWidth: 28,
                    maxHeight: 30,
                  ),
                  suffixIcon: _searchQuery.isNotEmpty
                      ? GestureDetector(
                          onTap: () {
                            _searchController.clear();
                            setState(() => _searchQuery = '');
                            _saveFilters();
                          },
                          child: Padding(
                            padding: const EdgeInsets.only(right: 6),
                            child: Icon(
                              Icons.close_rounded,
                              color: context.appColors.textMuted,
                              size: 14,
                            ),
                          ),
                        )
                      : null,
                  suffixIconConstraints: const BoxConstraints(
                    maxWidth: 24,
                    maxHeight: 30,
                  ),
                  filled: true,
                  fillColor: context.appColors.bgElevated,
                  contentPadding: const EdgeInsets.symmetric(
                    horizontal: 8,
                    vertical: 0,
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
            ),
            const SizedBox(width: 6),
            SizedBox(
              width: 30,
              height: 30,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.folder_copy_outlined,
                  color: _groupByProject
                      ? context.appColors.accent
                      : context.appColors.textSecondary,
                  size: 16,
                ),
                tooltip: _groupByProject
                    ? 'Grouping by project (tap to disable)'
                    : 'Group by project',
                onPressed: () {
                  setState(() => _groupByProject = !_groupByProject);
                  Provider.of<AppState>(
                    context,
                    listen: false,
                  ).settings.artifactsGroupByProject = _groupByProject;
                },
              ),
            ),
            const SizedBox(width: 4),
            SizedBox(
              width: 30,
              height: 30,
              child: _rechecking
                  ? Padding(
                      padding: const EdgeInsets.all(7),
                      child: SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(
                          strokeWidth: 1.5,
                          color: context.appColors.textMuted,
                        ),
                      ),
                    )
                  : IconButton(
                      padding: EdgeInsets.zero,
                      icon: Icon(
                        Icons.sync_rounded,
                        color: context.appColors.textSecondary,
                        size: 16,
                      ),
                      tooltip: 'Recheck artifact file existence',
                      onPressed: () => _recheckArtifacts(state),
                    ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildNoResults(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.search_off_rounded,
            color: context.appColors.textMuted,
            size: 32,
          ),
          const SizedBox(height: 8),
          Text(
            'No matching artifacts',
            style: TextStyle(
              color: context.appColors.textSecondary,
              fontSize: 13,
            ),
          ),
          const SizedBox(height: 4),
          GestureDetector(
            onTap: () {
              _searchController.clear();
              setState(() => _searchQuery = '');
              _saveFilters();
            },
            child: Text(
              'Clear search',
              style: TextStyle(color: context.appColors.accent, fontSize: 12),
            ),
          ),
        ],
      ),
    );
  }
}

/// Pure helper: builds the ordered flat list of visible [ArtifactInfo] items
/// given the current expansion/grouping state. Used by [ArtifactListPanel] for
/// Shift+click range selection.
///
/// When [hasMultipleWorkers] is false the [expandedWorkers] set is ignored and
/// all workers are treated as expanded.
List<ArtifactInfo> computeFlatVisibleArtifactList({
  required List<ArtifactInfo> filteredArtifacts,
  required Map<String, Map<String?, List<ArtifactInfo>>> grouped,
  required List<String> workerOrder,
  required bool hasMultipleWorkers,
  required Set<String> expandedWorkers,
  required bool groupByProject,
  required Set<String> expandedProjects,
  required String Function(String workerId, String? projectName) projectKey,
}) {
  if (filteredArtifacts.isEmpty) return const [];

  final result = <ArtifactInfo>[];

  // Follow worker order so the flat list matches visual render order.
  for (final workerId in workerOrder) {
    final projectMap = grouped[workerId];
    if (projectMap == null) continue;

    if (hasMultipleWorkers && !expandedWorkers.contains(workerId)) continue;

    if (groupByProject) {
      // Alphabetical project names, null ("Other") last — mirrors _buildGroupedList.
      final projectNames = projectMap.keys.toList()
        ..sort((a, b) {
          if (a == null && b == null) return 0;
          if (a == null) return 1;
          if (b == null) return -1;
          return a.toLowerCase().compareTo(b.toLowerCase());
        });

      for (final pName in projectNames) {
        final pKey = projectKey(workerId, pName);
        if (!expandedProjects.contains(pKey)) continue;
        result.addAll(projectMap[pName]!);
      }
    } else {
      // Flat mode: all artifacts sorted by discoveredAt descending.
      final all = projectMap.values.expand((list) => list).toList()
        ..sort(
          (a, b) => (b.discoveredAt ?? DateTime(2000))
              .compareTo(a.discoveredAt ?? DateTime(2000)),
        );
      result.addAll(all);
    }
  }

  return result;
}

/// Collapsible header for a worker group.
class _WorkerHeader extends StatelessWidget {
  final String workerName;
  final int artifactCount;
  final bool expanded;
  final VoidCallback onToggle;

  const _WorkerHeader({
    required this.workerName,
    required this.artifactCount,
    required this.expanded,
    required this.onToggle,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onToggle,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        child: Row(
          children: [
            Icon(
              expanded
                  ? Icons.expand_more_rounded
                  : Icons.chevron_right_rounded,
              color: context.appColors.textSecondary,
              size: 18,
            ),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                '$workerName ($artifactCount)',
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Collapsible header for a project group within a worker.
class _ProjectHeader extends StatelessWidget {
  final String? projectName;
  final int artifactCount;
  final bool expanded;
  final bool indented;
  final VoidCallback onToggle;

  const _ProjectHeader({
    required this.projectName,
    required this.artifactCount,
    required this.expanded,
    required this.indented,
    required this.onToggle,
  });

  @override
  Widget build(BuildContext context) {
    final label = projectName ?? 'Other';
    return InkWell(
      onTap: onToggle,
      child: Padding(
        padding: EdgeInsets.only(
          left: indented ? 32 : 16,
          right: 16,
          top: 4,
          bottom: 4,
        ),
        child: Row(
          children: [
            Icon(
              expanded
                  ? Icons.expand_more_rounded
                  : Icons.chevron_right_rounded,
              color: context.appColors.textMuted,
              size: 16,
            ),
            const SizedBox(width: 4),
            Icon(
              projectName != null
                  ? Icons.folder_outlined
                  : Icons.folder_off_outlined,
              color: context.appColors.textMuted,
              size: 14,
            ),
            const SizedBox(width: 4),
            Expanded(
              child: Text(
                '$label ($artifactCount)',
                style: TextStyle(
                  color: context.appColors.textSecondary,
                  fontSize: 11,
                  fontWeight: FontWeight.w600,
                ),
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _ArtifactTile extends StatelessWidget {
  final ArtifactInfo artifact;
  final AppState state;
  final VoidCallback? onArtifactSelected;
  final bool indented;

  const _ArtifactTile({
    required this.artifact,
    required this.state,
    this.onArtifactSelected,
    this.indented = false,
  });

  static const _extIcons = {
    '.md': Icons.description_outlined,
    '.markdown': Icons.description_outlined,
    '.py': Icons.code,
    '.js': Icons.javascript,
    '.ts': Icons.code,
    '.dart': Icons.code,
    '.json': Icons.data_object,
    '.yaml': Icons.settings,
    '.yml': Icons.settings,
    '.txt': Icons.text_snippet_outlined,
    '.log': Icons.text_snippet_outlined,
  };

  @override
  Widget build(BuildContext context) {
    final isViewed = _isArtifactViewed();
    final isActive = _isArtifactActive();
    final isMissing = !artifact.fileExists;
    final icon =
        _extIcons[artifact.fileExtension.toLowerCase()] ??
        Icons.insert_drive_file_outlined;

    return Container(
      decoration: BoxDecoration(
        color: isActive
            ? context.appColors.accent.withAlpha(25)
            : isViewed
            ? context.appColors.accent.withAlpha(12)
            : null,
        border: isActive
            ? Border(
                left: BorderSide(color: context.appColors.accent, width: 3),
              )
            : isViewed
            ? Border(
                left: BorderSide(
                  color: context.appColors.accent.withAlpha(80),
                  width: 2,
                ),
              )
            : null,
      ),
      child: ListTile(
        leading: Stack(
          children: [
            Container(
              width: 30,
              height: 30,
              decoration: BoxDecoration(
                color: isMissing
                    ? context.appColors.textMuted.withAlpha(30)
                    : context.appColors.accent.withAlpha(30),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Icon(
                icon,
                color: isMissing
                    ? context.appColors.textMuted
                    : context.appColors.accentLight,
                size: 16,
              ),
            ),
            if (isMissing)
              Positioned(
                right: 0,
                bottom: 0,
                child: Icon(
                  Icons.warning_amber_rounded,
                  color: Colors.orange.shade400,
                  size: 11,
                ),
              ),
          ],
        ),
        title: Text(
          artifact.fileName,
          style: TextStyle(
            color: isActive
                ? context.appColors.accentLight
                : isMissing
                ? context.appColors.textMuted
                : context.appColors.textPrimary,
            fontSize: 12,
            fontWeight: isActive ? FontWeight.w600 : FontWeight.w400,
          ),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        subtitle: Text(
          isMissing ? '${_subtitle()} · missing' : _subtitle(),
          style: TextStyle(
            color: isMissing
                ? Colors.orange.shade400.withAlpha(180)
                : context.appColors.textMuted,
            fontSize: 10,
          ),
        ),
        trailing: Text(
          artifact.displaySize,
          style: TextStyle(color: context.appColors.textMuted, fontSize: 10),
        ),
        dense: true,
        visualDensity: const VisualDensity(vertical: -4),
        contentPadding: EdgeInsets.only(left: indented ? 36 : 16, right: 8),
        onTap: () {
          state.openArtifactInPane(artifact.artifactId);
          onArtifactSelected?.call();
        },
      ),
    );
  }

  bool _isArtifactViewed() {
    for (final pane in state.panes.values) {
      if (pane.artifactId == artifact.artifactId) return true;
    }
    return false;
  }

  bool _isArtifactActive() {
    if (state.hasNoPanes) return false;
    return state.activePane.artifactId == artifact.artifactId;
  }

  String _subtitle() {
    final mod = artifact.discoveredAt;
    if (mod != null) {
      final local = mod.toLocal();
      return '${monthAbbr(local.month)} ${local.day}, '
          '${local.hour.toString().padLeft(2, '0')}:'
          '${local.minute.toString().padLeft(2, '0')}'
          ' \u00B7 ${artifact.workerName}';
    }
    return artifact.workerName;
  }
}
