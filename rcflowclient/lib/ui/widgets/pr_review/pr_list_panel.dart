import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/app_notification.dart';
import '../../../models/deduped_pr.dart';
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

  /// Selected PR states to show (any combination). Defaults to open only; an
  /// empty set means "no state filter" (show all).
  final Set<String> _states = {'open'};

  @override
  void initState() {
    super.initState();
    _roleTabController = TabController(length: 3, vsync: this);
    _roleTabController.addListener(_onTabChanged);
    // Restore the persisted repo-filter selection (un-checked repos).
    _hiddenRepos.addAll(context.read<AppState>().settings.prHiddenRepos);
  }

  /// Persist the repo-filter selection so it survives a client restart.
  void _persistHiddenRepos() {
    context.read<AppState>().settings.prHiddenRepos = _hiddenRepos.toList();
  }

  /// Index of the "All" tab.
  static const _allTabIndex = 2;
  bool get _isAllTab => _roleTabController.index == _allTabIndex;

  /// When the user lands on the "All" tab, fetch every PR (any author) in the
  /// repos already cached for each worker — the cheap For me / Owned syncs don't
  /// cover PRs the current user isn't attached to.
  void _onTabChanged() {
    if (_roleTabController.indexIsChanging) return;
    setState(() {}); // refresh refresh-button target / chip behaviour
    if (_isAllTab && mounted) {
      _syncAll(context.read<AppState>());
    }
  }

  /// Sync the "all" bucket (every PR, any author) for the active states across
  /// all connected workers. Best-effort, quiet.
  Future<void> _syncAll(AppState state) async {
    final workers = state.workerConfigs
        .map((c) => state.getWorker(c.id))
        .where((w) => w != null && w.isConnected);
    final states = _states.isEmpty
        ? const ['open', 'merged', 'closed']
        : _states.toList();
    for (final w in workers) {
      for (final st in states) {
        try {
          await w!.ws.syncGithubPrs(role: 'all', state: st, force: true);
        } catch (_) {}
      }
      try {
        w!.ws.listGithubPrs();
      } catch (_) {}
    }
  }

  @override
  void dispose() {
    _roleTabController.removeListener(_onTabChanged);
    _roleTabController.dispose();
    _searchController.dispose();
    super.dispose();
  }

  /// Resolve the connected workers to sync. The PR tab spans all workers, so
  /// every connected worker is synced; the backend returns `synced: 0` for any
  /// worker without a GitHub token.
  Future<void> _syncPrs(AppState state) async {
    if (_syncing) return;
    final workers = state.workerConfigs
        .map((c) => state.getWorker(c.id))
        .where((w) => w != null && w.isConnected)
        .toList();
    if (workers.isEmpty) {
      state.showNotification(
        level: NotificationLevel.warning,
        title: 'No connected workers to sync',
      );
      return;
    }
    // Only fetch the states the user is actually viewing (empty = all). Keeps
    // refresh cheap — open is fully enriched, merged/closed are listed lightly
    // and only when their filter chip is on.
    final states = _states.isEmpty
        ? const ['open', 'merged', 'closed']
        : _states.toList();

    setState(() => _syncing = true);
    try {
      var total = 0;
      var failed = 0;
      // Sync each worker independently — one worker erroring (e.g. an older
      // backend, or one without a GitHub token) must not abort the whole sweep.
      for (final w in workers) {
        var workerFailed = false;
        for (final st in states) {
          try {
            // Manual refresh → force, bypassing the 60s auto-sync throttle.
            // On the All tab, refresh the all-bucket (every author) too.
            final result = await w!.ws.syncGithubPrs(
              role: _isAllTab ? 'all' : null,
              state: st,
              force: true,
            );
            total += (result['synced'] as int?) ?? 0;
          } catch (_) {
            workerFailed = true;
          }
        }
        // Refresh the cached list once per worker (broadcast may be missed).
        try {
          w!.ws.listGithubPrs();
        } catch (_) {}
        if (workerFailed) failed++;
      }
      if (failed == workers.length) {
        state.showNotification(
          level: NotificationLevel.error,
          title: 'Sync failed',
          body: 'Could not sync any connected worker.',
        );
      } else {
        state.showNotification(
          level: failed > 0 ? NotificationLevel.warning : NotificationLevel.success,
          title: 'Synced $total pull request${total == 1 ? '' : 's'}',
          body: failed > 0 ? '$failed worker${failed == 1 ? '' : 's'} could not be synced.' : null,
        );
      }
    } finally {
      if (mounted) setState(() => _syncing = false);
    }
  }

  /// Fetch a single PR state across all connected workers (on-demand when a
  /// filter chip is enabled). Best-effort, quiet.
  Future<void> _syncState(AppState state, String prState) async {
    final workers = state.workerConfigs
        .map((c) => state.getWorker(c.id))
        .where((w) => w != null && w.isConnected);
    for (final w in workers) {
      try {
        await w!.ws.syncGithubPrs(
          role: _isAllTab ? 'all' : null,
          state: prState,
          force: true,
        );
        w.ws.listGithubPrs();
      } catch (_) {}
    }
  }

  /// Distinct repo slugs present in the store, sorted alphabetically.
  List<String> _repoOptions(AppState state) {
    final slugs = <String>{
      for (final d in state.dedupedGithubPrs)
        if (d.canonical.repoSlug.isNotEmpty && d.canonical.repoSlug != '/')
          d.canonical.repoSlug,
    };
    final list = slugs.toList()..sort();
    return list;
  }

  List<DedupedPr> _filterPrs(List<DedupedPr> prs) {
    var result = prs;
    // State filter (open/merged/closed); empty selection = show all.
    if (_states.isNotEmpty) {
      result = result
          .where((d) => _states.contains(d.canonical.state))
          .toList();
    }
    if (_hiddenRepos.isNotEmpty) {
      result = result
          .where((d) => !_hiddenRepos.contains(d.canonical.repoSlug))
          .toList();
    }
    if (_searchQuery.isEmpty) return result;
    final q = _searchQuery.toLowerCase();
    return result.where((d) {
      final p = d.canonical;
      return p.title.toLowerCase().contains(q) ||
          p.repoSlug.toLowerCase().contains(q) ||
          p.number.toString().contains(q) ||
          p.author.toLowerCase().contains(q);
    }).toList();
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<AppState>(
      builder: (context, state, _) {
        final repoOptions = _repoOptions(state);
        // Hidden-repo selections are persisted across restarts, so we keep them
        // even when a repo is temporarily absent (e.g. PRs not yet synced) —
        // the choice is restored when the repo reappears.
        return Column(
          children: [
            _buildSearchBar(context, state),
            _buildStateFilter(context),
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
                  Tab(text: 'Owned'),
                  Tab(text: 'All'),
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
                  _buildRoleList(context, state, 'all'),
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
                      ? MouseRegion(
                          cursor: SystemMouseCursors.click,
                          child: GestureDetector(
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

  /// Multi-select state filter chips (any combination of open / merged /
  /// closed). Tapping toggles a state; an empty selection shows all.
  Widget _buildStateFilter(BuildContext context) {
    final colors = context.appColors;
    Widget chip(String value, String label, Color color) {
      final on = _states.contains(value);
      return Padding(
        padding: const EdgeInsets.only(right: 6),
        child: MouseRegion(
          cursor: SystemMouseCursors.click,
          child: GestureDetector(
            onTap: () {
              final added = !_states.remove(value);
              if (added) _states.add(value);
              setState(() {});
              // Enabling a state fetches it on demand (it may not be cached yet).
              if (added) _syncState(context.read<AppState>(), value);
            },
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              decoration: BoxDecoration(
                color: on ? color.withAlpha(30) : colors.bgElevated,
                borderRadius: BorderRadius.circular(12),
                border: Border.all(
                  color: on ? color.withAlpha(120) : colors.divider,
                  width: 0.5,
                ),
              ),
              child: Text(
                label,
                style: TextStyle(
                  color: on ? color : colors.textMuted,
                  fontSize: 11,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
          ),
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 0, 10, 4),
      child: Align(
        alignment: Alignment.centerLeft,
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            chip('open', 'Open', const Color(0xFF10B981)),
            chip('merged', 'Merged', const Color(0xFF8B5CF6)),
            chip('closed', 'Closed', const Color(0xFFEF4444)),
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
                    // Recompute live inside the builder so the "All" checkbox
                    // and per-repo rows reflect the current selection after a
                    // toggle (the outer [allSelected] is captured stale).
                    final allReposSelected = _hiddenRepos.isEmpty;

                    void toggleAll(bool? value) {
                      setState(() {
                        if (value == true) {
                          _hiddenRepos.clear();
                        } else {
                          _hiddenRepos.addAll(repoOptions);
                        }
                      });
                      _persistHiddenRepos();
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
                      _persistHiddenRepos();
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
                            checked: allReposSelected,
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
    // The "All" tab is not filtered by the current user — show every cached PR.
    final source = role == 'all'
        ? state.dedupedGithubPrs.toList()
        : state.dedupedGithubPrs
              .where((d) => d.sources.any((s) => s.role == role))
              .toList();
    final prs = _filterPrs(source);

    if (prs.isEmpty) {
      return _buildEmptyState(context, role);
    }

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: kSpace1),
      children: [
        for (final d in prs)
          PrTile(pr: d, state: state, onSelected: widget.onPrSelected),
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
                  : role == 'created'
                  ? 'No pull requests you created'
                  : 'No pull requests in your repos yet — refresh to load them',
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
