import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../../models/session_info.dart';
import '../../../models/worker_config.dart';
import '../../../services/notification_service.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import '../../dialogs/worker_edit_dialog.dart';
import '../../onboarding_keys.dart' as onboarding;
import '../notification_toast.dart';
import '../settings_menu.dart';
import 'artifact_list_panel.dart';
import 'helpers.dart';
import 'task_list_panel.dart';
import 'worker_group.dart';

/// Compare sessions by sort_order ascending (nulls last), then createdAt desc.
int compareBySortOrder(SessionInfo a, SessionInfo b) {
  const maxOrder = 1 << 62;
  final aOrder = a.sortOrder ?? maxOrder;
  final bOrder = b.sortOrder ?? maxOrder;
  final cmp = aOrder.compareTo(bOrder);
  if (cmp != 0) return cmp;
  final aTime = a.createdAt ?? DateTime(2000);
  final bTime = b.createdAt ?? DateTime(2000);
  return bTime.compareTo(aTime);
}

/// Computes the ordered flat list of *visible* sessions across all workers,
/// respecting worker expansion and project sub-group collapse state.
///
/// Used for Shift+click range selection in the workers tab. Exposed at library
/// level so it can be unit-tested without a widget environment.
List<SessionInfo> computeFlatVisibleSessionList({
  required List<WorkerConfig> configs,
  required Map<String, List<SessionInfo>> groupedSessions,
  required Set<String> expandedWorkers,
  required bool groupByProject,
  required Map<String, Set<String>> collapsedWorkerProjects,
}) {
  final result = <SessionInfo>[];
  for (final config in configs) {
    if (!expandedWorkers.contains(config.id)) continue;
    final sessions = groupedSessions[config.id] ?? [];

    if (!groupByProject) {
      // Sort by sort_order ascending (nulls last), then newest-first.
      final sorted = [...sessions]..sort(compareBySortOrder);
      result.addAll(sorted);
    } else {
      // Replicate the project grouping logic from WorkerGroup.
      final byProject = <String?, List<SessionInfo>>{};
      for (final s in sessions) {
        final projectName = s.mainProjectPath
            ?.split('/')
            .where((p) => p.isNotEmpty)
            .lastOrNull;
        byProject.putIfAbsent(projectName, () => []).add(s);
      }
      final projectNames = byProject.keys.toList()
        ..sort((a, b) {
          if (a == null && b == null) return 0;
          if (a == null) return 1;
          if (b == null) return -1;
          return a.toLowerCase().compareTo(b.toLowerCase());
        });
      final collapsedProjects = collapsedWorkerProjects[config.id] ?? {};
      for (final projectName in projectNames) {
        final collapseKey = projectName ?? '\x00other';
        if (collapsedProjects.contains(collapseKey)) continue;
        final projectSessions = [...byProject[projectName]!]
          ..sort(compareBySortOrder);
        result.addAll(projectSessions);
      }
    }
  }
  return result;
}

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

  // ---- Workers-tab multi-select state ----
  final Set<String> _selectedSessionIds = {};

  /// Anchor index for Shift+click range selection. Points into
  /// [_currentFlatSessionList].
  int? _lastClickedVisibleSessionIndex;

  /// Populated at the start of each [_buildWorkersTab] build; used by
  /// [_handleSessionTap] to resolve Shift+click ranges.
  List<SessionInfo> _currentFlatSessionList = [];

  /// Per-worker project sub-group collapse state. Key: workerId,
  /// Value: set of collapsed project keys (project name or '\x00other').
  /// Lifted from WorkerGroup so the parent can compute the flat visible list.
  final Map<String, Set<String>> _collapsedWorkerProjects = {};

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
                // Update banner (shown above the settings divider when an
                // update is available and not yet dismissed).
                _UpdateBanner(appState: context.read<AppState>()),
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

  // ---------------------------------------------------------------------------
  // Workers-tab multi-select helpers
  // ---------------------------------------------------------------------------

  /// Handles a tap on a session tile, respecting Shift/Ctrl/Meta modifiers.
  ///
  /// - **Shift+click**: range-selects from the last clicked index to [flatIndex].
  /// - **Ctrl/Meta+click**: toggles [sessionId] in the selection.
  /// - **Plain click** while selection is non-empty: toggles [sessionId].
  /// - **Plain click** while selection is empty: opens the session in a pane.
  void _handleSessionTap(
    BuildContext context,
    String sessionId,
    int flatIndex,
    AppState state,
  ) {
    final keys = HardwareKeyboard.instance.logicalKeysPressed;
    final shift =
        keys.contains(LogicalKeyboardKey.shiftLeft) ||
        keys.contains(LogicalKeyboardKey.shiftRight);
    final ctrl =
        keys.contains(LogicalKeyboardKey.controlLeft) ||
        keys.contains(LogicalKeyboardKey.controlRight) ||
        keys.contains(LogicalKeyboardKey.metaLeft) ||
        keys.contains(LogicalKeyboardKey.metaRight);

    if (shift && _lastClickedVisibleSessionIndex != null) {
      final anchor = _lastClickedVisibleSessionIndex!;
      final lo = anchor < flatIndex ? anchor : flatIndex;
      final hi = anchor < flatIndex ? flatIndex : anchor;
      setState(() {
        for (var i = lo; i <= hi; i++) {
          if (i < _currentFlatSessionList.length) {
            _selectedSessionIds.add(_currentFlatSessionList[i].sessionId);
          }
        }
        _lastClickedVisibleSessionIndex = flatIndex;
      });
    } else if (ctrl) {
      setState(() {
        if (_selectedSessionIds.contains(sessionId)) {
          _selectedSessionIds.remove(sessionId);
        } else {
          _selectedSessionIds.add(sessionId);
        }
        _lastClickedVisibleSessionIndex = flatIndex;
      });
    } else if (_selectedSessionIds.isNotEmpty) {
      setState(() {
        if (_selectedSessionIds.contains(sessionId)) {
          _selectedSessionIds.remove(sessionId);
        } else {
          _selectedSessionIds.add(sessionId);
        }
        _lastClickedVisibleSessionIndex = flatIndex;
      });
    } else {
      // No selection, no modifiers — default: open session in pane.
      setState(() => _lastClickedVisibleSessionIndex = flatIndex);
      state.ensureChatPane().switchSession(sessionId);
      widget.onSessionSelected?.call();
    }
  }

  /// Move the single selected session up or down in the list via Ctrl+Arrow.
  void _handleKeyboardReorder(AppState state, {required bool isUp}) {
    if (_selectedSessionIds.length != 1) return;
    final selectedId = _selectedSessionIds.first;

    // Find the session in the flat list
    final flatIdx = _currentFlatSessionList.indexWhere(
      (s) => s.sessionId == selectedId,
    );
    if (flatIdx < 0) return;

    final session = _currentFlatSessionList[flatIdx];
    final worker = state.getWorker(session.workerId);
    if (worker == null) return;

    if (isUp && flatIdx == 0) return; // Already at top
    if (!isUp && flatIdx >= _currentFlatSessionList.length - 1) return;

    String? afterSessionId;
    if (isUp) {
      // Move before the item currently above. afterSessionId = item two above,
      // or null if moving to position 0.
      afterSessionId = flatIdx >= 2
          ? _currentFlatSessionList[flatIdx - 2].sessionId
          : null;
    } else {
      // Move after the item currently below.
      afterSessionId = _currentFlatSessionList[flatIdx + 1].sessionId;
    }

    worker.reorderSession(selectedId, afterSessionId: afterSessionId);
  }

  /// The thin bar shown below the filter bar when sessions are selected.
  Widget _buildSessionSelectionBar(BuildContext context, AppState state) {
    final count = _selectedSessionIds.length;
    return Container(
      color: context.appColors.accent.withAlpha(18),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 5),
      child: Row(
        children: [
          Icon(
            Icons.check_box_outlined,
            size: 14,
            color: context.appColors.accent,
          ),
          const SizedBox(width: 6),
          Text(
            '$count session${count == 1 ? '' : 's'} selected',
            style: TextStyle(
              color: context.appColors.accentLight,
              fontSize: 11,
              fontWeight: FontWeight.w500,
            ),
          ),
          const Spacer(),
          GestureDetector(
            onTap: () => setState(() => _selectedSessionIds.clear()),
            child: const Tooltip(
              message: 'Clear selection (Esc)',
              child: Icon(Icons.close_rounded, size: 14),
            ),
          ),
        ],
      ),
    );
  }

  /// Shows the bulk right-click context menu for the current session selection.
  void _showBulkSessionContextMenu(
    BuildContext context,
    Offset position,
    AppState state,
  ) {
    final count = _selectedSessionIds.length;
    final overlay = Overlay.of(context).context.findRenderObject() as RenderBox;

    // Determine which bulk actions are applicable based on selected states.
    final selectedSessions = _selectedSessionIds
        .map((id) => state.getSession(id))
        .whereType<SessionInfo>()
        .toList();
    final hasActive = selectedSessions.any(
      (s) => !isTerminalStatus(s.status) && s.status != 'paused',
    );
    final hasPaused = selectedSessions.any((s) => s.status == 'paused');
    final hasNonTerminal = selectedSessions.any(
      (s) => !isTerminalStatus(s.status),
    );

    showMenu<String>(
      context: context,
      position: RelativeRect.fromRect(
        position & const Size(1, 1),
        Offset.zero & overlay.size,
      ),
      color: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      items: [
        PopupMenuItem(
          enabled: false,
          height: 28,
          child: Text(
            '$count session${count == 1 ? '' : 's'} selected',
            style: TextStyle(
              color: context.appColors.textMuted,
              fontSize: 11,
              fontWeight: FontWeight.w500,
            ),
          ),
        ),
        const PopupMenuDivider(),
        if (hasActive)
          PopupMenuItem(
            value: 'pause',
            child: Row(
              children: [
                Icon(
                  Icons.pause_rounded,
                  color: context.appColors.accentLight,
                  size: 18,
                ),
                const SizedBox(width: 8),
                Text(
                  'Pause all running',
                  style: TextStyle(color: context.appColors.textPrimary),
                ),
              ],
            ),
          ),
        if (hasPaused)
          PopupMenuItem(
            value: 'resume',
            child: Row(
              children: [
                Icon(
                  Icons.play_arrow_rounded,
                  color: context.appColors.accentLight,
                  size: 18,
                ),
                const SizedBox(width: 8),
                Text(
                  'Resume all paused',
                  style: TextStyle(color: context.appColors.textPrimary),
                ),
              ],
            ),
          ),
        if (hasNonTerminal)
          PopupMenuItem(
            value: 'end',
            child: Row(
              children: [
                Icon(
                  Icons.stop_circle_outlined,
                  color: context.appColors.errorText,
                  size: 18,
                ),
                const SizedBox(width: 8),
                Text(
                  'End $count session${count == 1 ? '' : 's'}\u2026',
                  style: TextStyle(color: context.appColors.errorText),
                ),
              ],
            ),
          ),
        const PopupMenuDivider(),
        PopupMenuItem(
          value: 'clear',
          child: Row(
            children: [
              Icon(
                Icons.close_rounded,
                color: context.appColors.textSecondary,
                size: 18,
              ),
              const SizedBox(width: 8),
              Text(
                'Clear selection',
                style: TextStyle(color: context.appColors.textPrimary),
              ),
            ],
          ),
        ),
      ],
    ).then((value) {
      if (!context.mounted || value == null) return;
      switch (value) {
        case 'pause':
          _bulkPauseSessions(state);
        case 'resume':
          _bulkResumeSessions(state);
        case 'end':
          _confirmBulkEndSessions(context, state);
        case 'clear':
          setState(() => _selectedSessionIds.clear());
      }
    });
  }

  void _bulkPauseSessions(AppState state) {
    final ids = List<String>.from(_selectedSessionIds);
    setState(() => _selectedSessionIds.clear());
    for (final id in ids) {
      final session = state.getSession(id);
      if (session == null ||
          isTerminalStatus(session.status) ||
          session.status == 'paused') {
        continue;
      }
      state.pauseSessionDirect(id, session.workerId);
    }
  }

  void _bulkResumeSessions(AppState state) {
    final ids = List<String>.from(_selectedSessionIds);
    setState(() => _selectedSessionIds.clear());
    for (final id in ids) {
      final session = state.getSession(id);
      if (session == null || session.status != 'paused') continue;
      state.resumeSessionDirect(id, session.workerId);
    }
  }

  Future<void> _confirmBulkEndSessions(
    BuildContext context,
    AppState state,
  ) async {
    final count = _selectedSessionIds.length;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        title: Text(
          'End $count session${count == 1 ? '' : 's'}',
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 16),
        ),
        content: Text(
          'End $count session${count == 1 ? '' : 's'}? This cannot be undone.',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 14,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text(
              'Cancel',
              style: TextStyle(color: context.appColors.textSecondary),
            ),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: context.appColors.errorText,
            ),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text(
              'End sessions',
              style: TextStyle(color: Colors.white),
            ),
          ),
        ],
      ),
    );
    if (confirmed != true || !context.mounted) return;
    final ids = List<String>.from(_selectedSessionIds);
    setState(() => _selectedSessionIds.clear());
    for (final id in ids) {
      final session = state.getSession(id);
      if (session == null || isTerminalStatus(session.status)) continue;
      state.cancelSessionDirect(id, session.workerId);
    }
  }

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

        // Compute the global flat visible list for Shift+click range selection.
        _currentFlatSessionList = computeFlatVisibleSessionList(
          configs: filteredConfigs,
          groupedSessions: filteredGrouped,
          expandedWorkers: _expandedWorkers,
          groupByProject: _groupByProject,
          collapsedWorkerProjects: _collapsedWorkerProjects,
        );

        return Focus(
          autofocus: false,
          onKeyEvent: (node, event) {
            if (event is KeyDownEvent &&
                event.logicalKey == LogicalKeyboardKey.escape &&
                _selectedSessionIds.isNotEmpty) {
              setState(() => _selectedSessionIds.clear());
              return KeyEventResult.handled;
            }
            // Ctrl+Up/Down to reorder the single selected session
            if (event is KeyDownEvent &&
                _selectedSessionIds.length == 1 &&
                _workerSearchQuery.isEmpty &&
                _activeStatusFilters.isEmpty &&
                HardwareKeyboard.instance.logicalKeysPressed.any(
                  (k) =>
                      k == LogicalKeyboardKey.controlLeft ||
                      k == LogicalKeyboardKey.controlRight ||
                      k == LogicalKeyboardKey.metaLeft ||
                      k == LogicalKeyboardKey.metaRight,
                )) {
              final isUp =
                  event.logicalKey == LogicalKeyboardKey.arrowUp;
              final isDown =
                  event.logicalKey == LogicalKeyboardKey.arrowDown;
              if (isUp || isDown) {
                _handleKeyboardReorder(state, isUp: isUp);
                return KeyEventResult.handled;
              }
            }
            return KeyEventResult.ignored;
          },
          child: Column(
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
                                          setState(
                                            () => _workerSearchQuery = '',
                                          );
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
              if (_selectedSessionIds.isNotEmpty)
                _buildSessionSelectionBar(context, state),
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
                            selectedSessionIds: _selectedSessionIds,
                            currentFlatList: _currentFlatSessionList,
                            onSessionSelectTap: (sessionId, flatIndex) =>
                                _handleSessionTap(
                                  context,
                                  sessionId,
                                  flatIndex,
                                  state,
                                ),
                            onBulkSecondaryTap: (sessionId, position) =>
                                _showBulkSessionContextMenu(
                                  context,
                                  position,
                                  state,
                                ),
                            collapsedProjects:
                                _collapsedWorkerProjects[config.id] ?? const {},
                            onProjectToggle: (collapseKey) {
                              setState(() {
                                final set = _collapsedWorkerProjects
                                    .putIfAbsent(config.id, () => {});
                                if (set.contains(collapseKey)) {
                                  set.remove(collapseKey);
                                } else {
                                  set.add(collapseKey);
                                }
                              });
                            },
                            reorderEnabled:
                                _workerSearchQuery.isEmpty &&
                                _activeStatusFilters.isEmpty &&
                                _selectedSessionIds.isEmpty,
                            onReorder: (sessionId, afterSessionId) {
                              worker?.reorderSession(
                                sessionId,
                                afterSessionId: afterSessionId,
                              );
                            },
                          );
                        },
                      ),
              ),
            ],
          ),
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

// ---------------------------------------------------------------------------
// Update banner
// ---------------------------------------------------------------------------

/// Compact banner shown above the divider/settings row when a new client
/// version is available. Tapping anywhere on the row opens Settings → About
/// (where the user can trigger the download). The dismiss button hides the
/// banner for this release.
class _UpdateBanner extends StatelessWidget {
  final AppState appState;

  const _UpdateBanner({required this.appState});

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: appState.updateService,
      builder: (ctx, _) {
        final svc = appState.updateService;
        if (!svc.showBanner) return const SizedBox.shrink();

        final latest = svc.latestVersion!;

        return Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Divider(height: 1),
            InkWell(
              onTap: () => showSettingsMenu(context),
              child: Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: 12,
                  vertical: 8,
                ),
                child: Row(
                  children: [
                    Icon(
                      Icons.new_releases_outlined,
                      size: 16,
                      color: context.appColors.accent,
                    ),
                    const SizedBox(width: 8),
                    Expanded(
                      child: Text(
                        'v$latest available',
                        style: TextStyle(
                          color: context.appColors.accent,
                          fontSize: 13,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
                    ),
                    GestureDetector(
                      onTap: svc.dismissCurrentUpdate,
                      child: Icon(
                        Icons.close,
                        size: 16,
                        color: context.appColors.textMuted,
                      ),
                    ),
                  ],
                ),
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
