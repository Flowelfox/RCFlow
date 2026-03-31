import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/session_info.dart';
import '../../../services/notification_service.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import '../../dialogs/worker_edit_dialog.dart';
import '../../onboarding_keys.dart' as onboarding;
import '../notification_toast.dart';
import '../settings_menu.dart';
import 'artifact_list_panel.dart';
import 'task_list_panel.dart';
import 'worker_group.dart';

/// Reusable session list that works both inline (sidebar) and inside a sheet.
///
/// Sessions are grouped by worker in expandable sections.
class SessionListPanel extends StatefulWidget {
  final VoidCallback? onSessionSelected;

  const SessionListPanel({super.key, this.onSessionSelected});

  @override
  State<SessionListPanel> createState() => _SessionListPanelState();
}

class _SessionListPanelState extends State<SessionListPanel>
    with SingleTickerProviderStateMixin {
  final Set<String> _expandedWorkers = {};
  bool _initialized = false;
  bool _groupByProject = false;
  late final TabController _tabController;
  final TextEditingController _workerSearchController = TextEditingController();
  String _workerSearchQuery = '';
  final Set<String> _activeStatusFilters = {};

  static const _statusOrder = [
    'active',
    'paused',
    'completed',
    'failed',
    'cancelled',
  ];
  static const _statusLabels = {
    'active': 'Active',
    'paused': 'Paused',
    'completed': 'Completed',
    'failed': 'Failed',
    'cancelled': 'Cancelled',
  };
  static const _statusColors = {
    'active': Color(0xFF3B82F6),
    'paused': Color(0xFFF59E0B),
    'completed': Color(0xFF10B981),
    'failed': Color(0xFFEF4444),
    'cancelled': Color(0xFF6B7280),
  };

  @override
  void initState() {
    super.initState();
    _tabController = TabController(length: 3, vsync: this);
    final settings = Provider.of<AppState>(context, listen: false).settings;
    _workerSearchQuery = settings.workersFilterSearch;
    _workerSearchController.text = _workerSearchQuery;
    _activeStatusFilters.addAll(settings.workersFilterStatus);
    _groupByProject = settings.workersGroupByProject;
    final savedExpanded = settings.workersExpanded;
    if (savedExpanded != null) {
      _expandedWorkers.addAll(savedExpanded);
      _initialized = true;
    }
  }

  @override
  void dispose() {
    _workerSearchController.dispose();
    _tabController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        // Tab bar header – 39px + 1px divider = 40px to match CustomTitleBar
        SizedBox(
          height: 39,
          child: Row(
            children: [
              Expanded(
                child: TabBar(
                  key: onboarding.sidebarTabBarKey,
                  controller: _tabController,
                  labelColor: context.appColors.textPrimary,
                  unselectedLabelColor: context.appColors.textMuted,
                  labelStyle: const TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w600,
                  ),
                  unselectedLabelStyle: const TextStyle(
                    fontSize: 14,
                    fontWeight: FontWeight.w500,
                  ),
                  indicatorColor: context.appColors.accent,
                  indicatorSize: TabBarIndicatorSize.label,
                  indicatorWeight: 2,
                  dividerHeight: 0,
                  tabAlignment: TabAlignment.start,
                  isScrollable: true,
                  padding: const EdgeInsets.only(left: 12),
                  labelPadding: const EdgeInsets.symmetric(horizontal: 8),
                  tabs: const [
                    Tab(text: 'Workers'),
                    Tab(text: 'Tasks'),
                    Tab(text: 'Artifacts'),
                  ],
                ),
              ),
              const SizedBox(width: 4),
            ],
          ),
        ),
        const Divider(height: 1),
        // Tab content
        Expanded(
          child: TabBarView(
            controller: _tabController,
            clipBehavior: Clip.hardEdge,
            children: [
              // Workers tab
              _buildWorkersTab(),
              // Tasks tab
              TaskListPanel(onTaskSelected: widget.onSessionSelected),
              // Artifacts tab
              ArtifactListPanel(onArtifactSelected: widget.onSessionSelected),
            ],
          ),
        ),
        if (defaultTargetPlatform != TargetPlatform.android)
          Material(
            color: context.appColors.bgBase,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                // Notification toasts above settings
                _SidebarNotifications(
                  service: context.read<AppState>().notificationService,
                ),
                const Divider(height: 1),
                // Bottom bar: Settings
                Padding(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 12,
                    vertical: 8,
                  ),
                  child: InkWell(
                    key: onboarding.settingsButtonKey,
                    borderRadius: BorderRadius.circular(10),
                    onTap: () => showSettingsMenu(context),
                    child: Padding(
                      padding: EdgeInsets.symmetric(
                        horizontal: 8,
                        vertical: 10,
                      ),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            Icons.settings_outlined,
                            color: context.appColors.textMuted,
                            size: 20,
                          ),
                          SizedBox(width: 10),
                          Text(
                            'Settings',
                            style: TextStyle(
                              color: context.appColors.textSecondary,
                              fontSize: 14,
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }

  void _saveFilters() {
    final settings = Provider.of<AppState>(context, listen: false).settings;
    settings.workersFilterSearch = _workerSearchQuery;
    settings.workersFilterStatus = _activeStatusFilters.toList();
  }

  void _saveExpanded() {
    final settings = Provider.of<AppState>(context, listen: false).settings;
    settings.workersExpanded = _expandedWorkers.toList();
  }

  bool get _hasActiveFilters =>
      _workerSearchQuery.isNotEmpty || _activeStatusFilters.isNotEmpty;

  void _clearFilters() {
    setState(() {
      _workerSearchController.clear();
      _workerSearchQuery = '';
      _activeStatusFilters.clear();
    });
    _saveFilters();
  }

  /// Normalize 'executing' to 'active' for display grouping.
  static String _normalizeStatus(String status) =>
      status == 'executing' ? 'active' : status;

  Widget _buildWorkersTab() {
    return Consumer<AppState>(
      builder: (context, state, _) {
        final configs = state.workerConfigs;

        // Auto-expand all workers on first build (no saved state)
        if (!_initialized) {
          _initialized = true;
          for (final c in configs) {
            _expandedWorkers.add(c.id);
          }
          _saveExpanded();
        }

        if (configs.isEmpty) {
          return Center(
            child: Text(
              'No workers configured',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 14,
              ),
            ),
          );
        }

        final grouped = state.sessionsByWorker;
        final terminalsByWorker = state.terminalsByWorker;
        final query = _workerSearchQuery.toLowerCase();

        // Apply status filter to sessions per worker
        final filteredGrouped = <String, List<SessionInfo>>{};
        for (final entry in grouped.entries) {
          var sessions = entry.value;
          if (_activeStatusFilters.isNotEmpty) {
            sessions = sessions
                .where(
                  (s) =>
                      _activeStatusFilters.contains(_normalizeStatus(s.status)),
                )
                .toList();
          }
          filteredGrouped[entry.key] = sessions;
        }

        // Filter workers and their sessions by search query
        final filteredConfigs = configs.where((config) {
          if (query.isEmpty) return true;
          final worker = state.getWorker(config.id);
          final sessions = filteredGrouped[config.id] ?? [];
          // Match on worker name or host
          if (config.name.toLowerCase().contains(query) ||
              config.hostWithPort.toLowerCase().contains(query)) {
            return true;
          }
          // Match on any session title
          if (sessions.any(
            (s) =>
                (s.title?.toLowerCase().contains(query) ?? false) ||
                s.shortId.toLowerCase().contains(query),
          )) {
            return true;
          }
          // Match on OS
          if (worker?.serverOs?.toLowerCase().contains(query) ?? false) {
            return true;
          }
          return false;
        }).toList();

        return Column(
          children: [
            // Search bar and status filter chips
            Padding(
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
                            controller: _workerSearchController,
                            onChanged: (v) {
                              setState(() => _workerSearchQuery = v);
                              _saveFilters();
                            },
                            style: TextStyle(
                              color: context.appColors.textPrimary,
                              fontSize: 12,
                            ),
                            decoration: InputDecoration(
                              hintText: 'Search workers & sessions...',
                              hintStyle: TextStyle(
                                color: context.appColors.textMuted,
                                fontSize: 12,
                              ),
                              prefixIcon: Padding(
                                padding: const EdgeInsets.only(
                                  left: 8,
                                  right: 4,
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
                              suffixIcon: _workerSearchQuery.isNotEmpty
                                  ? GestureDetector(
                                      onTap: () {
                                        _workerSearchController.clear();
                                        setState(() => _workerSearchQuery = '');
                                        _saveFilters();
                                      },
                                      child: Padding(
                                        padding: const EdgeInsets.only(
                                          right: 6,
                                        ),
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
                              setState(
                                () => _groupByProject = !_groupByProject,
                              );
                              Provider.of<AppState>(
                                    context,
                                    listen: false,
                                  ).settings.workersGroupByProject =
                                  _groupByProject;
                            },
                          ),
                        ),
                        const SizedBox(width: 4),
                        SizedBox(
                          width: 30,
                          height: 30,
                          child: IconButton(
                            padding: EdgeInsets.zero,
                            icon: Icon(
                              Icons.add_rounded,
                              color: context.appColors.textSecondary,
                              size: 18,
                            ),
                            tooltip: 'Add worker',
                            onPressed: () async {
                              final state = context.read<AppState>();
                              final config = await showWorkerEditDialog(
                                context,
                                sortOrder: state.workerConfigs.length,
                              );
                              if (config != null && context.mounted) {
                                state.addWorker(config);
                              }
                            },
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 6),
                  SizedBox(
                    height: 24,
                    child: Row(
                      children: [
                        Expanded(
                          child: ListView(
                            scrollDirection: Axis.horizontal,
                            children: [
                              for (final status in _statusOrder)
                                Padding(
                                  padding: const EdgeInsets.only(right: 4),
                                  child: _SessionStatusFilterChip(
                                    label: _statusLabels[status]!,
                                    color: _statusColors[status]!,
                                    selected: _activeStatusFilters.contains(
                                      status,
                                    ),
                                    onTap: () {
                                      setState(() {
                                        if (_activeStatusFilters.contains(
                                          status,
                                        )) {
                                          _activeStatusFilters.remove(status);
                                        } else {
                                          _activeStatusFilters.add(status);
                                        }
                                      });
                                      _saveFilters();
                                    },
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
                              child: Icon(
                                Icons.filter_alt_off_rounded,
                                color: context.appColors.textMuted,
                                size: 16,
                              ),
                            ),
                          ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            Expanded(
              child: filteredConfigs.isEmpty && _hasActiveFilters
                  ? Center(
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
                            'No matching results',
                            style: TextStyle(
                              color: context.appColors.textSecondary,
                              fontSize: 13,
                            ),
                          ),
                          const SizedBox(height: 4),
                          GestureDetector(
                            onTap: _clearFilters,
                            child: Text(
                              'Clear filters',
                              style: TextStyle(
                                color: context.appColors.accent,
                                fontSize: 12,
                              ),
                            ),
                          ),
                        ],
                      ),
                    )
                  : ListView.builder(
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      itemCount: filteredConfigs.length,
                      itemBuilder: (context, index) {
                        final config = filteredConfigs[index];
                        final worker = state.getWorker(config.id);
                        final sessions = filteredGrouped[config.id] ?? [];
                        final terminals = terminalsByWorker[config.id] ?? [];
                        final expanded = _expandedWorkers.contains(config.id);
                        return WorkerGroup(
                          config: config,
                          worker: worker,
                          sessions: sessions,
                          terminals: terminals,
                          expanded: expanded,
                          groupByProject: _groupByProject,
                          onToggleExpand: () {
                            setState(() {
                              if (expanded) {
                                _expandedWorkers.remove(config.id);
                              } else {
                                _expandedWorkers.add(config.id);
                              }
                            });
                            _saveExpanded();
                          },
                          onSessionTap: (sessionId) {
                            state.ensureChatPane().switchSession(sessionId);
                            widget.onSessionSelected?.call();
                          },
                          state: state,
                          onSessionSelected: widget.onSessionSelected,
                        );
                      },
                    ),
            ),
          ],
        );
      },
    );
  }
}

/// Renders notification toasts inline in the sidebar, above the Settings button.
/// Uses a simple [ListenableBuilder] instead of [AnimatedList] for simplicity
/// in a constrained sidebar context.
class _SidebarNotifications extends StatelessWidget {
  final NotificationService service;

  const _SidebarNotifications({required this.service});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: service,
      builder: (context, _) {
        final notifications = service.notifications;
        if (notifications.isEmpty) return const SizedBox.shrink();

        return Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Divider(height: 1),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  for (final n in notifications)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: NotificationToast(
                        notification: n,
                        onDismiss: () => service.dismiss(n.id),
                        compact: true,
                      ),
                    ),
                ],
              ),
            ),
          ],
        );
      },
    );
  }
}

class _SessionStatusFilterChip extends StatelessWidget {
  final String label;
  final Color color;
  final bool selected;
  final VoidCallback onTap;

  const _SessionStatusFilterChip({
    required this.label,
    required this.color,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: selected ? color.withAlpha(40) : Colors.transparent,
          borderRadius: BorderRadius.circular(6),
          border: Border.all(
            color: selected ? color.withAlpha(120) : context.appColors.divider,
            width: 1,
          ),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: selected ? color : context.appColors.textMuted,
            fontSize: 10,
            fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
          ),
        ),
      ),
    );
  }
}
