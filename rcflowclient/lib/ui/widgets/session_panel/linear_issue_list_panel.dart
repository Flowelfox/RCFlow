import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/app_notification.dart';
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
  // --- Issue list state ---
  final Set<String> _collapsedGroups = {'completed', 'cancelled'};
  final TextEditingController _searchController = TextEditingController();
  String _searchQuery = '';
  final Set<String> _activeStateFilters = {};
  final Set<String> _activePriorityFilters = {};
  bool _syncing = false;

  // --- Config / setup state ---
  /// Null = not yet checked; false = not configured; true = configured.
  bool? _linearKeySet;
  /// Worker ID for which the config was last loaded; avoid redundant fetches.
  String? _lastConfigWorkerId;

  // --- Setup flow state ---
  final TextEditingController _setupKeyController = TextEditingController();
  bool _setupKeyObscured = true;
  bool _testing = false;
  List<Map<String, dynamic>> _testedTeams = [];
  String? _selectedTeamId; // null = all teams
  bool _saving = false;
  String? _setupError;

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
    _setupKeyController.dispose();
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
        // Trigger config check when a new connected worker appears.
        final workerId = state.defaultWorkerId;
        if (workerId != null &&
            workerId != _lastConfigWorkerId &&
            (state.getWorker(workerId)?.isConnected ?? false)) {
          Future.microtask(() => _loadLinearConfig(state));
        }

        // Still checking config.
        if (_linearKeySet == null) {
          return Center(
            child: SizedBox(
              width: 20,
              height: 20,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: context.appColors.textMuted,
              ),
            ),
          );
        }

        // API key not set — show the setup flow.
        if (_linearKeySet == false) {
          return _buildSetupFlow(context, state);
        }

        // API key is configured — show normal issue list.
        final issues = state.linearIssues;

        if (issues.isEmpty) {
          return _buildEmptyState(context, state);
        }

        final filtered = _filterIssues(issues);

        // Group by state type.
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

  // ---------------------------------------------------------------------------
  // Config loading
  // ---------------------------------------------------------------------------

  Future<void> _loadLinearConfig(AppState state) async {
    final workerId = state.defaultWorkerId;
    if (workerId == null) return;
    final worker = state.getWorker(workerId);
    if (worker == null || !worker.isConnected) return;

    // Mark this worker as the one we're loading for, even before the async
    // call completes, so we don't kick off a second fetch on the next rebuild.
    _lastConfigWorkerId = workerId;

    try {
      final config = await worker.ws.fetchConfig();
      final apiKeyEntry = config.firstWhere(
        (o) => o['key'] == 'LINEAR_API_KEY',
        orElse: () => <String, dynamic>{},
      );
      final apiKeyValue = apiKeyEntry['value'] as String? ?? '';
      if (mounted) {
        setState(() {
          _linearKeySet = apiKeyValue.isNotEmpty;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _linearKeySet = false;
        });
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Setup flow
  // ---------------------------------------------------------------------------

  Widget _buildSetupFlow(BuildContext context, AppState state) {
    // If we have tested teams, show team selection + save step.
    final showTeamStep = _testedTeams.isNotEmpty;

    return SingleChildScrollView(
      padding: const EdgeInsets.all(20),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Row(
            children: [
              Icon(Icons.extension_outlined,
                  color: context.appColors.accent, size: 20),
              const SizedBox(width: 8),
              Text(
                'Connect Linear',
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 15,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),

          // Step 1: API key entry.
          Text(
            'API Key',
            style: TextStyle(
              color: context.appColors.textSecondary,
              fontSize: 12,
              fontWeight: FontWeight.w500,
            ),
          ),
          const SizedBox(height: 6),
          TextField(
            controller: _setupKeyController,
            obscureText: _setupKeyObscured,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 13,
              fontFamily: 'monospace',
            ),
            decoration: InputDecoration(
              hintText: 'lin_api_...',
              hintStyle: TextStyle(
                  color: context.appColors.textMuted, fontSize: 13),
              filled: true,
              fillColor: context.appColors.bgElevated,
              contentPadding:
                  const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
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
              suffixIcon: IconButton(
                icon: Icon(
                  _setupKeyObscured
                      ? Icons.visibility_off_outlined
                      : Icons.visibility_outlined,
                  color: context.appColors.textMuted,
                  size: 16,
                ),
                onPressed: () =>
                    setState(() => _setupKeyObscured = !_setupKeyObscured),
              ),
            ),
          ),
          const SizedBox(height: 6),
          Text(
            'Create a personal API token at linear.app → Settings → API.',
            style: TextStyle(
                color: context.appColors.textMuted, fontSize: 11),
          ),

          if (_setupError != null) ...[
            const SizedBox(height: 10),
            Container(
              padding:
                  const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
              decoration: BoxDecoration(
                color: Colors.red.withAlpha(25),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: Colors.red.withAlpha(80)),
              ),
              child: Text(
                _setupError!,
                style: const TextStyle(color: Colors.red, fontSize: 12),
              ),
            ),
          ],

          const SizedBox(height: 12),

          // "Test Connection" button.
          if (!showTeamStep)
            FilledButton(
              onPressed: _testing ? null : () => _testConnection(context, state),
              style: FilledButton.styleFrom(
                backgroundColor: context.appColors.accent,
                foregroundColor: Colors.white,
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(8)),
                padding: const EdgeInsets.symmetric(vertical: 10),
              ),
              child: _testing
                  ? const SizedBox(
                      width: 16,
                      height: 16,
                      child: CircularProgressIndicator(
                          strokeWidth: 2, color: Colors.white))
                  : const Text('Test Connection', style: TextStyle(fontSize: 13)),
            ),

          // Step 2: Team selection (shown after successful test).
          if (showTeamStep) ...[
            const SizedBox(height: 20),
            Row(
              children: [
                Icon(Icons.check_circle_outline,
                    color: Colors.green, size: 16),
                const SizedBox(width: 6),
                Text(
                  'Connection successful',
                  style: TextStyle(
                    color: Colors.green,
                    fontSize: 12,
                    fontWeight: FontWeight.w500,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 14),
            Text(
              'Team (optional)',
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 12,
                fontWeight: FontWeight.w500,
              ),
            ),
            const SizedBox(height: 6),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              decoration: BoxDecoration(
                color: context.appColors.bgElevated,
                borderRadius: BorderRadius.circular(8),
              ),
              child: DropdownButton<String?>(
                value: _selectedTeamId,
                isExpanded: true,
                underline: const SizedBox.shrink(),
                dropdownColor: context.appColors.bgElevated,
                style: TextStyle(
                    color: context.appColors.textPrimary, fontSize: 13),
                items: [
                  DropdownMenuItem<String?>(
                    value: null,
                    child: Text(
                      'All teams',
                      style: TextStyle(
                          color: context.appColors.textSecondary,
                          fontSize: 13),
                    ),
                  ),
                  ..._testedTeams.map(
                    (t) => DropdownMenuItem<String?>(
                      value: t['id'] as String,
                      child: Text(t['name'] as String? ?? t['id'] as String),
                    ),
                  ),
                ],
                onChanged: (v) => setState(() => _selectedTeamId = v),
              ),
            ),
            const SizedBox(height: 6),
            Text(
              'Leave on "All teams" to sync issues from every accessible team.',
              style: TextStyle(
                  color: context.appColors.textMuted, fontSize: 11),
            ),
            const SizedBox(height: 14),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton(
                    onPressed: _saving
                        ? null
                        : () => setState(() {
                              _testedTeams = [];
                              _setupError = null;
                            }),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: context.appColors.textSecondary,
                      side: BorderSide(
                          color: context.appColors.divider),
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(8)),
                      padding: const EdgeInsets.symmetric(vertical: 10),
                    ),
                    child: const Text('Back', style: TextStyle(fontSize: 13)),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: FilledButton(
                    onPressed: _saving
                        ? null
                        : () => _saveConfig(context, state),
                    style: FilledButton.styleFrom(
                      backgroundColor: context.appColors.accent,
                      foregroundColor: Colors.white,
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(8)),
                      padding: const EdgeInsets.symmetric(vertical: 10),
                    ),
                    child: _saving
                        ? const SizedBox(
                            width: 16,
                            height: 16,
                            child: CircularProgressIndicator(
                                strokeWidth: 2, color: Colors.white))
                        : const Text('Save', style: TextStyle(fontSize: 13)),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Future<void> _testConnection(BuildContext context, AppState state) async {
    final apiKey = _setupKeyController.text.trim();
    if (apiKey.isEmpty) {
      setState(() => _setupError = 'Enter your Linear API key first.');
      return;
    }

    final worker = state.getWorker(state.defaultWorkerId ?? '');
    if (worker == null || !worker.isConnected) {
      setState(() => _setupError = 'Not connected to a worker.');
      return;
    }

    setState(() {
      _testing = true;
      _setupError = null;
    });

    try {
      final result = await worker.ws.testLinearConnection(apiKey);
      final teams = (result['teams'] as List<dynamic>? ?? [])
          .cast<Map<String, dynamic>>();
      if (mounted) {
        setState(() {
          _testedTeams = teams;
          _selectedTeamId = null; // default: all teams
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _setupError = _extractDetail(e);
        });
      }
    } finally {
      if (mounted) setState(() => _testing = false);
    }
  }

  Future<void> _saveConfig(BuildContext context, AppState state) async {
    final worker = state.getWorker(state.defaultWorkerId ?? '');
    if (worker == null || !worker.isConnected) return;

    setState(() => _saving = true);
    try {
      await worker.ws.updateConfig({
        'LINEAR_API_KEY': _setupKeyController.text.trim(),
        'LINEAR_TEAM_ID': _selectedTeamId ?? '',
      });
      if (mounted) {
        // Reset setup state and reload config.
        setState(() {
          _testedTeams = [];
          _setupError = null;
          _linearKeySet = null; // triggers reload spinner
          _lastConfigWorkerId = null;
        });
        _setupKeyController.clear();
        await _loadLinearConfig(state);
        // Kick off initial sync after config is saved.
        final freshWorker = state.getWorker(state.defaultWorkerId ?? '');
        freshWorker?.ws.listLinearIssues();
      }
    } catch (e) {
      if (mounted) {
        setState(() => _setupError = _extractDetail(e));
      }
    } finally {
      if (mounted) setState(() => _saving = false);
    }
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
        state.notificationService.show(
          level: NotificationLevel.error,
          title: 'Linear sync failed',
          body: _extractDetail(e),
          duration: const Duration(seconds: 8),
        );
      }
    } finally {
      if (mounted) setState(() => _syncing = false);
    }
  }

  /// Extracts the `detail` field from a JSON error body if present,
  /// otherwise returns the raw exception message.
  static String _extractDetail(Object e) {
    final raw = e.toString();
    final jsonStart = raw.indexOf('{');
    if (jsonStart >= 0) {
      try {
        final decoded = jsonDecode(raw.substring(jsonStart)) as Map<String, dynamic>;
        final detail = decoded['detail'] as String?;
        if (detail != null) return detail;
      } catch (_) {}
    }
    return raw;
  }
}
