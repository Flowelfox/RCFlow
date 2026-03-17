import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/linear_issue_info.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import 'linear_issue_tile.dart';

/// Sidebar panel for the Integrations tab — shows cached Linear issues.
class LinearIssueListPanel extends StatefulWidget {
  final VoidCallback? onIssueSelected;

  const LinearIssueListPanel({super.key, this.onIssueSelected});

  @override
  State<LinearIssueListPanel> createState() => _LinearIssueListPanelState();
}

class _LinearIssueListPanelState extends State<LinearIssueListPanel> {
  final Set<String> _collapsedGroups = {'completed', 'cancelled'};
  final TextEditingController _searchController = TextEditingController();
  String _searchQuery = '';
  final Set<String> _activeStateFilters = {};
  final Set<String> _activePriorityFilters = {};
  bool _syncing = false;

  /// Display order for state types.
  static const _stateOrder = [
    'started',
    'unstarted',
    'triage',
    'backlog',
    'completed',
    'cancelled',
  ];

  static const _stateLabels = {
    'triage': 'Triage',
    'backlog': 'Backlog',
    'unstarted': 'Todo',
    'started': 'In Progress',
    'completed': 'Done',
    'cancelled': 'Cancelled',
  };

  static const _stateColors = {
    'triage': Color(0xFF8B5CF6),
    'backlog': Color(0xFF6B7280),
    'unstarted': Color(0xFF6B7280),
    'started': Color(0xFF3B82F6),
    'completed': Color(0xFF10B981),
    'cancelled': Color(0xFF9CA3AF),
  };

  static const _priorityOrder = [1, 2, 3, 4, 0];
  static const _priorityLabels = {
    0: 'No Priority',
    1: 'Urgent',
    2: 'High',
    3: 'Medium',
    4: 'Low',
  };

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  List<LinearIssueInfo> _filterIssues(List<LinearIssueInfo> issues) {
    var filtered = issues;

    if (_activeStateFilters.isNotEmpty) {
      filtered = filtered
          .where((i) => _activeStateFilters.contains(i.stateType))
          .toList();
    }
    if (_activePriorityFilters.isNotEmpty) {
      filtered = filtered
          .where((i) => _activePriorityFilters.contains(i.priority.toString()))
          .toList();
    }
    if (_searchQuery.isNotEmpty) {
      final q = _searchQuery.toLowerCase();
      filtered = filtered
          .where((i) =>
              i.title.toLowerCase().contains(q) ||
              i.identifier.toLowerCase().contains(q) ||
              (i.assigneeName?.toLowerCase().contains(q) ?? false))
          .toList();
    }
    return filtered;
  }

  bool get _hasActiveFilters =>
      _searchQuery.isNotEmpty ||
      _activeStateFilters.isNotEmpty ||
      _activePriorityFilters.isNotEmpty;

  void _clearFilters() {
    setState(() {
      _searchController.clear();
      _searchQuery = '';
      _activeStateFilters.clear();
      _activePriorityFilters.clear();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<AppState>(
      builder: (context, state, _) {
        final issues = state.linearIssues;
        final apiConfigured = _isLinearConfigured(state);

        if (!apiConfigured) {
          return _buildUnconfiguredState(context);
        }

        if (issues.isEmpty) {
          return _buildEmptyState(context, state);
        }

        final filtered = _filterIssues(issues);

        // Group by state type
        final grouped = <String, List<LinearIssueInfo>>{};
        for (final st in _stateOrder) {
          grouped[st] = [];
        }
        for (final i in filtered) {
          grouped.putIfAbsent(i.stateType, () => []).add(i);
        }

        final sections = <Widget>[];
        for (final stateType in _stateOrder) {
          final group = grouped[stateType] ?? [];
          if (group.isEmpty) continue;
          final collapsed = _collapsedGroups.contains(stateType);
          sections.add(_buildStateGroup(
              context, state, stateType, group, collapsed));
        }

        return Column(
          children: [
            _buildFilterBar(context, state),
            Expanded(
              child: filtered.isEmpty && _hasActiveFilters
                  ? _buildNoResults(context)
                  : ListView(
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      children: sections,
                    ),
            ),
          ],
        );
      },
    );
  }

  bool _isLinearConfigured(AppState state) {
    // Check if any connected worker has Linear configured by seeing if we have
    // any issues OR by checking a heuristic (no direct config access in UI layer)
    // We just show the panel; the backend will return empty if not configured.
    return true;
  }

  Widget _buildUnconfiguredState(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.extension_off_outlined,
                color: context.appColors.textMuted, size: 40),
            const SizedBox(height: 12),
            Text('Linear not configured',
                style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 16,
                    fontWeight: FontWeight.w600)),
            const SizedBox(height: 4),
            Text(
              'Add your Linear API key in Settings → Linear to sync issues.',
              textAlign: TextAlign.center,
              style: TextStyle(
                  color: context.appColors.textMuted, fontSize: 13),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildEmptyState(BuildContext context, AppState state) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.inbox_outlined,
                color: context.appColors.textMuted, size: 40),
            const SizedBox(height: 12),
            Text('No issues synced',
                style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 16,
                    fontWeight: FontWeight.w600)),
            const SizedBox(height: 4),
            Text(
              'Sync your Linear issues to get started.',
              textAlign: TextAlign.center,
              style: TextStyle(
                  color: context.appColors.textMuted, fontSize: 13),
            ),
            const SizedBox(height: 16),
            FilledButton.icon(
              onPressed: _syncing ? null : () => _sync(context, state),
              icon: _syncing
                  ? const SizedBox(
                      width: 14,
                      height: 14,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: Colors.white))
                  : const Icon(Icons.sync, size: 18),
              label: const Text('Sync Now'),
              style: FilledButton.styleFrom(
                backgroundColor: context.appColors.accent,
                foregroundColor: Colors.white,
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10)),
                padding: const EdgeInsets.symmetric(
                    horizontal: 16, vertical: 10),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildFilterBar(BuildContext context, AppState state) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 8, 10, 4),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            height: 30,
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _searchController,
                    onChanged: (v) => setState(() => _searchQuery = v),
                    style: TextStyle(
                        color: context.appColors.textPrimary, fontSize: 12),
                    decoration: InputDecoration(
                      hintText: 'Search issues...',
                      hintStyle: TextStyle(
                          color: context.appColors.textMuted, fontSize: 12),
                      prefixIcon: Padding(
                        padding:
                            const EdgeInsets.only(left: 8, right: 4),
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
                              },
                              child: Padding(
                                padding: const EdgeInsets.only(right: 6),
                                child: Icon(Icons.close_rounded,
                                    color: context.appColors.textMuted,
                                    size: 14),
                              ),
                            )
                          : null,
                      suffixIconConstraints:
                          const BoxConstraints(maxWidth: 24, maxHeight: 30),
                      filled: true,
                      fillColor: context.appColors.bgElevated,
                      contentPadding: const EdgeInsets.symmetric(
                          horizontal: 8, vertical: 0),
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
                            color: context.appColors.accent, width: 1),
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
                      ? const Center(
                          child: SizedBox(
                              width: 14,
                              height: 14,
                              child: CircularProgressIndicator(
                                  strokeWidth: 2)))
                      : IconButton(
                          padding: EdgeInsets.zero,
                          icon: Icon(Icons.sync,
                              color: context.appColors.textSecondary,
                              size: 18),
                          tooltip: 'Sync from Linear',
                          onPressed: () => _sync(context, state),
                        ),
                ),
              ],
            ),
          ),
          const SizedBox(height: 6),
          // State filter chips
          SizedBox(
            height: 24,
            child: Row(
              children: [
                Expanded(
                  child: ListView(
                    scrollDirection: Axis.horizontal,
                    children: [
                      for (final stateType in _stateOrder)
                        Padding(
                          padding: const EdgeInsets.only(right: 4),
                          child: _buildFilterChip(
                            context,
                            label: _stateLabels[stateType] ?? stateType,
                            color: _stateColors[stateType] ??
                                context.appColors.textMuted,
                            selected:
                                _activeStateFilters.contains(stateType),
                            onTap: () => setState(() {
                              if (_activeStateFilters.contains(stateType)) {
                                _activeStateFilters.remove(stateType);
                              } else {
                                _activeStateFilters.add(stateType);
                              }
                            }),
                          ),
                        ),
                    ],
                  ),
                ),
                if (_hasActiveFilters)
                  GestureDetector(
                    onTap: _clearFilters,
                    child: Padding(
                      padding: const EdgeInsets.only(left: 4),
                      child: Icon(Icons.filter_alt_off_outlined,
                          color: context.appColors.textMuted, size: 16),
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFilterChip(
    BuildContext context, {
    required String label,
    required Color color,
    required bool selected,
    required VoidCallback onTap,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 150),
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: selected ? color.withAlpha(40) : context.appColors.bgElevated,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: selected ? color.withAlpha(180) : Colors.transparent,
            width: 1,
          ),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: selected ? color : context.appColors.textMuted,
            fontSize: 10,
            fontWeight:
                selected ? FontWeight.w600 : FontWeight.w400,
          ),
        ),
      ),
    );
  }

  Widget _buildStateGroup(
    BuildContext context,
    AppState state,
    String stateType,
    List<LinearIssueInfo> issues,
    bool collapsed,
  ) {
    final color =
        _stateColors[stateType] ?? context.appColors.textMuted;
    final label = _stateLabels[stateType] ?? stateType;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        InkWell(
          onTap: () => setState(() {
            if (collapsed) {
              _collapsedGroups.remove(stateType);
            } else {
              _collapsedGroups.add(stateType);
            }
          }),
          child: Padding(
            padding:
                const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
            child: Row(
              children: [
                Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: color,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  label,
                  style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 11,
                    fontWeight: FontWeight.w600,
                    letterSpacing: 0.5,
                  ),
                ),
                const SizedBox(width: 6),
                Text(
                  '${issues.length}',
                  style: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 10,
                  ),
                ),
                const Spacer(),
                Icon(
                  collapsed
                      ? Icons.chevron_right
                      : Icons.expand_more,
                  color: context.appColors.textMuted,
                  size: 16,
                ),
              ],
            ),
          ),
        ),
        if (!collapsed)
          ...issues.map((issue) => LinearIssueTile(
                issue: issue,
                state: state,
                onSelected: widget.onIssueSelected,
              )),
        const Divider(height: 1),
      ],
    );
  }

  Widget _buildNoResults(BuildContext context) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.search_off,
              color: context.appColors.textMuted, size: 32),
          const SizedBox(height: 8),
          Text('No issues match filters',
              style: TextStyle(
                  color: context.appColors.textMuted, fontSize: 13)),
          const SizedBox(height: 8),
          TextButton(
            onPressed: _clearFilters,
            child: Text('Clear filters',
                style: TextStyle(color: context.appColors.accent)),
          ),
        ],
      ),
    );
  }

  Future<void> _sync(BuildContext context, AppState state) async {
    final worker = state.getWorker(state.defaultWorkerId ?? '');
    if (worker == null) return;
    setState(() => _syncing = true);
    try {
      await worker.ws.syncLinearIssues();
      // The broadcast will update AppState via WS; also refresh via WS pull
      worker.ws.listLinearIssues();
    } catch (e) {
      if (context.mounted) {
        state.addSystemMessage('Linear sync failed: $e', isError: true);
      }
    } finally {
      if (mounted) setState(() => _syncing = false);
    }
  }
}
