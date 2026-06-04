import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/github_pr_info.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import '../../../theme/spacing.dart';
import 'pr_tile.dart';

/// Sidebar panel for the Pull Requests tab — shows cached GitHub PRs split
/// across a "For me" / "Created" tab control.
class PrListPanel extends StatefulWidget {
  final VoidCallback? onPrSelected;

  const PrListPanel({super.key, this.onPrSelected});

  @override
  State<PrListPanel> createState() => _PrListPanelState();
}

class _PrListPanelState extends State<PrListPanel>
    with SingleTickerProviderStateMixin {
  late final TabController _roleTabController;
  final TextEditingController _searchController = TextEditingController();
  String _searchQuery = '';

  /// Repo slugs ("owner/name") the user has un-checked. A repo is considered
  /// selected unless it appears here, so newly-appearing repos default to
  /// visible. Reset implicitly when a repo disappears (see [build]).
  final Set<String> _hiddenRepos = {};

  /// True while a sync request is in flight (disables the refresh button).
  bool _syncing = false;

  @override
  void initState() {
    super.initState();
    _roleTabController = TabController(length: 2, vsync: this);
  }

  @override
  void dispose() {
    _roleTabController.dispose();
    _searchController.dispose();
    super.dispose();
  }

  /// Resolve the connected workers to sync. The PR tab spans all workers, so
  /// every connected worker is synced; the backend returns `synced: 0` for any
  /// worker without a GitHub token.
  Future<void> _syncPrs(AppState state) async {
    if (_syncing) return;
    final messenger = ScaffoldMessenger.of(context);
    final workers = state.workerConfigs
        .map((c) => state.getWorker(c.id))
        .where((w) => w != null && w.isConnected)
        .toList();
    if (workers.isEmpty) {
      messenger.showSnackBar(
        const SnackBar(content: Text('No connected workers to sync')),
      );
      return;
    }
    setState(() => _syncing = true);
    try {
      var total = 0;
      for (final w in workers) {
        final result = await w!.ws.syncGithubPrs();
        total += (result['synced'] as int?) ?? 0;
        // Refresh the cached list explicitly in case any broadcast was missed.
        w.ws.listGithubPrs();
      }
      messenger.showSnackBar(
        SnackBar(
          content: Text('Synced $total pull request${total == 1 ? '' : 's'}'),
        ),
      );
    } catch (e) {
      messenger.showSnackBar(SnackBar(content: Text('Sync failed: $e')));
    } finally {
      if (mounted) setState(() => _syncing = false);
    }
  }

  /// Distinct repo slugs present in the store, sorted alphabetically.
  List<String> _repoOptions(AppState state) {
    final slugs = <String>{
      for (final p in state.githubPrs)
        if (p.repoSlug.isNotEmpty && p.repoSlug != '/') p.repoSlug,
    };
    final list = slugs.toList()..sort();
    return list;
  }

  List<GithubPrInfo> _filterPrs(List<GithubPrInfo> prs) {
    var result = prs;
    if (_hiddenRepos.isNotEmpty) {
      result = result.where((p) => !_hiddenRepos.contains(p.repoSlug)).toList();
    }
    if (_searchQuery.isEmpty) return result;
    final q = _searchQuery.toLowerCase();
    return result
        .where(
          (p) =>
              p.title.toLowerCase().contains(q) ||
              p.repoSlug.toLowerCase().contains(q) ||
              p.number.toString().contains(q) ||
              p.author.toLowerCase().contains(q),
        )
        .toList();
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<AppState>(
      builder: (context, state, _) {
        final repoOptions = _repoOptions(state);
        // Drop hidden entries for repos that are no longer present so a repo
        // re-appearing later defaults back to visible.
        _hiddenRepos.removeWhere((slug) => !repoOptions.contains(slug));
        return Column(
          children: [
            _buildSearchBar(context, state),
            if (repoOptions.isNotEmpty) _buildRepoFilter(context, repoOptions),
            SizedBox(
              height: 32,
              child: TabBar(
                controller: _roleTabController,
                labelColor: context.appColors.textPrimary,
                unselectedLabelColor: context.appColors.textMuted,
                labelStyle: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
                unselectedLabelStyle: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w500,
                ),
                indicatorColor: context.appColors.accent,
                indicatorSize: TabBarIndicatorSize.label,
                indicatorWeight: 2,
                dividerHeight: 0,
                tabs: const [
                  Tab(text: 'For me'),
                  Tab(text: 'Created'),
                ],
              ),
            ),
            const Divider(height: 1),
            Expanded(
              child: TabBarView(
                controller: _roleTabController,
                children: [
                  _buildRoleList(context, state, 'for_me'),
                  _buildRoleList(context, state, 'created'),
                ],
              ),
            ),
          ],
        );
      },
    );
  }

  Widget _buildSearchBar(BuildContext context, AppState state) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 8, 10, 4),
      child: SizedBox(
        height: 30,
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: _searchController,
                onChanged: (v) => setState(() => _searchQuery = v),
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 12,
                ),
                decoration: InputDecoration(
                  hintText: 'Search pull requests...',
                  hintStyle: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 12,
                  ),
                  prefixIcon: Padding(
                    padding: const EdgeInsets.only(
                      left: kSpace2,
                      right: kSpace1,
                    ),
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
                    horizontal: kSpace2,
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
              child: _syncing
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
                        Icons.refresh,
                        color: context.appColors.textSecondary,
                        size: 16,
                      ),
                      tooltip: 'Sync pull requests from GitHub',
                      onPressed: () => _syncPrs(state),
                    ),
            ),
          ],
        ),
      ),
    );
  }

  /// Compact multi-select repo filter: a header button showing the
  /// selected/total count that opens a checkbox popover (with an "All" toggle).
  Widget _buildRepoFilter(BuildContext context, List<String> repoOptions) {
    final colors = context.appColors;
    final selectedCount = repoOptions.length - _hiddenRepos.length;
    final allSelected = _hiddenRepos.isEmpty;

    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 0, 10, 4),
      child: SizedBox(
        height: 26,
        child: Align(
          alignment: Alignment.centerLeft,
          child: PopupMenuButton<void>(
            tooltip: 'Filter by repository',
            color: colors.bgSurface,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(kRadiusMedium),
              side: BorderSide(color: colors.divider, width: 0.5),
            ),
            position: PopupMenuPosition.under,
            padding: EdgeInsets.zero,
            itemBuilder: (menuContext) => [
              PopupMenuItem<void>(
                enabled: false,
                padding: EdgeInsets.zero,
                child: StatefulBuilder(
                  builder: (ctx, setMenuState) {
                    void toggleAll(bool? value) {
                      setState(() {
                        if (value == true) {
                          _hiddenRepos.clear();
                        } else {
                          _hiddenRepos.addAll(repoOptions);
                        }
                      });
                      setMenuState(() {});
                    }

                    void toggleRepo(String slug, bool? value) {
                      setState(() {
                        if (value == true) {
                          _hiddenRepos.remove(slug);
                        } else {
                          _hiddenRepos.add(slug);
                        }
                      });
                      setMenuState(() {});
                    }

                    Widget row({
                      required bool checked,
                      required String label,
                      required ValueChanged<bool?> onChanged,
                      bool emphasised = false,
                    }) {
                      return InkWell(
                        onTap: () => onChanged(!checked),
                        child: Padding(
                          padding: const EdgeInsets.symmetric(
                            horizontal: kSpace2,
                            vertical: 2,
                          ),
                          child: Row(
                            children: [
                              SizedBox(
                                width: 22,
                                height: 22,
                                child: Checkbox(
                                  value: checked,
                                  onChanged: onChanged,
                                  visualDensity: VisualDensity.compact,
                                  materialTapTargetSize:
                                      MaterialTapTargetSize.shrinkWrap,
                                  activeColor: colors.accent,
                                ),
                              ),
                              const SizedBox(width: kGapTight),
                              Expanded(
                                child: Text(
                                  label,
                                  style: TextStyle(
                                    color: colors.textPrimary,
                                    fontSize: 12,
                                    fontWeight: emphasised
                                        ? FontWeight.w600
                                        : FontWeight.w400,
                                  ),
                                  maxLines: 1,
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                            ],
                          ),
                        ),
                      );
                    }

                    return ConstrainedBox(
                      constraints: const BoxConstraints(
                        minWidth: 200,
                        maxWidth: 280,
                      ),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Padding(
                            padding: const EdgeInsets.fromLTRB(
                              kSpace3,
                              kSpace2,
                              kSpace3,
                              kSpace1,
                            ),
                            child: Text(
                              'Repositories',
                              style: TextStyle(
                                color: colors.textMuted,
                                fontSize: 10,
                                fontWeight: FontWeight.w600,
                                letterSpacing: 0.5,
                              ),
                            ),
                          ),
                          row(
                            checked: allSelected,
                            label: 'All',
                            onChanged: toggleAll,
                            emphasised: true,
                          ),
                          Divider(height: 1, color: colors.divider),
                          for (final slug in repoOptions)
                            row(
                              checked: !_hiddenRepos.contains(slug),
                              label: slug,
                              onChanged: (v) => toggleRepo(slug, v),
                            ),
                          const SizedBox(height: kSpace1),
                        ],
                      ),
                    );
                  },
                ),
              ),
            ],
            child: Container(
              padding: const EdgeInsets.symmetric(
                horizontal: kSpace3,
                vertical: 4,
              ),
              decoration: BoxDecoration(
                color: allSelected
                    ? colors.bgElevated
                    : colors.accent.withAlpha(30),
                borderRadius: BorderRadius.circular(kRadiusLarge),
                border: allSelected
                    ? null
                    : Border.all(color: colors.accent, width: 1),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    Icons.folder_outlined,
                    size: 13,
                    color: allSelected
                        ? colors.textSecondary
                        : colors.accentLight,
                  ),
                  const SizedBox(width: kGapInline),
                  Text(
                    'Repositories ($selectedCount/${repoOptions.length})',
                    style: TextStyle(
                      color: allSelected
                          ? colors.textSecondary
                          : colors.accentLight,
                      fontSize: 11,
                      fontWeight: allSelected
                          ? FontWeight.w500
                          : FontWeight.w600,
                    ),
                  ),
                  const SizedBox(width: kGapInline),
                  Icon(
                    Icons.arrow_drop_down,
                    size: 16,
                    color: allSelected ? colors.textMuted : colors.accentLight,
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildRoleList(BuildContext context, AppState state, String role) {
    final prs = _filterPrs(
      state.githubPrs.where((p) => p.role == role).toList(),
    );

    if (prs.isEmpty) {
      return _buildEmptyState(context, role);
    }

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: kSpace1),
      children: [
        for (final pr in prs)
          PrTile(pr: pr, state: state, onSelected: widget.onPrSelected),
      ],
    );
  }

  Widget _buildEmptyState(BuildContext context, String role) {
    final hasSearch = _searchQuery.isNotEmpty;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(kSpace5),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              hasSearch ? Icons.search_off : Icons.merge_type,
              color: context.appColors.textMuted,
              size: 36,
            ),
            const SizedBox(height: kGapRelaxed),
            Text(
              hasSearch
                  ? 'No pull requests match your search'
                  : role == 'for_me'
                  ? 'No pull requests for you to review'
                  : 'No pull requests you created',
              textAlign: TextAlign.center,
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 13,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
