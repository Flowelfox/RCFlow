import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/split_tree.dart';
import '../../services/websocket_service.dart';
import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';

/// Full-pane worker settings view.
///
/// Displays plugin management UI for a managed coding agent tool
/// (e.g. ``claude_code``).  Shows the "plugins" section by default.
class WorkerSettingsPane extends StatefulWidget {
  final String paneId;
  final PaneState pane;

  const WorkerSettingsPane({
    super.key,
    required this.paneId,
    required this.pane,
  });

  @override
  State<WorkerSettingsPane> createState() => _WorkerSettingsPaneState();
}

class _WorkerSettingsPaneState extends State<WorkerSettingsPane> {
  @override
  Widget build(BuildContext context) {
    final appState = context.watch<AppState>();
    final toolName = widget.pane.workerSettingsTool;

    if (toolName == null) {
      return _emptyState(context, appState);
    }

    final isActive = appState.activePaneId == widget.paneId;
    final multiPane = appState.paneCount > 1;

    return ChangeNotifierProvider<PaneState>.value(
      value: widget.pane,
      child: Column(
        children: [
          _WorkerSettingsPaneHeader(
            paneId: widget.paneId,
            toolName: toolName,
            appState: appState,
            isActive: isActive,
            multiPane: multiPane,
          ),
          Expanded(
            child: _PluginsSection(paneId: widget.paneId, toolName: toolName),
          ),
        ],
      ),
    );
  }

  Widget _emptyState(BuildContext context, AppState appState) {
    return Column(
      children: [
        _WorkerSettingsPaneHeader(
          paneId: widget.paneId,
          toolName: null,
          appState: appState,
          isActive: appState.activePaneId == widget.paneId,
          multiPane: appState.paneCount > 1,
        ),
        const Expanded(child: SizedBox()),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

class _WorkerSettingsPaneHeader extends StatelessWidget {
  final String paneId;
  final String? toolName;
  final AppState appState;
  final bool isActive;
  final bool multiPane;

  const _WorkerSettingsPaneHeader({
    required this.paneId,
    required this.toolName,
    required this.appState,
    required this.isActive,
    required this.multiPane,
  });

  @override
  Widget build(BuildContext context) {
    final displayName = toolName == 'claude_code'
        ? 'Claude Code'
        : toolName == 'codex'
        ? 'Codex'
        : toolName == 'opencode'
        ? 'OpenCode'
        : toolName ?? 'Worker Settings';

    return Container(
      height: 32,
      decoration: BoxDecoration(
        color: isActive
            ? context.appColors.accent.withAlpha(20)
            : context.appColors.bgSurface,
        border: Border(bottom: BorderSide(color: context.appColors.divider)),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: Row(
        children: [
          if (appState.panes[paneId]?.canGoBack ?? false)
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.arrow_back_rounded,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Back',
                onPressed: () => appState.goBack(paneId),
              ),
            ),
          if (isActive)
            Container(
              width: 6,
              height: 6,
              margin: const EdgeInsets.only(right: 6),
              decoration: BoxDecoration(
                color: context.appColors.accent,
                shape: BoxShape.circle,
              ),
            ),
          Icon(
            Icons.extension_outlined,
            color: context.appColors.textMuted,
            size: 14,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              '$displayName — Plugins',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 12,
                fontWeight: FontWeight.w500,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (multiPane) ...[
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.vertical_split_outlined,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Split',
                onPressed: () =>
                    appState.splitPane(paneId, SplitAxis.horizontal),
              ),
            ),
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.close_rounded,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Close',
                onPressed: () => appState.closePane(paneId),
              ),
            ),
          ] else
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.close_rounded,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Close settings',
                onPressed: () => appState.closeWorkerSettingsView(paneId),
              ),
            ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Plugin list section
// ---------------------------------------------------------------------------

class _PluginsSection extends StatefulWidget {
  final String paneId;
  final String toolName;

  const _PluginsSection({required this.paneId, required this.toolName});

  @override
  State<_PluginsSection> createState() => _PluginsSectionState();
}

class _PluginsSectionState extends State<_PluginsSection> {
  List<Map<String, dynamic>>? _plugins;
  String? _error;
  bool _loading = false;
  final _sourceController = TextEditingController();
  String? _installError;
  bool _installing = false;

  @override
  void initState() {
    super.initState();
    _loadPlugins();
  }

  @override
  void dispose() {
    _sourceController.dispose();
    super.dispose();
  }

  WebSocketService? _ws(BuildContext context) {
    final appState = context.read<AppState>();
    final workerId = appState.defaultWorkerId;
    if (workerId == null) return null;
    return appState.wsForWorker(workerId);
  }

  Future<void> _loadPlugins() async {
    if (!mounted) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final ws = _ws(context);
      if (ws == null) throw Exception('No worker connected');
      final plugins = await ws.fetchToolPlugins(widget.toolName);
      if (!mounted) return;
      setState(() {
        _plugins = plugins;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString().replaceFirst('Exception: ', '');
        _loading = false;
      });
    }
  }

  Future<void> _toggleEnabled(String name, bool enabled) async {
    try {
      final ws = _ws(context);
      if (ws == null) return;
      await ws.setToolPluginEnabled(widget.toolName, name, enabled);
      await _loadPlugins();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(e.toString().replaceFirst('Exception: ', ''))),
      );
    }
  }

  Future<void> _uninstall(String name) async {
    try {
      final ws = _ws(context);
      if (ws == null) return;
      await ws.uninstallToolPlugin(widget.toolName, name);
      await _loadPlugins();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(e.toString().replaceFirst('Exception: ', ''))),
      );
    }
  }

  Future<void> _install() async {
    final source = _sourceController.text.trim();
    if (source.isEmpty) return;
    setState(() {
      _installing = true;
      _installError = null;
    });
    try {
      final ws = _ws(context);
      if (ws == null) throw Exception('No worker connected');
      await ws.installToolPlugin(widget.toolName, source);
      if (!mounted) return;
      _sourceController.clear();
      await _loadPlugins();
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _installError = e.toString().replaceFirst('Exception: ', '');
        _installing = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        _InstallBar(
          controller: _sourceController,
          installing: _installing,
          error: _installError,
          onInstall: _install,
        ),
        const Divider(height: 1, thickness: 1),
        Expanded(child: _buildList(context)),
      ],
    );
  }

  Widget _buildList(BuildContext context) {
    if (_loading) {
      return const Center(child: CircularProgressIndicator(strokeWidth: 2));
    }
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(
              _error!,
              style: TextStyle(
                color: context.appColors.errorText,
                fontSize: 13,
              ),
            ),
            const SizedBox(height: 12),
            OutlinedButton(onPressed: _loadPlugins, child: const Text('Retry')),
          ],
        ),
      );
    }
    final plugins = _plugins ?? [];
    if (plugins.isEmpty) {
      return Center(
        child: Text(
          'No plugins installed.',
          style: TextStyle(color: context.appColors.textMuted, fontSize: 13),
        ),
      );
    }
    return ListView.separated(
      padding: const EdgeInsets.symmetric(vertical: 8),
      itemCount: plugins.length,
      separatorBuilder: (_, _) =>
          Divider(height: 1, color: context.appColors.divider),
      itemBuilder: (context, index) {
        final plugin = plugins[index];
        return _PluginTile(
          plugin: plugin,
          onToggleEnabled: (enabled) =>
              _toggleEnabled(plugin['name'] as String, enabled),
          onUninstall: () => _uninstall(plugin['name'] as String),
        );
      },
    );
  }
}

// ---------------------------------------------------------------------------
// Install bar
// ---------------------------------------------------------------------------

class _InstallBar extends StatelessWidget {
  final TextEditingController controller;
  final bool installing;
  final String? error;
  final VoidCallback onInstall;

  const _InstallBar({
    required this.controller,
    required this.installing,
    required this.error,
    required this.onInstall,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 6),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: controller,
                  style: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 13,
                  ),
                  decoration: InputDecoration(
                    hintText: 'Plugin path or URL…',
                    hintStyle: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 13,
                    ),
                    isDense: true,
                    contentPadding: const EdgeInsets.symmetric(
                      horizontal: 10,
                      vertical: 8,
                    ),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(6),
                      borderSide: BorderSide(color: context.appColors.divider),
                    ),
                    enabledBorder: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(6),
                      borderSide: BorderSide(color: context.appColors.divider),
                    ),
                  ),
                  onSubmitted: (_) => onInstall(),
                ),
              ),
              const SizedBox(width: 8),
              installing
                  ? const SizedBox(
                      width: 20,
                      height: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : SizedBox(
                      height: 34,
                      child: ElevatedButton.icon(
                        icon: const Icon(Icons.add, size: 14),
                        label: const Text(
                          'Install',
                          style: TextStyle(fontSize: 12),
                        ),
                        style: ElevatedButton.styleFrom(
                          padding: const EdgeInsets.symmetric(horizontal: 12),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(6),
                          ),
                        ),
                        onPressed: onInstall,
                      ),
                    ),
            ],
          ),
          if (error != null) ...[
            const SizedBox(height: 4),
            Text(
              error!,
              style: TextStyle(
                color: context.appColors.errorText,
                fontSize: 11,
              ),
            ),
          ],
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Plugin tile
// ---------------------------------------------------------------------------

class _PluginTile extends StatelessWidget {
  final Map<String, dynamic> plugin;
  final ValueChanged<bool> onToggleEnabled;
  final VoidCallback onUninstall;

  const _PluginTile({
    required this.plugin,
    required this.onToggleEnabled,
    required this.onUninstall,
  });

  @override
  Widget build(BuildContext context) {
    final name = plugin['name'] as String? ?? '';
    final commands =
        (plugin['commands'] as List<dynamic>?)
            ?.map((c) => c as Map<String, dynamic>)
            .toList() ??
        [];
    final enabled = plugin['enabled'] as bool? ?? true;

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(
                      Icons.extension,
                      size: 14,
                      color: context.appColors.accent,
                    ),
                    const SizedBox(width: 6),
                    Text(
                      name,
                      style: TextStyle(
                        color: context.appColors.textPrimary,
                        fontSize: 13,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                    if (!enabled) ...[
                      const SizedBox(width: 6),
                      Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 5,
                          vertical: 1,
                        ),
                        decoration: BoxDecoration(
                          color: context.appColors.divider,
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(
                          'disabled',
                          style: TextStyle(
                            color: context.appColors.textMuted,
                            fontSize: 10,
                          ),
                        ),
                      ),
                    ],
                  ],
                ),
                if (commands.isNotEmpty) ...[
                  const SizedBox(height: 4),
                  Wrap(
                    spacing: 4,
                    runSpacing: 2,
                    children: commands.map((cmd) {
                      final cmdName = cmd['name'] as String? ?? '';
                      return Container(
                        padding: const EdgeInsets.symmetric(
                          horizontal: 5,
                          vertical: 1,
                        ),
                        decoration: BoxDecoration(
                          color: context.appColors.accent.withAlpha(30),
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(
                          '/$cmdName',
                          style: TextStyle(
                            color: context.appColors.accent,
                            fontSize: 11,
                            fontFamily: 'monospace',
                          ),
                        ),
                      );
                    }).toList(),
                  ),
                ],
              ],
            ),
          ),
          Switch(
            value: enabled,
            onChanged: onToggleEnabled,
            materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
          ),
          SizedBox(
            width: 26,
            height: 26,
            child: IconButton(
              padding: EdgeInsets.zero,
              icon: Icon(
                Icons.delete_outline,
                color: context.appColors.textMuted,
                size: 14,
              ),
              tooltip: 'Uninstall',
              onPressed: () => _confirmUninstall(context),
            ),
          ),
        ],
      ),
    );
  }

  void _confirmUninstall(BuildContext context) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        title: const Text('Uninstall plugin'),
        content: Text('Remove "${plugin['name']}"? This cannot be undone.'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Uninstall'),
          ),
        ],
      ),
    );
    if (confirmed == true) onUninstall();
  }
}
