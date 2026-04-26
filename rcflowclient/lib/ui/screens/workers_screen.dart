import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/worker_config.dart';
import '../../services/worker_connection.dart';
import '../../state/app_state.dart';
import '../../theme.dart';
import '../dialogs/worker_edit_dialog.dart';
import '../widgets/custom_title_bar.dart';
import 'server_config_screen.dart';

void showWorkersScreen(BuildContext context) {
  Navigator.of(context).push(MaterialPageRoute(builder: (_) => _WorkersPage()));
}

// ---------------------------------------------------------------------------
// Full-screen page
// ---------------------------------------------------------------------------

class _WorkersPage extends StatelessWidget {
  const _WorkersPage();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: context.appColors.bgBase,
      body: Column(
        children: [
          CustomTitleBar(),
          AppBar(
            backgroundColor: context.appColors.bgBase,
            leading: IconButton(
              icon: Icon(
                Icons.arrow_back,
                color: context.appColors.textPrimary,
              ),
              onPressed: () => Navigator.of(context).pop(),
            ),
            title: Text(
              'Manage Workers',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 18,
              ),
            ),
            actions: [
              Consumer<AppState>(
                builder: (context, state, _) => Padding(
                  padding: const EdgeInsets.only(right: 12),
                  child: FilledButton.icon(
                    onPressed: () async {
                      final config = await showWorkerEditDialog(
                        context,
                        sortOrder: state.workerConfigs.length,
                      );
                      if (config != null && context.mounted) {
                        state.addWorker(config);
                      }
                    },
                    icon: Icon(Icons.add_rounded, size: 18),
                    label: Text('Add'),
                    style: FilledButton.styleFrom(
                      backgroundColor: context.appColors.accent,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(
                        horizontal: 16,
                        vertical: 8,
                      ),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(10),
                      ),
                    ),
                  ),
                ),
              ),
            ],
          ),
          const Expanded(
            child: Padding(
              padding: EdgeInsets.all(24),
              child: _WorkersContent(),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Shared content
// ---------------------------------------------------------------------------

class _WorkersContent extends StatefulWidget {
  const _WorkersContent();

  @override
  State<_WorkersContent> createState() => _WorkersContentState();
}

class _WorkersContentState extends State<_WorkersContent> {
  final TextEditingController _searchController = TextEditingController();
  String _searchQuery = '';
  final Set<WorkerConnectionStatus> _activeStatusFilters = {};

  static const _statusByName = {
    'connected': WorkerConnectionStatus.connected,
    'connecting': WorkerConnectionStatus.connecting,
    'reconnecting': WorkerConnectionStatus.reconnecting,
    'disconnected': WorkerConnectionStatus.disconnected,
  };

  static const _statusLabels = {
    WorkerConnectionStatus.connected: 'Connected',
    WorkerConnectionStatus.connecting: 'Connecting',
    WorkerConnectionStatus.reconnecting: 'Reconnecting',
    WorkerConnectionStatus.disconnected: 'Disconnected',
  };
  static const _statusColors = {
    WorkerConnectionStatus.connected: Color(0xFF4ADE80),
    WorkerConnectionStatus.connecting: Color(0xFFFBBF24),
    WorkerConnectionStatus.reconnecting: Color(0xFFFBBF24),
    WorkerConnectionStatus.disconnected: Color(0xFF6B7280),
  };

  @override
  void initState() {
    super.initState();
    final settings = Provider.of<AppState>(context, listen: false).settings;
    _searchQuery = settings.workersFilterSearch;
    _searchController.text = _searchQuery;
    for (final name in settings.workersFilterStatus) {
      final status = _statusByName[name];
      if (status != null) _activeStatusFilters.add(status);
    }
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  void _saveFilters() {
    final settings = Provider.of<AppState>(context, listen: false).settings;
    settings.workersFilterSearch = _searchQuery;
    settings.workersFilterStatus = _activeStatusFilters
        .map((s) => s.name)
        .toList();
  }

  bool get _hasActiveFilters =>
      _searchQuery.isNotEmpty || _activeStatusFilters.isNotEmpty;

  void _clearFilters() {
    setState(() {
      _searchController.clear();
      _searchQuery = '';
      _activeStatusFilters.clear();
    });
    _saveFilters();
  }

  List<(WorkerConfig, WorkerConnection?)> _filterWorkers(
    List<WorkerConfig> configs,
    AppState state,
  ) {
    var pairs = configs.map((c) => (c, state.getWorker(c.id))).toList();

    if (_activeStatusFilters.isNotEmpty) {
      pairs = pairs.where((pair) {
        final status = pair.$2?.status ?? WorkerConnectionStatus.disconnected;
        return _activeStatusFilters.contains(status);
      }).toList();
    }

    if (_searchQuery.isNotEmpty) {
      final query = _searchQuery.toLowerCase();
      pairs = pairs.where((pair) {
        final config = pair.$1;
        final worker = pair.$2;
        final status = worker?.status ?? WorkerConnectionStatus.disconnected;
        final statusText = _statusLabels[status] ?? '';
        return config.name.toLowerCase().contains(query) ||
            config.hostWithPort.toLowerCase().contains(query) ||
            statusText.toLowerCase().contains(query) ||
            (worker?.serverOs?.toLowerCase().contains(query) ?? false);
      }).toList();
    }

    return pairs;
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<AppState>(
      builder: (context, state, _) {
        final configs = state.workerConfigs;
        if (configs.isEmpty) {
          return Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Container(
                width: double.infinity,
                padding: EdgeInsets.all(24),
                decoration: BoxDecoration(
                  color: context.appColors.bgElevated,
                  borderRadius: BorderRadius.circular(14),
                ),
                child: Column(
                  children: [
                    Icon(
                      Icons.dns_outlined,
                      color: context.appColors.textMuted,
                      size: 40,
                    ),
                    SizedBox(height: 12),
                    Text(
                      'No workers configured',
                      style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 14,
                      ),
                    ),
                    SizedBox(height: 4),
                    Text(
                      'Add a worker to connect to an RCFlow server',
                      style: TextStyle(
                        color: context.appColors.textMuted,
                        fontSize: 12,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          );
        }

        final filtered = _filterWorkers(configs, state);

        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _buildFilterBar(context),
            const SizedBox(height: 12),
            Expanded(
              child: filtered.isEmpty && _hasActiveFilters
                  ? _buildNoResults(context)
                  : ListView.separated(
                      itemCount: filtered.length,
                      separatorBuilder: (context, index) =>
                          const SizedBox(height: 8),
                      itemBuilder: (context, index) {
                        final (config, worker) = filtered[index];
                        return _WorkerCard(
                          config: config,
                          worker: worker,
                          onEdit: () async {
                            final updated = await showWorkerEditDialog(
                              context,
                              existing: config,
                            );
                            if (updated != null && context.mounted) {
                              state.updateWorker(updated);
                            }
                          },
                          onRemove: () =>
                              _confirmRemove(context, state, config),
                          onSettings: worker?.isConnected == true
                              ? () => showServerConfigScreen(
                                  context,
                                  ws: worker!.ws,
                                  connection: worker,
                                  workerName: config.name,
                                )
                              : null,
                          onToggleConnect: () {
                            if (worker?.isConnected == true) {
                              state.disconnectWorker(config.id);
                            } else {
                              state.connectWorker(config.id);
                            }
                          },
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
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        SizedBox(
          height: 36,
          child: TextField(
            controller: _searchController,
            onChanged: (v) {
              setState(() => _searchQuery = v);
              _saveFilters();
            },
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 13,
            ),
            decoration: InputDecoration(
              hintText: 'Search workers by name, host, status...',
              hintStyle: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 13,
              ),
              prefixIcon: Padding(
                padding: const EdgeInsets.only(left: 12, right: 6),
                child: Icon(
                  Icons.search_rounded,
                  color: context.appColors.textMuted,
                  size: 18,
                ),
              ),
              prefixIconConstraints: const BoxConstraints(
                maxWidth: 36,
                maxHeight: 36,
              ),
              suffixIcon: _searchQuery.isNotEmpty
                  ? GestureDetector(
                      onTap: () {
                        _searchController.clear();
                        setState(() => _searchQuery = '');
                        _saveFilters();
                      },
                      child: Padding(
                        padding: const EdgeInsets.only(right: 10),
                        child: Icon(
                          Icons.close_rounded,
                          color: context.appColors.textMuted,
                          size: 16,
                        ),
                      ),
                    )
                  : null,
              suffixIconConstraints: const BoxConstraints(
                maxWidth: 30,
                maxHeight: 36,
              ),
              filled: true,
              fillColor: context.appColors.bgElevated,
              contentPadding: const EdgeInsets.symmetric(
                horizontal: 12,
                vertical: 0,
              ),
              border: OutlineInputBorder(
                borderSide: BorderSide.none,
                borderRadius: BorderRadius.circular(10),
              ),
              enabledBorder: OutlineInputBorder(
                borderSide: BorderSide.none,
                borderRadius: BorderRadius.circular(10),
              ),
              focusedBorder: OutlineInputBorder(
                borderSide: BorderSide(
                  color: context.appColors.accent,
                  width: 1,
                ),
                borderRadius: BorderRadius.circular(10),
              ),
            ),
          ),
        ),
        const SizedBox(height: 8),
        SizedBox(
          height: 28,
          child: Row(
            children: [
              Expanded(
                child: ListView(
                  scrollDirection: Axis.horizontal,
                  children: [
                    for (final status in WorkerConnectionStatus.values)
                      Padding(
                        padding: const EdgeInsets.only(right: 6),
                        child: _WorkerStatusFilterChip(
                          label: _statusLabels[status]!,
                          color: _statusColors[status]!,
                          selected: _activeStatusFilters.contains(status),
                          onTap: () {
                            setState(() {
                              if (_activeStatusFilters.contains(status)) {
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
                    padding: const EdgeInsets.only(left: 6),
                    child: Icon(
                      Icons.filter_alt_off_rounded,
                      color: context.appColors.textMuted,
                      size: 18,
                    ),
                  ),
                ),
            ],
          ),
        ),
      ],
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
            size: 40,
          ),
          const SizedBox(height: 12),
          Text(
            'No matching workers',
            style: TextStyle(
              color: context.appColors.textSecondary,
              fontSize: 14,
            ),
          ),
          const SizedBox(height: 6),
          GestureDetector(
            onTap: _clearFilters,
            child: Text(
              'Clear filters',
              style: TextStyle(color: context.appColors.accent, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }

  void _confirmRemove(
    BuildContext context,
    AppState state,
    WorkerConfig config,
  ) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: Text(
          'Remove worker?',
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 18),
        ),
        content: Text(
          'This will disconnect and remove "${config.name}".',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 14,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text(
              'Cancel',
              style: TextStyle(color: context.appColors.textSecondary),
            ),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: context.appColors.errorText,
            ),
            onPressed: () {
              Navigator.of(ctx).pop();
              state.removeWorker(config.id);
            },
            child: const Text('Remove', style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Worker status filter chip
// ---------------------------------------------------------------------------

class _WorkerStatusFilterChip extends StatelessWidget {
  final String label;
  final Color color;
  final bool selected;
  final VoidCallback onTap;

  const _WorkerStatusFilterChip({
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
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: selected ? color.withAlpha(40) : Colors.transparent,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
            color: selected ? color.withAlpha(120) : context.appColors.divider,
            width: 1,
          ),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: selected ? color : context.appColors.textMuted,
            fontSize: 11,
            fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Worker card
// ---------------------------------------------------------------------------

class _WorkerCard extends StatelessWidget {
  final WorkerConfig config;
  final WorkerConnection? worker;
  final VoidCallback onEdit;
  final VoidCallback onRemove;
  final VoidCallback? onSettings;
  final VoidCallback onToggleConnect;

  const _WorkerCard({
    required this.config,
    required this.worker,
    required this.onEdit,
    required this.onRemove,
    this.onSettings,
    required this.onToggleConnect,
  });

  @override
  Widget build(BuildContext context) {
    final status = worker?.status ?? WorkerConnectionStatus.disconnected;
    final (statusText, statusColor) = switch (status) {
      WorkerConnectionStatus.connected => (
        'Connected',
        context.appColors.successText,
      ),
      WorkerConnectionStatus.connecting => (
        'Connecting...',
        context.appColors.toolAccent,
      ),
      WorkerConnectionStatus.reconnecting => (
        'Reconnecting...',
        context.appColors.toolAccent,
      ),
      WorkerConnectionStatus.disconnected => (
        'Disconnected',
        context.appColors.textMuted,
      ),
    };

    return Container(
      padding: EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: context.appColors.bgElevated,
        borderRadius: BorderRadius.circular(14),
        border: status == WorkerConnectionStatus.connected
            ? Border.all(
                color: context.appColors.successText.withAlpha(60),
                width: 1,
              )
            : null,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 10,
                height: 10,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: statusColor,
                  boxShadow: [
                    BoxShadow(
                      color: statusColor.withAlpha(80),
                      blurRadius: 4,
                      spreadRadius: 1,
                    ),
                  ],
                ),
              ),
              SizedBox(width: 10),
              Expanded(
                child: Text(
                  config.name,
                  style: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 15,
                    fontWeight: FontWeight.w600,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          SizedBox(height: 8),
          Text(
            '${config.hostWithPort}  \u00B7  $statusText',
            style: TextStyle(color: statusColor, fontSize: 12),
          ),
          if (worker?.serverOs != null)
            Padding(
              padding: EdgeInsets.only(top: 4),
              child: Text(
                'OS: ${worker!.serverOs}',
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 11,
                ),
              ),
            ),
          if (config.autoConnect)
            Padding(
              padding: EdgeInsets.only(top: 4),
              child: Text(
                'Auto-connect: ON',
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 11,
                ),
              ),
            ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 4,
            children: [
              _SmallButton(
                label: 'Edit',
                icon: Icons.edit_outlined,
                onPressed: onEdit,
              ),
              if (onSettings != null)
                _SmallButton(
                  label: 'Settings',
                  icon: Icons.settings_outlined,
                  onPressed: onSettings,
                  color: context.appColors.accentLight,
                ),
              _SmallButton(
                label: 'Remove',
                icon: Icons.delete_outline,
                onPressed: onRemove,
                color: context.appColors.errorText,
              ),
              _SmallButton(
                label: status == WorkerConnectionStatus.connected
                    ? 'Disconnect'
                    : 'Connect',
                icon: status == WorkerConnectionStatus.connected
                    ? Icons.link_off_rounded
                    : Icons.link_rounded,
                onPressed:
                    status == WorkerConnectionStatus.connecting ||
                        status == WorkerConnectionStatus.reconnecting
                    ? null
                    : onToggleConnect,
                color: status == WorkerConnectionStatus.connected
                    ? context.appColors.textSecondary
                    : context.appColors.accentLight,
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _SmallButton extends StatelessWidget {
  final String label;
  final IconData icon;
  final VoidCallback? onPressed;
  final Color? color;

  const _SmallButton({
    required this.label,
    required this.icon,
    required this.onPressed,
    this.color,
  });

  @override
  Widget build(BuildContext context) {
    final c = color ?? context.appColors.textSecondary;
    return TextButton.icon(
      onPressed: onPressed,
      icon: Icon(
        icon,
        size: 16,
        color: onPressed != null ? c : context.appColors.textMuted,
      ),
      label: Text(
        label,
        style: TextStyle(
          color: onPressed != null ? c : context.appColors.textMuted,
          fontSize: 12,
        ),
      ),
      style: TextButton.styleFrom(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        minimumSize: Size.zero,
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
      ),
    );
  }
}
