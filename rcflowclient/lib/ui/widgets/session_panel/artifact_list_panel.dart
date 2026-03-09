import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/artifact_info.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import 'helpers.dart';

/// Artifact list panel for the sidebar Artifacts tab.
///
/// Shows all discovered artifacts with a search bar. Clicking an artifact opens
/// it in the active pane as an ArtifactPane.
class ArtifactListPanel extends StatefulWidget {
  final VoidCallback? onArtifactSelected;

  const ArtifactListPanel({super.key, this.onArtifactSelected});

  @override
  State<ArtifactListPanel> createState() => _ArtifactListPanelState();
}

class _ArtifactListPanelState extends State<ArtifactListPanel> {
  final TextEditingController _searchController = TextEditingController();
  String _searchQuery = '';

  @override
  void initState() {
    super.initState();
    final settings =
        Provider.of<AppState>(context, listen: false).settings;
    _searchQuery = settings.artifactsFilterSearch;
    _searchController.text = _searchQuery;
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  void _saveFilters() {
    final settings =
        Provider.of<AppState>(context, listen: false).settings;
    settings.artifactsFilterSearch = _searchQuery;
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
                Icon(Icons.article_outlined,
                    color: context.appColors.textMuted, size: 40),
                const SizedBox(height: 12),
                Text('No artifacts yet',
                    style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 16,
                        fontWeight: FontWeight.w600)),
                const SizedBox(height: 4),
                Text('Artifacts are extracted from\nsession messages',
                    textAlign: TextAlign.center,
                    style: TextStyle(
                        color: context.appColors.textMuted, fontSize: 13)),
              ],
            ),
          );
        }

        final filtered = _filterArtifacts(artifacts);

        return Column(
          children: [
            _buildFilterBar(context),
            Expanded(
              child: filtered.isEmpty && _searchQuery.isNotEmpty
                  ? _buildNoResults(context)
                  : ListView.builder(
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      itemCount: filtered.length,
                      itemBuilder: (context, index) {
                        final artifact = filtered[index];
                        return _ArtifactTile(
                          artifact: artifact,
                          state: state,
                          onArtifactSelected: widget.onArtifactSelected,
                        );
                      },
                    ),
            ),
          ],
        );
      },
    );
  }

  Widget _buildFilterBar(BuildContext context) {
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
                    child: Icon(Icons.search_rounded,
                        color: context.appColors.textMuted, size: 16),
                  ),
                  prefixIconConstraints:
                      const BoxConstraints(maxWidth: 28, maxHeight: 30),
                  suffixIcon: _searchQuery.isNotEmpty
                      ? GestureDetector(
                          onTap: () {
                            _searchController.clear();
                            setState(() => _searchQuery = '');
                            _saveFilters();
                          },
                          child: Padding(
                            padding: const EdgeInsets.only(right: 6),
                            child: Icon(Icons.close_rounded,
                                color: context.appColors.textMuted, size: 14),
                          ),
                        )
                      : null,
                  suffixIconConstraints:
                      const BoxConstraints(maxWidth: 24, maxHeight: 30),
                  filled: true,
                  fillColor: context.appColors.bgElevated,
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 0),
                  border: OutlineInputBorder(
                    borderSide: BorderSide.none,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  enabledBorder: OutlineInputBorder(
                    borderSide: BorderSide.none,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  focusedBorder: OutlineInputBorder(
                    borderSide:
                        BorderSide(color: context.appColors.accent, width: 1),
                    borderRadius: BorderRadius.circular(8),
                  ),
                ),
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
          Icon(Icons.search_off_rounded,
              color: context.appColors.textMuted, size: 32),
          const SizedBox(height: 8),
          Text('No matching artifacts',
              style: TextStyle(
                  color: context.appColors.textSecondary, fontSize: 13)),
          const SizedBox(height: 4),
          GestureDetector(
            onTap: () {
              _searchController.clear();
              setState(() => _searchQuery = '');
              _saveFilters();
            },
            child: Text('Clear search',
                style: TextStyle(
                    color: context.appColors.accent, fontSize: 12)),
          ),
        ],
      ),
    );
  }

}

class _ArtifactTile extends StatelessWidget {
  final ArtifactInfo artifact;
  final AppState state;
  final VoidCallback? onArtifactSelected;

  const _ArtifactTile({
    required this.artifact,
    required this.state,
    this.onArtifactSelected,
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
    final icon = _extIcons[artifact.fileExtension.toLowerCase()] ??
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
                left:
                    BorderSide(color: context.appColors.accent, width: 3))
            : isViewed
                ? Border(
                    left: BorderSide(
                        color: context.appColors.accent.withAlpha(80),
                        width: 2))
                : null,
      ),
      child: ListTile(
        leading: Container(
          width: 30,
          height: 30,
          decoration: BoxDecoration(
            color: context.appColors.accent.withAlpha(30),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Icon(icon, color: context.appColors.accentLight, size: 16),
        ),
        title: Text(
          artifact.fileName,
          style: TextStyle(
            color: isActive
                ? context.appColors.accentLight
                : context.appColors.textPrimary,
            fontSize: 12,
            fontWeight: isActive ? FontWeight.w600 : FontWeight.w400,
          ),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        subtitle: Text(
          _subtitle(),
          style:
              TextStyle(color: context.appColors.textMuted, fontSize: 10),
        ),
        trailing: Text(
          artifact.displaySize,
          style: TextStyle(
            color: context.appColors.textMuted,
            fontSize: 10,
          ),
        ),
        dense: true,
        visualDensity: const VisualDensity(vertical: -4),
        contentPadding: const EdgeInsets.only(left: 16, right: 8),
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
