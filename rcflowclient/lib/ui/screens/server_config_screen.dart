import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../models/server_config.dart';
import '../../services/websocket_service.dart';
import '../../theme.dart';
import '../widgets/custom_title_bar.dart';

void showServerConfigScreen(
  BuildContext context, {
  required WebSocketService ws,
  required String workerName,
}) {
  Navigator.of(context).push(
    MaterialPageRoute(
      builder: (_) => _ServerConfigPage(ws: ws, workerName: workerName),
    ),
  );
}

// ---------------------------------------------------------------------------
// Full-screen page
// ---------------------------------------------------------------------------

class _ServerConfigPage extends StatelessWidget {
  final WebSocketService ws;
  final String workerName;

  _ServerConfigPage({required this.ws, required this.workerName});

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
              icon: Icon(Icons.arrow_back, color: context.appColors.textPrimary),
              onPressed: () => Navigator.of(context).pop(),
            ),
            title: Text(
              '$workerName Settings',
              style: TextStyle(color: context.appColors.textPrimary, fontSize: 18),
            ),
          ),
          Expanded(
            child: ServerConfigContent(
              ws: ws,
              workerName: workerName,
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

class ServerConfigContent extends StatefulWidget {
  final WebSocketService ws;
  final String workerName;
  final ScrollController? scrollController;

  const ServerConfigContent({
    super.key,
    required this.ws,
    required this.workerName,
    this.scrollController,
  });

  @override
  State<ServerConfigContent> createState() => ServerConfigContentState();
}

class ServerConfigContentState extends State<ServerConfigContent> {
  List<ConfigOption>? _options;
  bool _loading = true;
  String? _error;
  bool _saving = false;
  String? _saveMessage;

  String _selectedSection = 'Tools';

  final Map<String, dynamic> _editedValues = {};
  final Map<String, TextEditingController> _textControllers = {};

  Map<String, dynamic>? _tools;
  bool _toolsLoading = false;
  String? _toolsError;
  bool _toolsUpdating = false;
  final Set<String> _toolsUpdatingIndividual = {};
  final Set<String> _toolsInstalling = {};
  final Set<String> _toolsUninstalling = {};
  final Set<String> _toolsSwitching = {};
  // Progress tracking for install/update operations (tool_name → progress data)
  final Map<String, Map<String, dynamic>> _toolProgress = {};

  final Map<String, List<Map<String, dynamic>>?> _toolSettings = {};
  final Map<String, bool> _toolSettingsLoading = {};
  final Map<String, String?> _toolSettingsError = {};
  final Map<String, Map<String, dynamic>> _toolSettingsEdited = {};
  final Map<String, bool> _toolSettingsSaving = {};
  final Set<String> _toolSettingsExpanded = {};
  final Map<String, Map<String, TextEditingController>>
      _toolSettingsControllers = {};

  // Codex ChatGPT login state
  bool? _codexLoggedIn;
  bool _codexLoggingIn = false;
  String? _codexDeviceUrl;
  String? _codexDeviceCode;
  String? _codexAuthUrl; // Browser OAuth URL (non-device-code flow)
  String? _codexLoginError;

  @override
  void initState() {
    super.initState();
    _loadConfig();
  }

  @override
  void dispose() {
    for (final c in _textControllers.values) {
      c.dispose();
    }
    for (final controllers in _toolSettingsControllers.values) {
      for (final c in controllers.values) {
        c.dispose();
      }
    }
    super.dispose();
  }

  Future<void> _loadConfig() async {
    setState(() {
      _loading = true;
      _error = null;
      _toolsLoading = true;
      _toolsError = null;
    });
    final results = await Future.wait([
      widget.ws.fetchConfig().then<Object?>((v) => v).catchError((e) => e),
      widget.ws.fetchToolStatus().then<Object?>((v) => v).catchError((e) => e),
    ]);

    if (!mounted) return;

    final configResult = results[0];
    final toolsResult = results[1];

    if (configResult is List<Map<String, dynamic>>) {
      final options =
          configResult.map((o) => ConfigOption.fromJson(o)).toList();
      setState(() {
        _options = options;
        _loading = false;
        _editedValues.clear();
        _initControllers(options);
      });
    } else {
      setState(() {
        _error = configResult.toString();
        _loading = false;
      });
    }

    if (toolsResult is Map<String, dynamic>) {
      final tools = toolsResult['tools'] as Map<String, dynamic>?;
      setState(() {
        _tools = tools;
        _toolsLoading = false;
      });
    } else {
      setState(() {
        _toolsError = toolsResult.toString();
        _toolsLoading = false;
      });
    }
  }

  Future<void> _refreshToolStatus() async {
    setState(() {
      _toolsLoading = true;
      _toolsError = null;
    });
    try {
      final data = await widget.ws.fetchToolStatus();
      if (!mounted) return;
      setState(() {
        _tools = data['tools'] as Map<String, dynamic>?;
        _toolsLoading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _toolsError = e.toString();
        _toolsLoading = false;
      });
    }
  }

  Future<void> _updateTools() async {
    setState(() => _toolsUpdating = true);
    try {
      final data = await widget.ws.triggerToolUpdate();
      if (!mounted) return;
      setState(() {
        _tools = data['tools'] as Map<String, dynamic>?;
        _toolsUpdating = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _toolsError = e.toString();
        _toolsUpdating = false;
      });
    }
  }

  Future<void> _updateSingleTool(String toolName) async {
    setState(() {
      _toolsUpdatingIndividual.add(toolName);
      _toolProgress[toolName] = {'step': 'starting', 'message': 'Starting...'};
    });
    try {
      final data = await widget.ws.triggerSingleToolUpdate(
        toolName,
        onProgress: (event) {
          if (!mounted) return;
          setState(() => _toolProgress[toolName] = event);
        },
      );
      if (!mounted) return;
      final updatedTool = data['tool'] as Map<String, dynamic>;
      setState(() {
        _tools![toolName] = updatedTool;
        _toolsUpdatingIndividual.remove(toolName);
        _toolProgress.remove(toolName);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _toolsError = e.toString();
        _toolsUpdatingIndividual.remove(toolName);
        _toolProgress.remove(toolName);
      });
    }
  }

  Future<void> _installManagedTool(String toolName) async {
    setState(() {
      _toolsInstalling.add(toolName);
      _toolProgress[toolName] = {'step': 'starting', 'message': 'Starting...'};
    });
    try {
      final data = await widget.ws.installManagedTool(
        toolName,
        onProgress: (event) {
          if (!mounted) return;
          setState(() => _toolProgress[toolName] = event);
        },
      );
      if (!mounted) return;
      final updatedTool = data['tool'] as Map<String, dynamic>;
      setState(() {
        _tools![toolName] = updatedTool;
        _toolsInstalling.remove(toolName);
        _toolProgress.remove(toolName);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _toolsError = e.toString();
        _toolsInstalling.remove(toolName);
        _toolProgress.remove(toolName);
      });
    }
  }

  Future<void> _confirmUninstall(String toolName) async {
    final displayName = _toolDisplayNames[toolName] ?? toolName;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        title: Text('Uninstall $displayName?',
            style: TextStyle(color: context.appColors.textPrimary, fontSize: 16)),
        content: Text('The managed binary will be removed. Settings will be preserved.',
            style: TextStyle(color: context.appColors.textSecondary, fontSize: 13)),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: TextButton.styleFrom(foregroundColor: context.appColors.errorText),
            child: const Text('Uninstall'),
          ),
        ],
      ),
    );
    if (confirmed == true) {
      _uninstallManagedTool(toolName);
    }
  }

  Future<void> _uninstallManagedTool(String toolName) async {
    setState(() => _toolsUninstalling.add(toolName));
    try {
      final data = await widget.ws.uninstallManagedTool(toolName);
      if (!mounted) return;
      final updatedTool = data['tool'] as Map<String, dynamic>;
      setState(() {
        _tools![toolName] = updatedTool;
        _toolsUninstalling.remove(toolName);
      });
      if (_toolSettingsExpanded.contains(toolName)) {
        _loadToolSettings(toolName);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _toolsError = e.toString();
        _toolsUninstalling.remove(toolName);
      });
    }
  }

  Future<void> _switchToolSource(String toolName, bool useManaged) async {
    setState(() => _toolsSwitching.add(toolName));
    try {
      final data = await widget.ws.switchToolSource(toolName, useManaged);
      if (!mounted) return;
      final updatedTool = data['tool'] as Map<String, dynamic>;
      setState(() {
        _tools![toolName] = updatedTool;
        _toolsSwitching.remove(toolName);
      });
      // Reload settings if the panel is expanded (schema changes with source)
      if (_toolSettingsExpanded.contains(toolName)) {
        _loadToolSettings(toolName);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _toolsError = e.toString();
        _toolsSwitching.remove(toolName);
      });
    }
  }

  Future<void> _loadToolSettings(String toolName) async {
    setState(() {
      _toolSettingsLoading[toolName] = true;
      _toolSettingsError[toolName] = null;
    });
    try {
      final data = await widget.ws.fetchToolSettings(toolName);
      if (!mounted) return;
      final fields = (data['fields'] as List<dynamic>)
          .cast<Map<String, dynamic>>();
      setState(() {
        _toolSettings[toolName] = fields;
        _toolSettingsLoading[toolName] = false;
        _toolSettingsEdited.remove(toolName);
        _initToolSettingsControllers(toolName, fields);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _toolSettingsError[toolName] = e.toString();
        _toolSettingsLoading[toolName] = false;
      });
    }
  }

  Future<void> _saveToolSettings(String toolName) async {
    final edits = _toolSettingsEdited[toolName];
    if (edits == null || edits.isEmpty) return;
    setState(() => _toolSettingsSaving[toolName] = true);
    try {
      final data = await widget.ws.updateToolSettings(toolName, edits);
      if (!mounted) return;
      final fields = (data['fields'] as List<dynamic>)
          .cast<Map<String, dynamic>>();
      setState(() {
        _toolSettings[toolName] = fields;
        _toolSettingsEdited.remove(toolName);
        _toolSettingsSaving[toolName] = false;
        _initToolSettingsControllers(toolName, fields);
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _toolSettingsError[toolName] = e.toString();
        _toolSettingsSaving[toolName] = false;
      });
    }
  }

  Future<void> _checkCodexLoginStatus() async {
    try {
      final data = await widget.ws.codexLoginStatus();
      if (!mounted) return;
      setState(() {
        _codexLoggedIn = data['logged_in'] as bool? ?? false;
        _codexLoginError = null;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _codexLoggedIn = null;
        _codexLoginError = e.toString();
      });
    }
  }

  Future<void> _startCodexLogin({bool deviceCode = false}) async {
    setState(() {
      _codexLoggingIn = true;
      _codexDeviceUrl = null;
      _codexDeviceCode = null;
      _codexAuthUrl = null;
      _codexLoginError = null;
    });
    try {
      await widget.ws.codexLogin(
        deviceCode: deviceCode,
        onProgress: (event) {
          if (!mounted) return;
          final step = event['step'] as String?;
          setState(() {
            if (step == 'device_code') {
              _codexDeviceUrl = event['url'] as String?;
              _codexDeviceCode = event['code'] as String?;
            } else if (step == 'auth_url') {
              _codexAuthUrl = event['url'] as String?;
              // Auto-open the URL in the browser
              final url = event['url'] as String?;
              if (url != null) {
                launchUrl(Uri.parse(url), mode: LaunchMode.externalApplication);
              }
            } else if (step == 'complete') {
              _codexLoggingIn = false;
              _codexLoggedIn = true;
              _codexDeviceUrl = null;
              _codexDeviceCode = null;
              _codexAuthUrl = null;
            }
          });
        },
      );
      if (!mounted) return;
      setState(() {
        _codexLoggingIn = false;
        _codexLoggedIn = true;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _codexLoggingIn = false;
        _codexLoginError = e.toString();
        _codexDeviceUrl = null;
        _codexDeviceCode = null;
        _codexAuthUrl = null;
      });
    }
  }

  void _initToolSettingsControllers(
      String toolName, List<Map<String, dynamic>> fields) {
    final existing = _toolSettingsControllers[toolName];
    if (existing != null) {
      for (final c in existing.values) {
        c.dispose();
      }
    }
    final controllers = <String, TextEditingController>{};
    for (final field in fields) {
      final type = field['type'] as String;
      final key = field['key'] as String;
      if (type == 'string') {
        controllers[key] =
            TextEditingController(text: field['value']?.toString() ?? '');
      } else if (type == 'string_list') {
        final list = field['value'];
        final text = list is List ? list.join('\n') : '';
        controllers[key] = TextEditingController(text: text);
      } else if (type == 'secret') {
        // Secret controllers start empty — the masked value is shown
        // separately and actual input only happens via the Change flow.
        controllers[key] = TextEditingController();
      }
    }
    _toolSettingsControllers[toolName] = controllers;
  }

  void _initControllers(List<ConfigOption> options) {
    for (final c in _textControllers.values) {
      c.dispose();
    }
    _textControllers.clear();
    for (final opt in options) {
      if (opt.type == 'string' || opt.type == 'secret') {
        _textControllers[opt.key] =
            TextEditingController(text: opt.value?.toString() ?? '');
      }
    }
  }

  bool get _hasChanges => _editedValues.isNotEmpty;

  void _onValueChanged(String key, dynamic value, dynamic originalValue) {
    setState(() {
      if (value == originalValue) {
        _editedValues.remove(key);
      } else {
        _editedValues[key] = value;
      }
      _saveMessage = null;
    });
  }

  Future<void> _save() async {
    if (!_hasChanges || _saving) return;
    setState(() {
      _saving = true;
      _saveMessage = null;
    });
    try {
      final rawOptions = await widget.ws.updateConfig(_editedValues);
      final options =
          rawOptions.map((o) => ConfigOption.fromJson(o)).toList();
      if (!mounted) return;

      final hadRestartRequired = _editedValues.keys.any((key) {
        final opt = _options?.firstWhere((o) => o.key == key,
            orElse: () => _options!.first);
        return opt?.restartRequired ?? false;
      });

      setState(() {
        _options = options;
        _editedValues.clear();
        _saving = false;
        _initControllers(options);
        _saveMessage = hadRestartRequired
            ? 'Saved. Some changes may require a server restart.'
            : 'Saved successfully.';
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _saving = false;
        _saveMessage = 'Error: $e';
      });
    }
  }

  /// Groups that match tool names are managed via the tool settings panel,
  /// so they should not appear as standalone sidebar sections.
  Set<String> get _toolGroupNames {
    final names = <String>{};
    for (final entry in _toolDisplayNames.entries) {
      names.add(entry.key.toLowerCase());
      names.add(entry.value.toLowerCase());
    }
    if (_tools != null) {
      for (final key in _tools!.keys) {
        names.add(key.toLowerCase());
      }
    }
    return names;
  }

  List<String> get _sectionNames {
    final sections = <String>['Tools'];
    if (_options != null) {
      final excludes = _toolGroupNames;
      final seen = <String>{};
      for (final opt in _options!) {
        if (seen.add(opt.group) &&
            !excludes.contains(opt.group.toLowerCase()) &&
            _options!.any((o) => o.group == opt.group && _isVisible(o))) {
          sections.add(opt.group);
        }
      }
    }
    return sections;
  }

  String get _effectiveSection {
    final sections = _sectionNames;
    return sections.contains(_selectedSection)
        ? _selectedSection
        : sections.first;
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return Center(
        child: CircularProgressIndicator(color: context.appColors.accent),
      );
    }
    if (_error != null) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.error_outline, color: context.appColors.errorText, size: 40),
            SizedBox(height: 12),
            Text(_error!,
                style: TextStyle(color: context.appColors.errorText, fontSize: 13)),
            SizedBox(height: 16),
            TextButton(
              onPressed: _loadConfig,
              child: Text('Retry',
                  style: TextStyle(color: context.appColors.accentLight)),
            ),
          ],
        ),
      );
    }

    final section = _effectiveSection;

    return Row(
      children: [
        // Sidebar
        Container(
          width: 180,
          decoration: BoxDecoration(
            border: Border(right: BorderSide(color: context.appColors.divider, width: 1)),
          ),
          child: ListView(
            padding: const EdgeInsets.symmetric(vertical: 8),
            children: [
              for (final s in _sectionNames)
                _ConfigSidebarItem(
                  label: s,
                  icon: s == 'Tools' ? Icons.build_outlined : Icons.tune,
                  selected: section == s,
                  hasModified: s != 'Tools' &&
                      _options != null &&
                      _options!
                          .where((o) => o.group == s)
                          .any((o) => _editedValues.containsKey(o.key)),
                  onTap: () => setState(() => _selectedSection = s),
                ),
            ],
          ),
        ),
        // Content
        Expanded(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (_hasChanges || _saveMessage != null) ...[
                  Row(
                    mainAxisAlignment: MainAxisAlignment.end,
                    children: [
                      if (_saveMessage != null)
                        Expanded(
                          child: Text(
                            _saveMessage!,
                            style: TextStyle(
                              color: _saveMessage!.startsWith('Error')
                                  ? context.appColors.errorText
                                  : context.appColors.successText,
                              fontSize: 12,
                            ),
                          ),
                        ),
                      if (_hasChanges)
                        FilledButton(
                          onPressed: _saving ? null : _save,
                          style: FilledButton.styleFrom(
                            backgroundColor: context.appColors.accent,
                            foregroundColor: Colors.white,
                            padding: const EdgeInsets.symmetric(
                                horizontal: 16, vertical: 8),
                            shape: RoundedRectangleBorder(
                                borderRadius: BorderRadius.circular(10)),
                          ),
                          child: _saving
                              ? const SizedBox(
                                  width: 16,
                                  height: 16,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                    color: Colors.white,
                                  ),
                                )
                              : const Text('Save'),
                        ),
                    ],
                  ),
                  const SizedBox(height: 16),
                ],
                Expanded(
                  child: SingleChildScrollView(
                    controller: widget.scrollController,
                    child: _buildSectionContent(section),
                  ),
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildSectionContent(String section) {
    if (section == 'Tools') {
      return _buildToolsSection();
    }

    final groupOpts =
        _options!.where((o) => o.group == section && _isVisible(o)).toList();
    final hasModified =
        groupOpts.any((o) => _editedValues.containsKey(o.key));

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SectionHeader(
          title: section,
          icon: Icons.tune,
          hasModified: hasModified,
        ),
        for (final opt in groupOpts) ...[
          _buildOptionWidget(opt),
          const SizedBox(height: 8),
        ],
      ],
    );
  }

  Map<String, dynamic> _effectiveValues() {
    final values = <String, dynamic>{};
    for (final opt in _options!) {
      values[opt.key] = _editedValues.containsKey(opt.key)
          ? _editedValues[opt.key]
          : opt.value;
    }
    return values;
  }

  bool _isVisible(ConfigOption opt) {
    if (opt.visibleWhen == null) return true;
    return opt.visibleWhen!.evaluate(_effectiveValues());
  }

  bool get _hasUpdatesAvailable {
    if (_tools == null) return false;
    return _tools!.values.any((t) {
      final tool = t as Map<String, dynamic>;
      return tool['update_available'] == true;
    });
  }

  static const _toolDisplayNames = <String, String>{
    'claude_code': 'Claude Code',
    'codex': 'Codex',
  };

  Widget _buildToolsSection() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SectionHeader(title: 'Tools', icon: Icons.build_outlined),
        if (_toolsLoading && _tools == null)
          Padding(
            padding: EdgeInsets.symmetric(vertical: 12),
            child: Center(
              child: SizedBox(
                width: 20,
                height: 20,
                child:
                    CircularProgressIndicator(strokeWidth: 2, color: context.appColors.accent),
              ),
            ),
          )
        else if (_toolsError != null && _tools == null)
          Padding(
            padding: EdgeInsets.symmetric(vertical: 8),
            child: Text(_toolsError!,
                style: TextStyle(color: context.appColors.errorText, fontSize: 12)),
          )
        else if (_tools != null) ...[
          for (final entry in _tools!.entries)
            _buildToolRow(entry.key, entry.value as Map<String, dynamic>),
          const SizedBox(height: 8),
          Row(
            children: [
              _ToolActionButton(
                label: 'Check for Updates',
                loading: _toolsLoading,
                onPressed: (_toolsLoading || _toolsUpdating)
                    ? null
                    : _refreshToolStatus,
              ),
              if (_hasUpdatesAvailable) ...[
                const SizedBox(width: 8),
                _ToolActionButton(
                  label: 'Update All',
                  loading: _toolsUpdating,
                  accent: true,
                  onPressed: (_toolsUpdating ||
                          _toolsUpdatingIndividual.isNotEmpty)
                      ? null
                      : _updateTools,
                ),
              ],
            ],
          ),
          if (_toolsError != null)
            Padding(
              padding: EdgeInsets.only(top: 6),
              child: Text(_toolsError!,
                  style: TextStyle(color: context.appColors.errorText, fontSize: 11)),
            ),
        ],
      ],
    );
  }

  Widget _buildToolRow(String key, Map<String, dynamic> tool) {
    final displayName = _toolDisplayNames[key] ?? key;
    final installed = tool['installed'] as bool? ?? false;
    final managed = tool['managed'] as bool? ?? false;
    final currentVersion = tool['current_version'] as String?;
    final latestVersion = tool['latest_version'] as String?;
    final updateAvailable = tool['update_available'] as bool? ?? false;
    final error = tool['error'] as String?;
    final managedPath = tool['managed_path'] as String?;
    final externalPath = tool['external_path'] as String?;
    final canSwitch = managedPath != null && externalPath != null;
    final isUpdating = _toolsUpdatingIndividual.contains(key);
    final isInstalling = _toolsInstalling.contains(key);
    final isUninstalling = _toolsUninstalling.contains(key);
    final isSwitching = _toolsSwitching.contains(key);
    final settingsExpanded = _toolSettingsExpanded.contains(key);
    final progress = _toolProgress[key];

    return Padding(
      padding: EdgeInsets.only(bottom: 8),
      child: Container(
        padding: EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: context.appColors.bgElevated,
          borderRadius: BorderRadius.circular(10),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Top row: name + badge + gear + update button
            Row(
              children: [
                Text(displayName,
                    style: TextStyle(
                        color: context.appColors.textPrimary,
                        fontSize: 13,
                        fontWeight: FontWeight.w600)),
                const SizedBox(width: 8),
                if (canSwitch)
                  _SourceToggle(
                    managed: managed,
                    switching: isSwitching,
                    onSwitch: (useManaged) =>
                        _switchToolSource(key, useManaged),
                  )
                else if (managed)
                  _SourceBadge(label: 'managed', accent: true)
                else if (installed)
                  _SourceBadge(label: 'external', accent: false),
                const Spacer(),
                SizedBox(
                  width: 28,
                  height: 28,
                  child: IconButton(
                    padding: EdgeInsets.zero,
                    iconSize: 16,
                    icon: Icon(
                      settingsExpanded
                          ? Icons.settings
                          : Icons.settings_outlined,
                      color: settingsExpanded ? context.appColors.accentLight : context.appColors.textMuted,
                    ),
                    onPressed: () {
                      setState(() {
                        if (settingsExpanded) {
                          _toolSettingsExpanded.remove(key);
                        } else {
                          _toolSettingsExpanded.add(key);
                          if (_toolSettings[key] == null) {
                            _loadToolSettings(key);
                          }
                        }
                      });
                    },
                  ),
                ),
                if (updateAvailable && installed) ...[
                  const SizedBox(width: 4),
                  _ToolActionButton(
                    label: 'Update',
                    loading: isUpdating,
                    accent: true,
                    onPressed:
                        isUpdating ? null : () => _updateSingleTool(key),
                  ),
                ],
                // Show "Install" when no managed binary exists
                if (managedPath == null && !canSwitch) ...[
                  const SizedBox(width: 4),
                  _ToolActionButton(
                    label: installed ? 'Install managed' : 'Install',
                    loading: isInstalling,
                    accent: !installed,
                    onPressed: isInstalling
                        ? null
                        : () => _installManagedTool(key),
                  ),
                ],
                // Show "Uninstall" when managed install exists
                if (managed && managedPath != null) ...[
                  const SizedBox(width: 4),
                  _ToolActionButton(
                    label: 'Uninstall',
                    loading: isUninstalling,
                    accent: false,
                    onPressed: isUninstalling
                        ? null
                        : () => _confirmUninstall(key),
                  ),
                ],
              ],
            ),
            SizedBox(height: 6),
            // Version info
            if (!installed)
              Text('Not installed',
                  style: TextStyle(color: context.appColors.textMuted, fontSize: 12))
            else ...[
              Row(
                children: [
                  Text('v$currentVersion',
                      style:
                          TextStyle(color: context.appColors.textSecondary, fontSize: 12)),
                  if (updateAvailable && latestVersion != null) ...[
                    SizedBox(width: 6),
                    Icon(Icons.arrow_forward_rounded,
                        color: context.appColors.textMuted, size: 12),
                    SizedBox(width: 6),
                    Text('v$latestVersion',
                        style:
                            TextStyle(color: context.appColors.accentLight, fontSize: 12)),
                  ],
                ],
              ),
            ],
            if (error != null)
              Padding(
                padding: EdgeInsets.only(top: 4),
                child: Text(error,
                    style: TextStyle(color: context.appColors.errorText, fontSize: 11)),
              ),
            // Progress bar for install/update
            if (progress != null)
              Padding(
                padding: const EdgeInsets.only(top: 6),
                child: _ToolProgressBar(progress: progress),
              ),
            if (settingsExpanded) _buildToolSettingsPanel(key),
          ],
        ),
      ),
    );
  }

  bool _isToolFieldVisible(String toolName, Map<String, dynamic> field) {
    final visibleWhen = field['visible_when'] as Map<String, dynamic>?;
    if (visibleWhen == null) return true;
    final depKey = visibleWhen['key'] as String;
    final depValue = visibleWhen['value'] as String;
    // Check edited values first, then saved values from schema.
    final edits = _toolSettingsEdited[toolName];
    if (edits != null && edits.containsKey(depKey)) {
      return edits[depKey]?.toString() == depValue;
    }
    // Fall back to the saved value from the fields list.
    final fields = _toolSettings[toolName];
    if (fields != null) {
      for (final f in fields) {
        if (f['key'] == depKey) {
          return f['value']?.toString() == depValue;
        }
      }
    }
    return false;
  }

  /// Get the current effective value for a tool setting field.
  String _getToolFieldValue(String toolName, String key) {
    final edits = _toolSettingsEdited[toolName];
    if (edits != null && edits.containsKey(key)) {
      return edits[key]?.toString() ?? '';
    }
    final fields = _toolSettings[toolName];
    if (fields != null) {
      for (final f in fields) {
        if (f['key'] == key) return f['value']?.toString() ?? '';
      }
    }
    return '';
  }

  Widget _buildCodexLoginSection() {
    // Fetch status on first render
    if (_codexLoggedIn == null && !_codexLoggingIn && _codexLoginError == null) {
      Future.microtask(() => _checkCodexLoginStatus());
    }

    return Container(
      padding: EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: context.appColors.bgSurface,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: context.appColors.bgOverlay),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'ChatGPT Authentication',
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 12,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 6),
          // Status indicator
          if (_codexLoggedIn == null && _codexLoginError == null)
            Row(
              children: [
                SizedBox(
                  width: 12,
                  height: 12,
                  child: CircularProgressIndicator(
                    strokeWidth: 1.5,
                    color: context.appColors.textMuted,
                  ),
                ),
                SizedBox(width: 8),
                Text('Checking status...',
                    style: TextStyle(color: context.appColors.textMuted, fontSize: 11)),
              ],
            )
          else if (_codexLoggedIn == true && !_codexLoggingIn)
            Row(
              children: [
                Icon(Icons.check_circle, color: context.appColors.successText, size: 14),
                SizedBox(width: 6),
                Text('Logged in',
                    style: TextStyle(color: context.appColors.successText, fontSize: 11)),
                const Spacer(),
                _ToolActionButton(
                  label: 'Re-login',
                  loading: false,
                  accent: false,
                  onPressed: () => _startCodexLogin(),
                ),
              ],
            )
          else if (!_codexLoggingIn) ...[
            Row(
              children: [
                Icon(Icons.cancel_outlined,
                    color: context.appColors.textMuted, size: 14),
                SizedBox(width: 6),
                Text('Not logged in',
                    style: TextStyle(color: context.appColors.textMuted, fontSize: 11)),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: _ToolActionButton(
                    label: 'Login with ChatGPT',
                    loading: false,
                    accent: true,
                    onPressed: () => _startCodexLogin(),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: _ToolActionButton(
                    label: 'Use device code',
                    loading: false,
                    accent: false,
                    onPressed: () => _startCodexLogin(deviceCode: true),
                  ),
                ),
              ],
            ),
          ],
          // Active login flow display
          if (_codexLoggingIn) ...[
            // Browser OAuth flow — URL opened automatically
            if (_codexAuthUrl != null) ...[
              SizedBox(height: 8),
              Container(
                width: double.infinity,
                padding: EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: context.appColors.bgBase,
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Column(
                  children: [
                    Text(
                      'Complete sign-in in your browser.',
                      style: TextStyle(color: context.appColors.textSecondary, fontSize: 11),
                    ),
                    SizedBox(height: 6),
                    Text(
                      'If the browser did not open, click the link:',
                      style: TextStyle(color: context.appColors.textMuted, fontSize: 10),
                    ),
                    const SizedBox(height: 4),
                    GestureDetector(
                      onTap: () => launchUrl(
                        Uri.parse(_codexAuthUrl!),
                        mode: LaunchMode.externalApplication,
                      ),
                      child: Text(
                        _codexAuthUrl!.length > 80
                            ? '${_codexAuthUrl!.substring(0, 80)}...'
                            : _codexAuthUrl!,
                        style: TextStyle(
                          color: context.appColors.accentLight,
                          fontSize: 10,
                          decoration: TextDecoration.underline,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              SizedBox(height: 6),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  SizedBox(
                    width: 12,
                    height: 12,
                    child: CircularProgressIndicator(
                      strokeWidth: 1.5, color: context.appColors.accent),
                  ),
                  SizedBox(width: 8),
                  Text('Waiting for browser authentication...',
                      style: TextStyle(color: context.appColors.textMuted, fontSize: 11)),
                ],
              ),
            ]
            // Device code flow
            else if (_codexDeviceCode != null &&
                _codexDeviceUrl != null) ...[
              SizedBox(height: 8),
              Container(
                width: double.infinity,
                padding: EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: context.appColors.bgBase,
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Column(
                  children: [
                    Text(
                      'Enter this code in your browser:',
                      style: TextStyle(color: context.appColors.textSecondary, fontSize: 11),
                    ),
                    SizedBox(height: 6),
                    SelectableText(
                      _codexDeviceCode!,
                      style: TextStyle(
                        color: context.appColors.accentLight,
                        fontSize: 20,
                        fontWeight: FontWeight.bold,
                        letterSpacing: 3,
                      ),
                    ),
                    SizedBox(height: 6),
                    GestureDetector(
                      onTap: () => launchUrl(
                        Uri.parse(_codexDeviceUrl!),
                        mode: LaunchMode.externalApplication,
                      ),
                      child: Text(
                        _codexDeviceUrl!,
                        style: TextStyle(
                          color: context.appColors.accentLight,
                          fontSize: 11,
                          decoration: TextDecoration.underline,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
              SizedBox(height: 6),
              Row(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  SizedBox(
                    width: 12,
                    height: 12,
                    child: CircularProgressIndicator(
                      strokeWidth: 1.5, color: context.appColors.accent),
                  ),
                  SizedBox(width: 8),
                  Text('Waiting for browser authentication...',
                      style: TextStyle(color: context.appColors.textMuted, fontSize: 11)),
                ],
              ),
            ] else ...[
              SizedBox(height: 6),
              Row(
                children: [
                  SizedBox(
                    width: 12,
                    height: 12,
                    child: CircularProgressIndicator(
                      strokeWidth: 1.5, color: context.appColors.accent),
                  ),
                  SizedBox(width: 8),
                  Text('Starting login...',
                      style: TextStyle(color: context.appColors.textMuted, fontSize: 11)),
                ],
              ),
            ],
          ],
          if (_codexLoginError != null)
            Padding(
              padding: EdgeInsets.only(top: 4),
              child: Text(_codexLoginError!,
                  style: TextStyle(color: context.appColors.errorText, fontSize: 10)),
            ),
        ],
      ),
    );
  }

  Widget _buildToolSettingsPanel(String toolName) {
    final loading = _toolSettingsLoading[toolName] ?? false;
    final error = _toolSettingsError[toolName];
    final fields = _toolSettings[toolName];
    final saving = _toolSettingsSaving[toolName] ?? false;
    final edits = _toolSettingsEdited[toolName];
    final hasEdits = edits != null && edits.isNotEmpty;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Divider(color: context.appColors.bgOverlay, height: 16),
        if (loading && fields == null)
          Padding(
            padding: EdgeInsets.symmetric(vertical: 8),
            child: Center(
              child: SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(
                    strokeWidth: 2, color: context.appColors.accent),
              ),
            ),
          )
        else if (error != null && fields == null)
          Padding(
            padding: EdgeInsets.symmetric(vertical: 4),
            child: Text(error,
                style: TextStyle(color: context.appColors.errorText, fontSize: 11)),
          )
        else if (fields != null) ...[
          for (final field in fields) ...[
            if (_isToolFieldVisible(toolName, field))
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: _buildToolSettingField(toolName, field),
              ),
            // Show ChatGPT login section right after the provider field
            if (toolName == 'codex' &&
                field['key'] == 'provider' &&
                _getToolFieldValue(toolName, 'provider') == 'chatgpt')
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: _buildCodexLoginSection(),
              ),
          ],
          if (hasEdits || saving)
            Row(
              children: [
                _ToolActionButton(
                  label: 'Save',
                  loading: saving,
                  accent: true,
                  onPressed: saving ? null : () => _saveToolSettings(toolName),
                ),
              ],
            ),
          if (error != null)
            Padding(
              padding: EdgeInsets.only(top: 4),
              child: Text(error,
                  style: TextStyle(color: context.appColors.errorText, fontSize: 11)),
            ),
        ],
      ],
    );
  }

  Widget _buildToolSettingField(
      String toolName, Map<String, dynamic> field) {
    final key = field['key'] as String;
    final label = field['label'] as String;
    final type = field['type'] as String;
    final description = field['description'] as String? ?? '';
    final originalValue = field['value'];
    final edits = _toolSettingsEdited[toolName];
    final currentValue = edits != null && edits.containsKey(key)
        ? edits[key]
        : originalValue;

    void onChanged(dynamic value) {
      setState(() {
        final edited = _toolSettingsEdited[toolName] ?? {};
        if (value == originalValue) {
          edited.remove(key);
        } else {
          edited[key] = value;
        }
        _toolSettingsEdited[toolName] = edited;
      });
    }

    Widget input;
    switch (type) {
      case 'boolean':
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SwitchListTile(
              title: Text(label,
                  style:
                      TextStyle(color: context.appColors.textPrimary, fontSize: 13)),
              value: currentValue is bool ? currentValue : currentValue == true,
              activeTrackColor: context.appColors.accent,
              contentPadding: EdgeInsets.zero,
              dense: true,
              onChanged: (v) => onChanged(v),
            ),
            if (description.isNotEmpty)
              Text(description,
                  style: TextStyle(color: context.appColors.textMuted, fontSize: 10)),
          ],
        );
      case 'select':
        final options = (field['options'] as List<dynamic>?)
                ?.cast<Map<String, dynamic>>() ??
            [];
        final values = options.map((o) => o['value'] as String).toList();
        input = Container(
          padding: EdgeInsets.symmetric(horizontal: 10),
          decoration: BoxDecoration(
            color: context.appColors.bgSurface,
            borderRadius: BorderRadius.circular(8),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              value: values.contains(currentValue?.toString())
                  ? currentValue?.toString()
                  : null,
              isExpanded: true,
              dropdownColor: context.appColors.bgSurface,
              style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
              items: options
                  .map((o) => DropdownMenuItem(
                      value: o['value'] as String,
                      child: Text(o['label'] as String)))
                  .toList(),
              onChanged: (v) {
                if (v != null) onChanged(v);
              },
            ),
          ),
        );
      case 'secret':
        return _ToolSecretField(
          key: ValueKey(key),
          label: label,
          description: description,
          maskedValue: originalValue?.toString() ?? '',
          controller: _toolSettingsControllers[toolName]?[key],
          onChanged: onChanged,
        );
      case 'string_list':
        final controller = _toolSettingsControllers[toolName]?[key];
        input = TextField(
          controller: controller,
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
          maxLines: 3,
          minLines: 2,
          onChanged: (v) {
            final list = v
                .split('\n')
                .map((s) => s.trim())
                .where((s) => s.isNotEmpty)
                .toList();
            onChanged(list);
          },
          decoration: InputDecoration(
            hintText: 'One entry per line',
            hintStyle: TextStyle(color: context.appColors.textMuted, fontSize: 12),
            fillColor: context.appColors.bgSurface,
            filled: true,
            border: OutlineInputBorder(
              borderSide: BorderSide.none,
              borderRadius: BorderRadius.circular(8),
            ),
            contentPadding:
                EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          ),
        );
      default:
        final controller = _toolSettingsControllers[toolName]?[key];
        input = TextField(
          controller: controller,
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
          onChanged: (v) => onChanged(v),
          decoration: InputDecoration(
            hintText: label,
            hintStyle: TextStyle(color: context.appColors.textMuted, fontSize: 12),
            fillColor: context.appColors.bgSurface,
            filled: true,
            border: OutlineInputBorder(
              borderSide: BorderSide.none,
              borderRadius: BorderRadius.circular(8),
            ),
            contentPadding:
                EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          ),
        );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label,
            style: TextStyle(color: context.appColors.textSecondary, fontSize: 11)),
        SizedBox(height: 4),
        input,
        if (description.isNotEmpty)
          Padding(
            padding: EdgeInsets.only(top: 3),
            child: Text(description,
                style: TextStyle(color: context.appColors.textMuted, fontSize: 10)),
          ),
      ],
    );
  }

  Widget _buildOptionWidget(ConfigOption opt) {
    final currentValue = _editedValues.containsKey(opt.key)
        ? _editedValues[opt.key]
        : opt.value;
    final isModified = _editedValues.containsKey(opt.key);

    switch (opt.type) {
      case 'select':
        return _SelectField(
          option: opt,
          value: currentValue?.toString() ?? '',
          isModified: isModified,
          onChanged: (v) => _onValueChanged(opt.key, v, opt.value),
        );
      case 'boolean':
        return _BoolField(
          option: opt,
          value: currentValue is bool ? currentValue : currentValue == 'true',
          isModified: isModified,
          onChanged: (v) => _onValueChanged(opt.key, v, opt.value),
        );
      case 'secret':
        return _SecretField(
          option: opt,
          controller: _textControllers[opt.key]!,
          isModified: isModified,
          onChanged: (v) => _onValueChanged(opt.key, v, opt.value),
        );
      default:
        return _TextField(
          option: opt,
          controller: _textControllers[opt.key]!,
          isModified: isModified,
          onChanged: (v) => _onValueChanged(opt.key, v, opt.value),
        );
    }
  }
}

// ---------------------------------------------------------------------------
// Section header & divider (matches global settings style)
// ---------------------------------------------------------------------------

class _SectionHeader extends StatelessWidget {
  final String title;
  final IconData icon;
  final bool hasModified;

  const _SectionHeader({
    required this.title,
    required this.icon,
    this.hasModified = false,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: 16),
      child: Row(
        children: [
          Icon(icon, color: context.appColors.accentLight, size: 20),
          SizedBox(width: 8),
          Text(
            title,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 17,
              fontWeight: FontWeight.w600,
            ),
          ),
          if (hasModified)
            Padding(
              padding: EdgeInsets.only(left: 8),
              child: Text('\u2022 modified',
                  style: TextStyle(color: context.appColors.accentLight, fontSize: 10)),
            ),
        ],
      ),
    );
  }
}

class _ConfigSidebarItem extends StatelessWidget {
  final String label;
  final IconData icon;
  final bool selected;
  final bool hasModified;
  final VoidCallback onTap;

  const _ConfigSidebarItem({
    required this.label,
    required this.icon,
    required this.selected,
    this.hasModified = false,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      child: Material(
        color: selected ? context.appColors.bgElevated : Colors.transparent,
        borderRadius: BorderRadius.circular(10),
        child: InkWell(
          borderRadius: BorderRadius.circular(10),
          onTap: onTap,
          child: Padding(
            padding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            child: Row(
              children: [
                Icon(icon,
                    size: 18,
                    color: selected ? context.appColors.accentLight : context.appColors.textMuted),
                SizedBox(width: 10),
                Expanded(
                  child: Text(
                    label,
                    style: TextStyle(
                      color: selected ? context.appColors.textPrimary : context.appColors.textSecondary,
                      fontSize: 14,
                      fontWeight:
                          selected ? FontWeight.w600 : FontWeight.normal,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                if (hasModified)
                  Container(
                    width: 6,
                    height: 6,
                    decoration: BoxDecoration(
                      color: context.appColors.accentLight,
                      shape: BoxShape.circle,
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Field widgets
// ---------------------------------------------------------------------------

class _TextField extends StatelessWidget {
  final ConfigOption option;
  final TextEditingController controller;
  final bool isModified;
  final ValueChanged<String> onChanged;

  const _TextField({
    required this.option,
    required this.controller,
    required this.isModified,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return _FieldWrapper(
      option: option,
      isModified: isModified,
      child: TextField(
        controller: controller,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 14),
        onChanged: onChanged,
        decoration: InputDecoration(
          hintText: option.label,
          fillColor: context.appColors.bgElevated,
          border: OutlineInputBorder(
            borderSide: BorderSide.none,
            borderRadius: BorderRadius.circular(10),
          ),
          contentPadding:
              const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        ),
      ),
    );
  }
}

class _SecretField extends StatefulWidget {
  final ConfigOption option;
  final TextEditingController controller;
  final bool isModified;
  final ValueChanged<String> onChanged;

  const _SecretField({
    required this.option,
    required this.controller,
    required this.isModified,
    required this.onChanged,
  });

  @override
  State<_SecretField> createState() => _SecretFieldState();
}

class _SecretFieldState extends State<_SecretField> {
  bool _obscure = true;
  bool _editing = false;

  @override
  Widget build(BuildContext context) {
    return _FieldWrapper(
      option: widget.option,
      isModified: widget.isModified,
      child: _editing
          ? TextField(
              controller: widget.controller,
              obscureText: _obscure,
              style: TextStyle(color: context.appColors.textPrimary, fontSize: 14),
              onChanged: widget.onChanged,
              decoration: InputDecoration(
                hintText: 'Enter new value',
                fillColor: context.appColors.bgElevated,
                border: OutlineInputBorder(
                  borderSide: BorderSide.none,
                  borderRadius: BorderRadius.circular(10),
                ),
                contentPadding:
                    EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                suffixIcon: IconButton(
                  icon: Icon(
                    _obscure
                        ? Icons.visibility_off_outlined
                        : Icons.visibility_outlined,
                    color: context.appColors.textMuted,
                    size: 18,
                  ),
                  onPressed: () => setState(() => _obscure = !_obscure),
                ),
              ),
            )
          : Row(
              children: [
                Expanded(
                  child: Container(
                    padding: EdgeInsets.symmetric(
                        horizontal: 12, vertical: 10),
                    decoration: BoxDecoration(
                      color: context.appColors.bgElevated,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      widget.option.value?.toString() ?? '',
                      style:
                          TextStyle(color: context.appColors.textSecondary, fontSize: 14),
                    ),
                  ),
                ),
                SizedBox(width: 8),
                TextButton(
                  onPressed: () {
                    widget.controller.clear();
                    setState(() => _editing = true);
                  },
                  child: Text('Change',
                      style: TextStyle(color: context.appColors.accentLight, fontSize: 12)),
                ),
              ],
            ),
    );
  }
}

class _SelectField extends StatelessWidget {
  final ConfigOption option;
  final String value;
  final bool isModified;
  final ValueChanged<String> onChanged;

  const _SelectField({
    required this.option,
    required this.value,
    required this.isModified,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    final items = option.options ?? [];
    final values = items.map((o) => o.value).toList();
    return _FieldWrapper(
      option: option,
      isModified: isModified,
      child: Container(
        padding: EdgeInsets.symmetric(horizontal: 12),
        decoration: BoxDecoration(
          color: context.appColors.bgElevated,
          borderRadius: BorderRadius.circular(10),
        ),
        child: DropdownButtonHideUnderline(
          child: DropdownButton<String>(
            value: values.contains(value) ? value : null,
            isExpanded: true,
            dropdownColor: context.appColors.bgSurface,
            style: TextStyle(color: context.appColors.textPrimary, fontSize: 14),
            hint: Text(option.label,
                style: TextStyle(color: context.appColors.textMuted, fontSize: 14)),
            items: items
                .map((o) =>
                    DropdownMenuItem(value: o.value, child: Text(o.label)))
                .toList(),
            onChanged: (v) {
              if (v != null) onChanged(v);
            },
          ),
        ),
      ),
    );
  }
}

class _BoolField extends StatelessWidget {
  final ConfigOption option;
  final bool value;
  final bool isModified;
  final ValueChanged<bool> onChanged;

  const _BoolField({
    required this.option,
    required this.value,
    required this.isModified,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return _FieldWrapper(
      option: option,
      isModified: isModified,
      child: SwitchListTile(
        title: Text(option.label,
            style: TextStyle(color: context.appColors.textPrimary, fontSize: 14)),
        value: value,
        activeTrackColor: context.appColors.accent,
        contentPadding: EdgeInsets.zero,
        onChanged: onChanged,
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Tool action button
// ---------------------------------------------------------------------------

class _ToolSecretField extends StatefulWidget {
  final String label;
  final String description;
  final String maskedValue;
  final TextEditingController? controller;
  final ValueChanged<dynamic> onChanged;

  const _ToolSecretField({
    super.key,
    required this.label,
    required this.description,
    required this.maskedValue,
    required this.controller,
    required this.onChanged,
  });

  @override
  State<_ToolSecretField> createState() => _ToolSecretFieldState();
}

class _ToolSecretFieldState extends State<_ToolSecretField> {
  bool _editing = false;
  bool _obscure = true;

  @override
  Widget build(BuildContext context) {
    final hasValue = widget.maskedValue.isNotEmpty;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(widget.label,
            style: TextStyle(color: context.appColors.textSecondary, fontSize: 11)),
        SizedBox(height: 4),
        if (!_editing) ...[
          Row(
            children: [
              Expanded(
                child: Text(
                  hasValue ? widget.maskedValue : 'Not set',
                  style: TextStyle(
                    color: hasValue ? context.appColors.textPrimary : context.appColors.textMuted,
                    fontSize: 13,
                    fontFamily: 'monospace',
                  ),
                ),
              ),
              SizedBox(
                height: 28,
                child: TextButton(
                  onPressed: () => setState(() {
                    _editing = true;
                    _obscure = true;
                    widget.controller?.clear();
                  }),
                  style: TextButton.styleFrom(
                    foregroundColor: context.appColors.accent,
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(6)),
                  ),
                  child: Text(hasValue ? 'Change' : 'Set',
                      style: TextStyle(fontSize: 11)),
                ),
              ),
            ],
          ),
        ] else
          TextField(
            controller: widget.controller,
            obscureText: _obscure,
            style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
            onChanged: (v) => widget.onChanged(v),
            decoration: InputDecoration(
              hintText: 'Enter new value',
              hintStyle: TextStyle(color: context.appColors.textMuted, fontSize: 12),
              fillColor: context.appColors.bgSurface,
              filled: true,
              border: OutlineInputBorder(
                borderSide: BorderSide.none,
                borderRadius: BorderRadius.circular(8),
              ),
              contentPadding:
                  EdgeInsets.symmetric(horizontal: 10, vertical: 8),
              suffixIcon: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  IconButton(
                    icon: Icon(
                      _obscure ? Icons.visibility_off : Icons.visibility,
                      size: 18,
                      color: context.appColors.textMuted,
                    ),
                    onPressed: () => setState(() => _obscure = !_obscure),
                    splashRadius: 16,
                  ),
                  IconButton(
                    icon: Icon(Icons.close, size: 18, color: context.appColors.textMuted),
                    onPressed: () => setState(() {
                      _editing = false;
                      widget.controller?.clear();
                      // Reset edit — send masked value back so it's treated
                      // as unchanged on the server side.
                      widget.onChanged(widget.maskedValue);
                    }),
                    splashRadius: 16,
                  ),
                ],
              ),
            ),
          ),
        if (widget.description.isNotEmpty)
          Padding(
            padding: EdgeInsets.only(top: 3),
            child: Text(widget.description,
                style: TextStyle(color: context.appColors.textMuted, fontSize: 10)),
          ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------

class _ToolActionButton extends StatelessWidget {
  final String label;
  final bool loading;
  final bool accent;
  final VoidCallback? onPressed;

  const _ToolActionButton({
    required this.label,
    required this.loading,
    this.accent = false,
    required this.onPressed,
  });

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 32,
      child: TextButton(
        onPressed: onPressed,
        style: TextButton.styleFrom(
          backgroundColor: accent ? context.appColors.accent : context.appColors.bgElevated,
          foregroundColor: accent ? Colors.white : context.appColors.textSecondary,
          padding: EdgeInsets.symmetric(horizontal: 12),
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        ),
        child: loading
            ? SizedBox(
                width: 14,
                height: 14,
                child: CircularProgressIndicator(
                    strokeWidth: 2, color: context.appColors.textSecondary),
              )
            : Text(label, style: const TextStyle(fontSize: 12)),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Tool progress bar
// ---------------------------------------------------------------------------

class _ToolProgressBar extends StatelessWidget {
  final Map<String, dynamic> progress;

  const _ToolProgressBar({required this.progress});

  @override
  Widget build(BuildContext context) {
    final message = progress['message'] as String? ?? '';
    final progressValue = progress['progress'];
    final double? pct =
        progressValue is num ? progressValue.toDouble() : null;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(3),
          child: SizedBox(
            height: 4,
            child: pct != null
                ? LinearProgressIndicator(
                    value: pct,
                    backgroundColor: context.appColors.bgOverlay,
                    valueColor:
                        AlwaysStoppedAnimation<Color>(context.appColors.accent),
                  )
                : LinearProgressIndicator(
                    backgroundColor: context.appColors.bgOverlay,
                    valueColor:
                        AlwaysStoppedAnimation<Color>(context.appColors.accent),
                  ),
          ),
        ),
        if (message.isNotEmpty)
          Padding(
            padding: EdgeInsets.only(top: 3),
            child: Text(message,
                style: TextStyle(color: context.appColors.textMuted, fontSize: 10)),
          ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Source badge (non-switchable)
// ---------------------------------------------------------------------------

class _SourceBadge extends StatelessWidget {
  final String label;
  final bool accent;

  _SourceBadge({required this.label, required this.accent});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: accent ? context.appColors.accentDim : context.appColors.bgOverlay,
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(label,
          style: TextStyle(
            color: accent ? context.appColors.accentLight : context.appColors.textMuted,
            fontSize: 9,
          )),
    );
  }
}

// ---------------------------------------------------------------------------
// Source toggle (managed <-> external)
// ---------------------------------------------------------------------------

class _SourceToggle extends StatelessWidget {
  final bool managed;
  final bool switching;
  final ValueChanged<bool> onSwitch;

  const _SourceToggle({
    required this.managed,
    required this.switching,
    required this.onSwitch,
  });

  @override
  Widget build(BuildContext context) {
    if (switching) {
      return SizedBox(
        width: 14,
        height: 14,
        child: CircularProgressIndicator(strokeWidth: 2, color: context.appColors.accentLight),
      );
    }
    return Container(
      height: 22,
      decoration: BoxDecoration(
        color: context.appColors.bgOverlay,
        borderRadius: BorderRadius.circular(4),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          _toggleOption(context, 'managed', managed, () => onSwitch(true)),
          _toggleOption(context, 'external', !managed, () => onSwitch(false)),
        ],
      ),
    );
  }

  Widget _toggleOption(BuildContext context, String label, bool active, VoidCallback onTap) {
    return GestureDetector(
      onTap: active ? null : onTap,
      child: Container(
        padding: EdgeInsets.symmetric(horizontal: 6, vertical: 2),
        decoration: BoxDecoration(
          color: active ? context.appColors.accentDim : Colors.transparent,
          borderRadius: BorderRadius.circular(4),
        ),
        child: Text(label,
            style: TextStyle(
              color: active ? context.appColors.accentLight : context.appColors.textMuted,
              fontSize: 9,
              fontWeight: active ? FontWeight.w600 : FontWeight.normal,
            )),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Field wrapper
// ---------------------------------------------------------------------------

class _FieldWrapper extends StatelessWidget {
  final ConfigOption option;
  final bool isModified;
  final Widget child;

  const _FieldWrapper({
    required this.option,
    required this.isModified,
    required this.child,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (option.type != 'boolean')
          Row(
            children: [
              Text(
                option.label,
                style: TextStyle(
                  color: isModified ? context.appColors.accentLight : context.appColors.textSecondary,
                  fontSize: 12,
                  fontWeight:
                      isModified ? FontWeight.w600 : FontWeight.normal,
                ),
              ),
              if (option.restartRequired)
                Padding(
                  padding: EdgeInsets.only(left: 6),
                  child: Text('restart required',
                      style: TextStyle(color: context.appColors.toolAccent, fontSize: 10)),
                ),
              if (isModified)
                Padding(
                  padding: EdgeInsets.only(left: 6),
                  child: Text('\u2022 modified',
                      style: TextStyle(color: context.appColors.accentLight, fontSize: 10)),
                ),
            ],
          ),
        if (option.type != 'boolean') SizedBox(height: 4),
        child,
        if (option.description.isNotEmpty)
          Padding(
            padding: EdgeInsets.only(top: 4),
            child: Text(
              option.description,
              style: TextStyle(color: context.appColors.textMuted, fontSize: 11),
            ),
          ),
      ],
    );
  }
}
