import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../models/server_config.dart';
import '../../services/websocket_service.dart';
import '../../services/worker_connection.dart';
import '../../theme.dart';
import '../widgets/custom_title_bar.dart';

void showServerConfigScreen(
  BuildContext context, {
  required WebSocketService ws,
  required String workerName,
  WorkerConnection? connection,
}) {
  Navigator.of(context).push(
    MaterialPageRoute(
      builder: (_) => _ServerConfigPage(
        ws: ws,
        workerName: workerName,
        connection: connection,
      ),
    ),
  );
}

// ---------------------------------------------------------------------------
// Full-screen page
// ---------------------------------------------------------------------------

class _ServerConfigPage extends StatelessWidget {
  final WebSocketService ws;
  final String workerName;
  final WorkerConnection? connection;

  const _ServerConfigPage({
    required this.ws,
    required this.workerName,
    this.connection,
  });

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
              '$workerName Settings',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 18,
              ),
            ),
          ),
          Expanded(
            child: ServerConfigContent(
              ws: ws,
              connection: connection,
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

  /// Optional reference to the host worker's [WorkerConnection]. When
  /// supplied, dynamic model dropdowns subscribe to its
  /// [WorkerConnection.modelCatalogGeneration] map and re-fetch the
  /// catalog whenever credentials change in the same form (config save,
  /// tool-settings save, login flow). Setup wizard runs without an
  /// active connection so this stays optional.
  final WorkerConnection? connection;

  /// When true, hides internal save buttons (used when embedded in a dialog
  /// that provides its own save action).
  final bool embedded;

  /// When set, renders only options in this config group (e.g. 'LLM')
  /// without the sidebar navigation.
  final String? sectionFilter;

  /// Sidebar section to preselect on first build (e.g. 'LLM'). Ignored when
  /// [sectionFilter] is set (filter mode has no sidebar). Falls back to the
  /// first available section if the name doesn't match after config loads.
  final String? initialSection;

  /// Invoked after the user saves changes via the embedded Save button.
  /// Useful for letting the host (e.g. worker edit dialog) refresh any
  /// config-derived state cached on the connection.
  final VoidCallback? onSaved;

  const ServerConfigContent({
    super.key,
    required this.ws,
    required this.workerName,
    this.scrollController,
    this.embedded = false,
    this.sectionFilter,
    this.initialSection,
    this.onSaved,
    this.connection,
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

  late String _selectedSection = widget.initialSection ?? 'Tools';

  final Map<String, dynamic> _editedValues = {};
  final Map<String, TextEditingController> _textControllers = {};

  Map<String, dynamic>? _tools;
  bool _toolsLoading = false;
  String? _toolsError;
  bool _toolsUpdating = false;
  final Set<String> _toolsUpdatingIndividual = {};
  final Set<String> _toolsInstalling = {};
  final Set<String> _toolsUninstalling = {};
  // Progress tracking for install/update operations (tool_name → progress data)
  final Map<String, Map<String, dynamic>> _toolProgress = {};

  final Map<String, List<Map<String, dynamic>>?> _toolSettings = {};
  final Map<String, bool> _toolSettingsLoading = {};
  final Map<String, String?> _toolSettingsError = {};
  final Map<String, Map<String, dynamic>> _toolSettingsEdited = {};
  final Map<String, bool> _toolSettingsSaving = {};
  // Whether the Tools group in the sidebar is expanded to show sub-items.
  bool _toolsSidebarExpanded = true;
  final Map<String, Map<String, TextEditingController>>
  _toolSettingsControllers = {};

  // Codex ChatGPT login state
  bool? _codexLoggedIn;
  bool _codexLoggingIn = false;
  String? _codexDeviceUrl;
  String? _codexDeviceCode;
  String? _codexAuthUrl; // Browser OAuth URL (non-device-code flow)
  String? _codexLoginError;

  // Claude Code Anthropic login state
  bool? _claudeCodeLoggedIn;
  bool _claudeCodeLoggingIn = false;
  bool _claudeCodeSubmittingCode = false;
  String? _claudeCodeAuthUrl;
  String? _claudeCodeLoginError;
  String? _claudeCodeEmail;
  String? _claudeCodeSubscription;
  final TextEditingController _claudeCodeAuthCodeController =
      TextEditingController();

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
    _claudeCodeAuthCodeController.dispose();
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
      final options = configResult
          .map((o) => ConfigOption.fromJson(o))
          .toList();
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
      _loadToolSettings(toolName);
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
        title: Text(
          'Uninstall $displayName?',
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 16),
        ),
        content: Text(
          'The managed binary will be removed. Settings will be preserved.',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 13,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            style: TextButton.styleFrom(
              foregroundColor: context.appColors.errorText,
            ),
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
      _loadToolSettings(toolName);
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _toolsError = e.toString();
        _toolsUninstalling.remove(toolName);
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

  /// Effective provider value for [toolName] = pending edit if any, else
  /// the saved value. Empty when the user has not yet picked one.
  String _effectiveToolProvider(String toolName) {
    final edits = _toolSettingsEdited[toolName];
    if (edits != null && edits.containsKey('provider')) {
      return edits['provider']?.toString() ?? '';
    }
    final fields = _toolSettings[toolName];
    if (fields == null) return '';
    final field = fields.firstWhere(
      (f) => f['key'] == 'provider',
      orElse: () => const {},
    );
    return field['value']?.toString() ?? '';
  }

  /// Coding agents whose ``provider`` field is required. Mirrors the backend
  /// preflight in ``src/core/agent_auth.py``.
  static const _codingAgentToolNames = {'claude_code', 'codex', 'opencode'};

  Future<void> _saveToolSettings(String toolName) async {
    final edits = _toolSettingsEdited[toolName];
    if (edits == null || edits.isEmpty) return;

    // Block save when a coding agent has no provider picked. The PATCH would
    // succeed but the agent could not run, and the user's intent in opening
    // this dialog was almost always to fix exactly that.
    if (_codingAgentToolNames.contains(toolName) &&
        _effectiveToolProvider(toolName).isEmpty) {
      setState(() {
        _toolSettingsError[toolName] =
            'Pick a provider before saving.';
      });
      return;
    }

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
        _toolSettingsError.remove(toolName);
        _initToolSettingsControllers(toolName, fields);
      });
      // Bump the catalog generation for this tool so any open dynamic
      // model dropdown re-fetches against the new credentials.
      widget.connection?.bumpModelCatalog(toolName);
      // Refresh the host worker's derived state (agent-readiness preflight,
      // token limits, …) so the warning banner disappears immediately.
      widget.onSaved?.call();
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
      // ChatGPT login completed — drop the Codex model cache so the next
      // dropdown open re-fetches against the freshly authenticated identity.
      widget.connection?.bumpModelCatalog('codex');
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

  Future<void> _checkClaudeCodeLoginStatus() async {
    try {
      final data = await widget.ws.claudeCodeLoginStatus();
      if (!mounted) return;
      setState(() {
        _claudeCodeLoggedIn = data['logged_in'] as bool? ?? false;
        _claudeCodeEmail = data['email'] as String?;
        _claudeCodeSubscription = data['subscription'] as String?;
        _claudeCodeLoginError = null;
      });
      // When the CLI is already authenticated via Anthropic Login (left over
      // from a prior session) but the saved provider is still unset, propose
      // ``anthropic_login`` as a pending edit so the user just clicks Save —
      // no need to manually pick the matching dropdown option.
      if (_claudeCodeLoggedIn == true) {
        _proposeProviderEdit('claude_code', 'anthropic_login');
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _claudeCodeLoggedIn = null;
        _claudeCodeLoginError = e.toString();
      });
    }
  }

  /// Stage [value] as a pending edit on the ``provider`` field for [toolName]
  /// when the saved value is empty and the user hasn't already edited it.
  /// Used to reflect detected OAuth-login state in the form so a single Save
  /// closes the loop. Caller is already inside [setState] / a microtask.
  void _proposeProviderEdit(String toolName, String value) {
    final fields = _toolSettings[toolName];
    if (fields == null) return;
    final providerField = fields.firstWhere(
      (f) => f['key'] == 'provider',
      orElse: () => const {},
    );
    if (providerField.isEmpty) return;
    final saved = providerField['value']?.toString() ?? '';
    if (saved.isNotEmpty) return;
    final edits = _toolSettingsEdited[toolName] ?? {};
    if (edits.containsKey('provider')) return;
    setState(() {
      edits['provider'] = value;
      _toolSettingsEdited[toolName] = edits;
    });
  }

  Future<void> _startClaudeCodeLogin() async {
    setState(() {
      _claudeCodeLoggingIn = true;
      _claudeCodeAuthUrl = null;
      _claudeCodeLoginError = null;
      _claudeCodeAuthCodeController.clear();
    });
    try {
      final result = await widget.ws.claudeCodeLogin();
      if (!mounted) return;
      final authUrl = result['auth_url'] as String?;
      setState(() {
        _claudeCodeAuthUrl = authUrl;
      });
      if (authUrl != null) {
        launchUrl(Uri.parse(authUrl), mode: LaunchMode.externalApplication);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _claudeCodeLoggingIn = false;
        _claudeCodeLoginError = e.toString();
        _claudeCodeAuthUrl = null;
      });
    }
  }

  Future<void> _submitClaudeCodeAuthCode() async {
    final code = _claudeCodeAuthCodeController.text.trim();
    if (code.isEmpty) return;

    setState(() {
      _claudeCodeSubmittingCode = true;
      _claudeCodeLoginError = null;
    });
    try {
      final result = await widget.ws.claudeCodeLoginCode(code);
      if (!mounted) return;
      final loggedIn = result['logged_in'] as bool? ?? false;
      setState(() {
        _claudeCodeSubmittingCode = false;
        _claudeCodeLoggingIn = false;
        _claudeCodeLoggedIn = loggedIn;
        _claudeCodeAuthUrl = null;
        _claudeCodeAuthCodeController.clear();
        if (loggedIn) {
          _claudeCodeEmail = result['email'] as String?;
          _claudeCodeSubscription = result['subscription'] as String?;
          // Reload settings to pick up auto-set provider
          _loadToolSettings('claude_code');
        } else {
          _claudeCodeLoginError = 'Login failed. Please try again.';
        }
      });
      if (loggedIn) {
        // Anthropic Login completed — drop the Claude Code model cache so
        // the next dropdown open re-fetches with subscription credentials.
        widget.connection?.bumpModelCatalog('claude_code');
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _claudeCodeSubmittingCode = false;
        _claudeCodeLoginError = e.toString();
      });
    }
  }

  Future<void> _claudeCodeLogout() async {
    try {
      await widget.ws.claudeCodeLogout();
      if (!mounted) return;
      setState(() {
        _claudeCodeLoggedIn = false;
        _claudeCodeEmail = null;
        _claudeCodeSubscription = null;
      });
      // Reload settings to pick up provider reset
      _loadToolSettings('claude_code');
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _claudeCodeLoginError = e.toString();
      });
    }
  }

  void _initToolSettingsControllers(
    String toolName,
    List<Map<String, dynamic>> fields,
  ) {
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
      if (type == 'string' || type == 'model_select') {
        controllers[key] = TextEditingController(
          text: field['value']?.toString() ?? '',
        );
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
      if (opt.type == 'string' ||
          opt.type == 'secret' ||
          opt.type == 'textarea' ||
          opt.type == 'number' ||
          opt.type == 'model_select') {
        _textControllers[opt.key] = TextEditingController(
          text: opt.value?.toString() ?? '',
        );
      } else if (opt.type == 'string_list') {
        final list = opt.value;
        final text = list is List
            ? list.join('\n')
            : (opt.value?.toString() ?? '');
        _textControllers[opt.key] = TextEditingController(text: text);
      }
    }
  }

  bool get _hasChanges => _editedValues.isNotEmpty;

  /// Whether there are unsaved server config or tool settings changes.
  bool get hasUnsavedChanges =>
      _editedValues.isNotEmpty || _toolSettingsEdited.isNotEmpty;

  /// Saves all pending changes (server config + all tool settings).
  Future<void> saveAll() async {
    if (_editedValues.isNotEmpty) await _save();
    for (final toolName in _toolSettingsEdited.keys.toList()) {
      await _saveToolSettings(toolName);
    }
  }

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
      final options = rawOptions.map((o) => ConfigOption.fromJson(o)).toList();
      if (!mounted) return;

      final hadRestartRequired = _editedValues.keys.any((key) {
        final opt = _options?.firstWhere(
          (o) => o.key == key,
          orElse: () => _options!.first,
        );
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
      // Global LLM credentials may have changed — refresh dropdowns.
      widget.connection?.bumpModelCatalog('global');
      widget.onSaved?.call();
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
            !_mergedIntoTools.contains(opt.group) &&
            _options!.any((o) => o.group == opt.group && _isVisible(o))) {
          sections.add(opt.group);
        }
      }
    }
    return sections;
  }

  String get _effectiveSection {
    final sections = _sectionNames;
    if (sections.contains(_selectedSection)) return _selectedSection;
    // Also valid when a tool sub-item key is selected (e.g. 'claude_code').
    if (_tools != null && _tools!.containsKey(_selectedSection)) {
      return _selectedSection;
    }
    return sections.first;
  }

  static IconData _iconForSection(String section) {
    switch (section) {
      case 'Tools':
        return Icons.handyman_outlined;
      case 'LLM':
        return Icons.psychology_outlined;
      case 'Prompt':
        return Icons.edit_note_outlined;
      case 'Codex':
        return Icons.menu_book_outlined;
      case 'Paths':
        return Icons.folder_outlined;
      case 'Tool Management':
        return Icons.tune_outlined;
      case 'Session Limits':
        return Icons.hourglass_empty_outlined;
      case 'Artifacts':
        return Icons.inventory_2_outlined;
      case 'Linear':
        return Icons.dashboard_outlined;
      case 'Logging':
        return Icons.receipt_long_outlined;
      default:
        return Icons.settings_outlined;
    }
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
            Icon(
              Icons.error_outline,
              color: context.appColors.errorText,
              size: 40,
            ),
            SizedBox(height: 12),
            Text(
              _error!,
              style: TextStyle(
                color: context.appColors.errorText,
                fontSize: 13,
              ),
            ),
            SizedBox(height: 16),
            TextButton(
              onPressed: _loadConfig,
              child: Text(
                'Retry',
                style: TextStyle(color: context.appColors.accentLight),
              ),
            ),
          ],
        ),
      );
    }

    // Filtered mode — render only the specified section without sidebar.
    if (widget.sectionFilter != null) {
      return SingleChildScrollView(
        controller: widget.scrollController,
        padding: const EdgeInsets.all(16),
        child: _buildSectionContent(widget.sectionFilter!),
      );
    }

    final section = _effectiveSection;

    return Row(
      children: [
        // Sidebar
        Container(
          width: 180,
          decoration: BoxDecoration(
            border: Border(
              right: BorderSide(color: context.appColors.divider, width: 1),
            ),
          ),
          child: ListView(
            padding: const EdgeInsets.symmetric(vertical: 8),
            children: [
              for (final s in _sectionNames)
                if (s == 'Tools') ...[
                  // Tools parent — tap selects the general Tools view and
                  // toggles the sub-item group.
                  _ConfigSidebarItem(
                    label: 'Tools',
                    icon: Icons.handyman_outlined,
                    selected: section == 'Tools',
                    hasModified:
                        _options != null &&
                        _options!
                            .where((o) => _mergedIntoTools.contains(o.group))
                            .any((o) => _editedValues.containsKey(o.key)),
                    trailingIcon: _toolsSidebarExpanded
                        ? Icons.expand_more_rounded
                        : Icons.chevron_right_rounded,
                    onTap: () => setState(() {
                      _selectedSection = 'Tools';
                      _toolsSidebarExpanded = !_toolsSidebarExpanded;
                    }),
                  ),
                  // Tool sub-items (Claude Code, Codex, …)
                  if (_toolsSidebarExpanded && _tools != null)
                    for (final toolKey in _tools!.keys)
                      _ConfigSidebarSubItem(
                        label: _toolDisplayNames[toolKey] ?? toolKey,
                        selected: section == toolKey,
                        onTap: () => setState(() => _selectedSection = toolKey),
                      ),
                ] else
                  _ConfigSidebarItem(
                    label: s,
                    icon: _iconForSection(s),
                    selected: section == s,
                    hasModified:
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
                if (!widget.embedded &&
                    (_hasChanges || _saveMessage != null)) ...[
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
                              horizontal: 16,
                              vertical: 8,
                            ),
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(10),
                            ),
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
    if (section == 'Tools') return _buildToolsSection();

    // Tool sub-item selected — show that tool's detail view.
    if (_tools != null && _tools!.containsKey(section)) {
      return _buildToolDetailSection(
        section,
        _tools![section] as Map<String, dynamic>,
      );
    }

    final groupOpts = _options!
        .where((o) => o.group == section && _isVisible(o))
        .toList();
    final hasModified = groupOpts.any((o) => _editedValues.containsKey(o.key));

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SectionHeader(
          title: section,
          icon: _iconForSection(section),
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
    'opencode': 'Opencode',
  };

  static const _kManagedTooltip =
      'Managed by the RCFlow worker. This is a separate install bundled with '
      'the worker — it does not interfere with any system-wide install you '
      'may already have.';

  /// Config option groups that are absorbed into the Tools section body
  /// instead of appearing as standalone sidebar items.
  static const _mergedIntoTools = {'Tool Management'};

  /// General Tools view — shown when the "Tools" sidebar parent is selected.
  ///
  /// Contains cross-tool actions (Check for Updates, Update All) and a hint
  /// to select a specific tool from the sidebar.
  Widget _buildToolsSection() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SectionHeader(title: 'Tools', icon: Icons.handyman_outlined),
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
                onPressed:
                    (_toolsUpdating || _toolsUpdatingIndividual.isNotEmpty)
                    ? null
                    : _updateTools,
              ),
            ],
          ],
        ),
        if (_toolsError != null)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              _toolsError!,
              style: TextStyle(
                color: context.appColors.errorText,
                fontSize: 12,
              ),
            ),
          ),
        // Options from groups merged into Tools (e.g. "Tool Management")
        ..._buildMergedToolsOptions(),
        const SizedBox(height: 16),
        Text(
          'Select a tool from the sidebar to view its settings.',
          style: TextStyle(color: context.appColors.textMuted, fontSize: 13),
        ),
      ],
    );
  }

  /// Builds the option widgets for every group in [_mergedIntoTools].
  ///
  /// Returned as a flat list so it can be spread directly into a Column's
  /// children (avoids variable declarations inside collection literals).
  List<Widget> _buildMergedToolsOptions() {
    if (_options == null) return [];
    final widgets = <Widget>[];
    for (final group in _mergedIntoTools) {
      final groupOpts = _options!
          .where((o) => o.group == group && _isVisible(o))
          .toList();
      if (groupOpts.isEmpty) continue;
      widgets.add(const SizedBox(height: 20));
      widgets.add(Divider(color: context.appColors.divider, height: 1));
      widgets.add(const SizedBox(height: 16));
      for (final opt in groupOpts) {
        widgets.add(_buildOptionWidget(opt));
        widgets.add(const SizedBox(height: 8));
      }
    }
    return widgets;
  }

  /// Body shown when a specific tool sub-item is selected in the sidebar.
  ///
  /// Renders a header row (icon + tool name + version + managed badge) then
  /// delegates to [_buildToolSubsectionBody] for actions, errors, and the
  /// settings panel.
  Widget _buildToolDetailSection(String key, Map<String, dynamic> tool) {
    // Kick off settings load the first time this tool's detail view is shown.
    if (_toolSettings[key] == null && !(_toolSettingsLoading[key] ?? false)) {
      Future.microtask(() => _loadToolSettings(key));
    }

    final displayName = _toolDisplayNames[key] ?? key;
    final installed = tool['installed'] as bool? ?? false;
    final currentVersion = tool['current_version'] as String?;
    final latestVersion = tool['latest_version'] as String?;
    final updateAvailable = tool['update_available'] as bool? ?? false;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.only(bottom: 16),
          child: Row(
            children: [
              Icon(
                Icons.handyman_outlined,
                color: context.appColors.accentLight,
                size: 20,
              ),
              const SizedBox(width: 8),
              Text(
                displayName,
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 17,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(width: 8),
              if (!installed)
                Text(
                  'not installed',
                  style: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 13,
                  ),
                )
              else ...[
                Text(
                  'v$currentVersion',
                  style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 13,
                  ),
                ),
                if (updateAvailable && latestVersion != null) ...[
                  const SizedBox(width: 4),
                  Icon(
                    Icons.arrow_forward_rounded,
                    color: context.appColors.textMuted,
                    size: 13,
                  ),
                  const SizedBox(width: 4),
                  Text(
                    'v$latestVersion',
                    style: TextStyle(
                      color: context.appColors.accentLight,
                      fontSize: 13,
                    ),
                  ),
                ],
              ],
              const Spacer(),
              if (installed)
                Tooltip(
                  message: _kManagedTooltip,
                  triggerMode: TooltipTriggerMode.longPress,
                  child: const _SourceBadge(label: 'managed', accent: true),
                ),
            ],
          ),
        ),
        _buildToolSubsectionBody(key, tool),
      ],
    );
  }

  /// Renders the body content for a tool detail view.
  ///
  /// Shows install/uninstall/update actions, progress feedback, errors, and
  /// the settings fields inline (always visible, no gear toggle). The version
  /// and tool name are rendered in the header above.
  Widget _buildToolSubsectionBody(String key, Map<String, dynamic> tool) {
    final installed = tool['installed'] as bool? ?? false;
    final updateAvailable = tool['update_available'] as bool? ?? false;
    final error = tool['error'] as String?;
    final managedPath = tool['managed_path'] as String?;
    final isInstalling = _toolsInstalling.contains(key);
    final isUninstalling = _toolsUninstalling.contains(key);
    final isUpdating = _toolsUpdatingIndividual.contains(key);
    final progress = _toolProgress[key];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Action button row: Install / Uninstall + Update.
        Row(
          children: [
            const Spacer(),
            if (managedPath == null)
              _ToolActionButton(
                label: 'Install',
                loading: isInstalling,
                accent: true,
                onPressed: isInstalling ? null : () => _installManagedTool(key),
              )
            else ...[
              _ToolActionButton(
                label: 'Uninstall',
                loading: isUninstalling,
                accent: false,
                onPressed: isUninstalling
                    ? null
                    : () => _confirmUninstall(key),
              ),
              if (updateAvailable && installed) ...[
                const SizedBox(width: 8),
                _ToolActionButton(
                  label: 'Update',
                  loading: isUpdating,
                  accent: true,
                  onPressed: isUpdating ? null : () => _updateSingleTool(key),
                ),
              ],
            ],
          ],
        ),
        // Error
        if (error != null)
          Padding(
            padding: const EdgeInsets.only(top: 4),
            child: Text(
              error,
              style: TextStyle(
                color: context.appColors.errorText,
                fontSize: 11,
              ),
            ),
          ),
        // Progress bar for install/update operations
        if (progress != null)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: _ToolProgressBar(progress: progress),
          ),
        // Settings fields — only shown after the tool is installed.  An
        // un-installed coding agent has nothing meaningful to configure;
        // showing the form would just confuse users.
        if (installed) ...[
          const SizedBox(height: 12),
          Divider(color: context.appColors.divider, height: 1),
          const SizedBox(height: 12),
          _buildToolSettingsPanel(key),
        ] else ...[
          const SizedBox(height: 12),
          Text(
            'Install ${_toolDisplayNames[key] ?? key} to configure it.',
            style: TextStyle(
              color: context.appColors.textMuted,
              fontSize: 12,
              fontStyle: FontStyle.italic,
            ),
          ),
        ],
      ],
    );
  }

  String? _resolveToolFieldValue(String toolName, String depKey) {
    final edits = _toolSettingsEdited[toolName];
    if (edits != null && edits.containsKey(depKey)) {
      return edits[depKey]?.toString();
    }
    final fields = _toolSettings[toolName];
    if (fields != null) {
      for (final f in fields) {
        if (f['key'] == depKey) {
          return f['value']?.toString();
        }
      }
    }
    return null;
  }

  bool _isToolFieldVisible(String toolName, Map<String, dynamic> field) {
    // hidden_when: hide when the dependency key matches the value
    final hiddenWhen = field['hidden_when'] as Map<String, dynamic>?;
    if (hiddenWhen != null) {
      final depKey = hiddenWhen['key'] as String;
      final depValue = hiddenWhen['value'] as String;
      if (_resolveToolFieldValue(toolName, depKey) == depValue) return false;
    }
    // visible_when: show only when the dependency key matches the value
    final visibleWhen = field['visible_when'] as Map<String, dynamic>?;
    if (visibleWhen == null) return true;
    final depKey = visibleWhen['key'] as String;
    final depValue = visibleWhen['value'] as String;
    return _resolveToolFieldValue(toolName, depKey) == depValue;
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
    if (_codexLoggedIn == null &&
        !_codexLoggingIn &&
        _codexLoginError == null) {
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
                Text(
                  'Checking status...',
                  style: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 11,
                  ),
                ),
              ],
            )
          else if (_codexLoggedIn == true && !_codexLoggingIn)
            Row(
              children: [
                Icon(
                  Icons.check_circle,
                  color: context.appColors.successText,
                  size: 14,
                ),
                SizedBox(width: 6),
                Text(
                  'Logged in',
                  style: TextStyle(
                    color: context.appColors.successText,
                    fontSize: 11,
                  ),
                ),
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
                Icon(
                  Icons.cancel_outlined,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                SizedBox(width: 6),
                Text(
                  'Not logged in',
                  style: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 11,
                  ),
                ),
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
                      style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 11,
                      ),
                    ),
                    SizedBox(height: 6),
                    Text(
                      'If the browser did not open, click the link:',
                      style: TextStyle(
                        color: context.appColors.textMuted,
                        fontSize: 10,
                      ),
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
                      strokeWidth: 1.5,
                      color: context.appColors.accent,
                    ),
                  ),
                  SizedBox(width: 8),
                  Text(
                    'Waiting for browser authentication...',
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 11,
                    ),
                  ),
                ],
              ),
            ]
            // Device code flow
            else if (_codexDeviceCode != null && _codexDeviceUrl != null) ...[
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
                      style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 11,
                      ),
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
                      strokeWidth: 1.5,
                      color: context.appColors.accent,
                    ),
                  ),
                  SizedBox(width: 8),
                  Text(
                    'Waiting for browser authentication...',
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 11,
                    ),
                  ),
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
                      strokeWidth: 1.5,
                      color: context.appColors.accent,
                    ),
                  ),
                  SizedBox(width: 8),
                  Text(
                    'Starting login...',
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 11,
                    ),
                  ),
                ],
              ),
            ],
          ],
          if (_codexLoginError != null)
            Padding(
              padding: EdgeInsets.only(top: 4),
              child: Text(
                _codexLoginError!,
                style: TextStyle(
                  color: context.appColors.errorText,
                  fontSize: 10,
                ),
              ),
            ),
        ],
      ),
    );
  }

  Widget _buildClaudeCodeLoginSection() {
    // Fetch status on first render
    if (_claudeCodeLoggedIn == null &&
        !_claudeCodeLoggingIn &&
        _claudeCodeLoginError == null) {
      Future.microtask(() => _checkClaudeCodeLoginStatus());
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
            'Anthropic Authentication',
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 12,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 6),
          // Status indicator
          if (_claudeCodeLoggedIn == null &&
              _claudeCodeLoginError == null &&
              !_claudeCodeLoggingIn)
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
                Text(
                  'Checking status...',
                  style: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 11,
                  ),
                ),
              ],
            )
          else if (_claudeCodeLoggedIn == true && !_claudeCodeLoggingIn)
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Icon(
                      Icons.check_circle,
                      color: context.appColors.successText,
                      size: 14,
                    ),
                    SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        _claudeCodeEmail != null
                            ? 'Logged in as $_claudeCodeEmail'
                            : 'Logged in',
                        style: TextStyle(
                          color: context.appColors.successText,
                          fontSize: 11,
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                  ],
                ),
                if (_claudeCodeSubscription != null) ...[
                  SizedBox(height: 2),
                  Padding(
                    padding: EdgeInsets.only(left: 20),
                    child: Text(
                      'Subscription: ${_claudeCodeSubscription!}',
                      style: TextStyle(
                        color: context.appColors.textMuted,
                        fontSize: 10,
                      ),
                    ),
                  ),
                ],
                SizedBox(height: 6),
                Row(
                  children: [
                    _ToolActionButton(
                      label: 'Re-login',
                      loading: false,
                      accent: false,
                      onPressed: () => _startClaudeCodeLogin(),
                    ),
                    SizedBox(width: 8),
                    _ToolActionButton(
                      label: 'Logout',
                      loading: false,
                      accent: false,
                      onPressed: () => _claudeCodeLogout(),
                    ),
                  ],
                ),
              ],
            )
          else if (!_claudeCodeLoggingIn) ...[
            Row(
              children: [
                Icon(
                  Icons.cancel_outlined,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                SizedBox(width: 6),
                Text(
                  'Not logged in',
                  style: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 11,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: _ToolActionButton(
                    label: 'Login with Anthropic',
                    loading: false,
                    accent: true,
                    onPressed: () => _startClaudeCodeLogin(),
                  ),
                ),
              ],
            ),
          ],
          // Active login flow — show URL + code input
          if (_claudeCodeLoggingIn) ...[
            if (_claudeCodeAuthUrl != null) ...[
              SizedBox(height: 8),
              Container(
                width: double.infinity,
                padding: EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: context.appColors.bgBase,
                  borderRadius: BorderRadius.circular(4),
                ),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Sign in via your browser, then paste the code below.',
                      style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 11,
                      ),
                    ),
                    SizedBox(height: 6),
                    GestureDetector(
                      onTap: () => launchUrl(
                        Uri.parse(_claudeCodeAuthUrl!),
                        mode: LaunchMode.externalApplication,
                      ),
                      child: Text(
                        'Open login page',
                        style: TextStyle(
                          color: context.appColors.accentLight,
                          fontSize: 11,
                          decoration: TextDecoration.underline,
                        ),
                      ),
                    ),
                    SizedBox(height: 10),
                    Row(
                      children: [
                        Expanded(
                          child: SizedBox(
                            height: 32,
                            child: TextField(
                              controller: _claudeCodeAuthCodeController,
                              style: TextStyle(
                                color: context.appColors.textPrimary,
                                fontSize: 12,
                              ),
                              decoration: InputDecoration(
                                hintText: 'Paste auth code here',
                                hintStyle: TextStyle(
                                  color: context.appColors.textMuted,
                                  fontSize: 12,
                                ),
                                contentPadding: EdgeInsets.symmetric(
                                  horizontal: 8,
                                  vertical: 6,
                                ),
                                border: OutlineInputBorder(
                                  borderRadius: BorderRadius.circular(4),
                                  borderSide: BorderSide(
                                    color: context.appColors.bgOverlay,
                                  ),
                                ),
                                enabledBorder: OutlineInputBorder(
                                  borderRadius: BorderRadius.circular(4),
                                  borderSide: BorderSide(
                                    color: context.appColors.bgOverlay,
                                  ),
                                ),
                                focusedBorder: OutlineInputBorder(
                                  borderRadius: BorderRadius.circular(4),
                                  borderSide: BorderSide(
                                    color: context.appColors.accent,
                                  ),
                                ),
                                filled: true,
                                fillColor: context.appColors.bgSurface,
                              ),
                              onSubmitted: (_) => _submitClaudeCodeAuthCode(),
                            ),
                          ),
                        ),
                        SizedBox(width: 8),
                        _ToolActionButton(
                          label: 'Submit',
                          loading: _claudeCodeSubmittingCode,
                          accent: true,
                          onPressed: _claudeCodeSubmittingCode
                              ? null
                              : () => _submitClaudeCodeAuthCode(),
                        ),
                      ],
                    ),
                  ],
                ),
              ),
            ] else ...[
              SizedBox(height: 6),
              Row(
                children: [
                  SizedBox(
                    width: 12,
                    height: 12,
                    child: CircularProgressIndicator(
                      strokeWidth: 1.5,
                      color: context.appColors.accent,
                    ),
                  ),
                  SizedBox(width: 8),
                  Text(
                    'Starting login...',
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 11,
                    ),
                  ),
                ],
              ),
            ],
          ],
          if (_claudeCodeLoginError != null)
            Padding(
              padding: EdgeInsets.only(top: 4),
              child: Text(
                _claudeCodeLoginError!,
                style: TextStyle(
                  color: context.appColors.errorText,
                  fontSize: 10,
                ),
              ),
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
        if (loading && fields == null)
          Padding(
            padding: EdgeInsets.symmetric(vertical: 8),
            child: Center(
              child: SizedBox(
                width: 16,
                height: 16,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: context.appColors.accent,
                ),
              ),
            ),
          )
        else if (error != null && fields == null)
          Padding(
            padding: EdgeInsets.symmetric(vertical: 4),
            child: Text(
              error,
              style: TextStyle(
                color: context.appColors.errorText,
                fontSize: 11,
              ),
            ),
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
            // Show Anthropic login section right after the provider field
            if (toolName == 'claude_code' &&
                field['key'] == 'provider' &&
                _getToolFieldValue(toolName, 'provider') == 'anthropic_login')
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: _buildClaudeCodeLoginSection(),
              ),
          ],
          if (!widget.embedded && (hasEdits || saving))
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
              child: Text(
                error,
                style: TextStyle(
                  color: context.appColors.errorText,
                  fontSize: 11,
                ),
              ),
            ),
        ],
      ],
    );
  }

  Widget _buildToolSettingField(String toolName, Map<String, dynamic> field) {
    final key = field['key'] as String;
    final label = field['label'] as String;
    final type = field['type'] as String;
    final description = field['description'] as String? ?? '';
    final comingSoon = field['coming_soon'] == true;
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
        final titleColor = comingSoon
            ? context.appColors.textMuted
            : context.appColors.textPrimary;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            SwitchListTile(
              title: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Flexible(
                    child: Text(
                      label,
                      style: TextStyle(color: titleColor, fontSize: 13),
                    ),
                  ),
                  if (comingSoon) ...[
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
                        'Coming soon',
                        style: TextStyle(
                          color: context.appColors.textMuted,
                          fontSize: 10,
                        ),
                      ),
                    ),
                  ],
                ],
              ),
              value: currentValue is bool ? currentValue : currentValue == true,
              activeTrackColor: context.appColors.accent,
              contentPadding: EdgeInsets.zero,
              dense: true,
              onChanged: comingSoon ? null : (v) => onChanged(v),
            ),
            if (description.isNotEmpty)
              Text(
                description,
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 10,
                ),
              ),
          ],
        );
      case 'select':
        final options =
            (field['options'] as List<dynamic>?)
                ?.cast<Map<String, dynamic>>() ??
            [];
        final values = options.map((o) => o['value'] as String).toList();
        input = Container(
          padding: EdgeInsets.symmetric(horizontal: 10),
          decoration: BoxDecoration(
            color: context.appColors.bgElevated,
            borderRadius: BorderRadius.circular(8),
          ),
          child: DropdownButtonHideUnderline(
            child: DropdownButton<String>(
              value: values.contains(currentValue?.toString())
                  ? currentValue?.toString()
                  : null,
              isExpanded: true,
              dropdownColor: context.appColors.bgSurface,
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
              ),
              hint: Text(
                'Choose a provider',
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 13,
                ),
              ),
              items: options
                  .map(
                    (o) => DropdownMenuItem(
                      value: o['value'] as String,
                      child: Text(o['label'] as String),
                    ),
                  )
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
          textAlignVertical: TextAlignVertical.center,
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
            hintStyle: TextStyle(
              color: context.appColors.textMuted,
              fontSize: 12,
            ),
            fillColor: context.appColors.bgElevated,
            filled: true,
            isDense: true,
            border: OutlineInputBorder(
              borderSide: BorderSide.none,
              borderRadius: BorderRadius.circular(8),
            ),
            contentPadding: EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          ),
        );
      case 'model_select':
        // Resolve current provider value from the provider_key field.
        final providerKey = field['provider_key'] as String?;
        String providerValue = '';
        if (providerKey != null) {
          providerValue = _getToolFieldValue(toolName, providerKey);
        }
        final modelsMap = field['models'] as Map<String, dynamic>?;
        final providerModels = modelsMap != null
            ? modelsMap[providerValue] as Map<String, dynamic>?
            : null;
        final modelOptions = providerModels != null
            ? (providerModels['options'] as List<dynamic>?)
                      ?.cast<Map<String, dynamic>>() ??
                  []
            : <Map<String, dynamic>>[];
        final allowCustom = providerModels?['allow_custom'] == true;
        final controller = _toolSettingsControllers[toolName]?[key];
        input = _DynamicModelInput(
          value: currentValue?.toString() ?? '',
          seedOptions: modelOptions,
          allowCustom: allowCustom,
          controller: controller,
          ws: widget.ws,
          connection: widget.connection,
          dynamicEnabled: field['dynamic'] == true,
          fetchScope: field['fetch_scope'] as String?,
          fetchProvider: field['fetch_provider'] as String?,
          providerSelection: providerValue,
          hintText: label,
          compact: true,
          onChanged: (v) => onChanged(v),
        );
      default:
        final controller = _toolSettingsControllers[toolName]?[key];
        input = TextField(
          controller: controller,
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
          textAlignVertical: TextAlignVertical.center,
          onChanged: (v) => onChanged(v),
          decoration: InputDecoration(
            hintText: label,
            hintStyle: TextStyle(
              color: context.appColors.textMuted,
              fontSize: 12,
            ),
            fillColor: context.appColors.bgElevated,
            filled: true,
            isDense: true,
            border: OutlineInputBorder(
              borderSide: BorderSide.none,
              borderRadius: BorderRadius.circular(8),
            ),
            contentPadding: EdgeInsets.symmetric(horizontal: 10, vertical: 8),
          ),
        );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 11,
          ),
        ),
        SizedBox(height: 4),
        input,
        if (description.isNotEmpty)
          Padding(
            padding: EdgeInsets.only(top: 3),
            child: Text(
              description,
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 10,
              ),
            ),
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
      case 'textarea':
        return _TextAreaField(
          option: opt,
          controller: _textControllers[opt.key]!,
          isModified: isModified,
          onChanged: (v) => _onValueChanged(opt.key, v, opt.value),
        );
      case 'model_select':
        // Resolve current provider value to pick the right model options.
        final providerKey = opt.providerKey;
        String providerValue = '';
        if (providerKey != null) {
          providerValue =
              (_editedValues.containsKey(providerKey)
                      ? _editedValues[providerKey]
                      : _options
                            ?.where((o) => o.key == providerKey)
                            .firstOrNull
                            ?.value)
                  ?.toString() ??
              '';
        }
        final modelsMap = opt.models;
        final providerModels = modelsMap != null
            ? modelsMap[providerValue] as Map<String, dynamic>?
            : null;
        final modelOptions = providerModels != null
            ? (providerModels['options'] as List<dynamic>?)
                      ?.cast<Map<String, dynamic>>() ??
                  []
            : <Map<String, dynamic>>[];
        final allowCustom = providerModels?['allow_custom'] == true;
        return _ModelSelectField(
          option: opt,
          value: currentValue?.toString() ?? '',
          isModified: isModified,
          modelOptions: modelOptions,
          allowCustom: allowCustom,
          controller: _textControllers[opt.key],
          ws: widget.ws,
          connection: widget.connection,
          providerSelection: providerValue,
          onChanged: (v) => _onValueChanged(opt.key, v, opt.value),
        );
      case 'string_list':
        return _StringListField(
          option: opt,
          currentValue: currentValue is List
              ? List<String>.from(currentValue)
              : (currentValue != null ? [currentValue.toString()] : []),
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
              child: Text(
                '\u2022 modified',
                style: TextStyle(
                  color: context.appColors.accentLight,
                  fontSize: 10,
                ),
              ),
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
  final IconData? trailingIcon;
  final VoidCallback onTap;

  const _ConfigSidebarItem({
    required this.label,
    required this.icon,
    required this.selected,
    this.hasModified = false,
    this.trailingIcon,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      child: Material(
        color: selected ? context.appColors.bgElevated : Colors.transparent,
        borderRadius: BorderRadius.circular(10),
        child: InkWell(
          borderRadius: BorderRadius.circular(10),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            child: Row(
              children: [
                Icon(
                  icon,
                  size: 18,
                  color: selected
                      ? context.appColors.accentLight
                      : context.appColors.textMuted,
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    label,
                    style: TextStyle(
                      color: selected
                          ? context.appColors.textPrimary
                          : context.appColors.textSecondary,
                      fontSize: 14,
                      fontWeight: selected
                          ? FontWeight.w600
                          : FontWeight.normal,
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
                if (trailingIcon != null) ...[
                  const SizedBox(width: 4),
                  Icon(
                    trailingIcon,
                    size: 16,
                    color: context.appColors.textMuted,
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Indented sidebar item used for tool sub-entries under the Tools group.
class _ConfigSidebarSubItem extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _ConfigSidebarSubItem({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      // Left indent aligns the sub-item under the parent's label.
      padding: const EdgeInsets.only(left: 24, right: 8, top: 1, bottom: 1),
      child: Material(
        color: selected ? context.appColors.bgElevated : Colors.transparent,
        borderRadius: BorderRadius.circular(8),
        child: InkWell(
          borderRadius: BorderRadius.circular(8),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: Row(
              children: [
                Container(
                  width: 4,
                  height: 4,
                  decoration: BoxDecoration(
                    color: selected
                        ? context.appColors.accentLight
                        : context.appColors.textMuted,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    label,
                    style: TextStyle(
                      color: selected
                          ? context.appColors.textPrimary
                          : context.appColors.textSecondary,
                      fontSize: 13,
                      fontWeight: selected
                          ? FontWeight.w600
                          : FontWeight.normal,
                    ),
                    overflow: TextOverflow.ellipsis,
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
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 12,
            vertical: 10,
          ),
        ),
      ),
    );
  }
}

class _TextAreaField extends StatelessWidget {
  final ConfigOption option;
  final TextEditingController controller;
  final bool isModified;
  final ValueChanged<String> onChanged;

  const _TextAreaField({
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
        maxLines: null,
        minLines: 3,
        keyboardType: TextInputType.multiline,
        decoration: InputDecoration(
          hintText: option.label,
          fillColor: context.appColors.bgElevated,
          border: OutlineInputBorder(
            borderSide: BorderSide.none,
            borderRadius: BorderRadius.circular(10),
          ),
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 12,
            vertical: 10,
          ),
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
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 14,
              ),
              onChanged: widget.onChanged,
              decoration: InputDecoration(
                hintText: 'Enter new value',
                fillColor: context.appColors.bgElevated,
                border: OutlineInputBorder(
                  borderSide: BorderSide.none,
                  borderRadius: BorderRadius.circular(10),
                ),
                contentPadding: EdgeInsets.symmetric(
                  horizontal: 12,
                  vertical: 10,
                ),
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
                    padding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                    decoration: BoxDecoration(
                      color: context.appColors.bgElevated,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      widget.option.value?.toString() ?? '',
                      style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 14,
                      ),
                    ),
                  ),
                ),
                SizedBox(width: 8),
                TextButton(
                  onPressed: () {
                    widget.controller.clear();
                    setState(() => _editing = true);
                  },
                  child: Text(
                    'Change',
                    style: TextStyle(
                      color: context.appColors.accentLight,
                      fontSize: 12,
                    ),
                  ),
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
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 14,
            ),
            hint: Text(
              option.label,
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 14,
              ),
            ),
            items: items
                .map(
                  (o) => DropdownMenuItem(value: o.value, child: Text(o.label)),
                )
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

class _ModelSelectField extends StatelessWidget {
  final ConfigOption option;
  final String value;
  final bool isModified;
  final List<Map<String, dynamic>> modelOptions;
  final bool allowCustom;
  final ValueChanged<String> onChanged;
  final TextEditingController? controller;
  final WebSocketService ws;
  final WorkerConnection? connection;
  final String? providerSelection;

  const _ModelSelectField({
    required this.option,
    required this.value,
    required this.isModified,
    required this.modelOptions,
    required this.allowCustom,
    required this.onChanged,
    required this.ws,
    this.controller,
    this.connection,
    this.providerSelection,
  });

  @override
  Widget build(BuildContext context) {
    return _FieldWrapper(
      option: option,
      isModified: isModified,
      child: _DynamicModelInput(
        value: value,
        seedOptions: modelOptions,
        allowCustom: allowCustom,
        onChanged: onChanged,
        controller: controller,
        ws: ws,
        connection: connection,
        dynamicEnabled: option.isDynamic,
        fetchScope: option.fetchScope,
        fetchProvider: option.fetchProvider,
        providerSelection: providerSelection,
        hintText: option.label,
        compact: false,
      ),
    );
  }
}

/// Shared dropdown / text-field widget that knows how to fetch its own
/// option list from ``GET /api/models`` when the schema marks the field
/// as ``dynamic``. Used by both server-config and tool-settings render
/// paths so they stay in sync on cache/refresh behaviour.
class _DynamicModelInput extends StatefulWidget {
  final String value;
  final List<Map<String, dynamic>> seedOptions;
  final bool allowCustom;
  final ValueChanged<String> onChanged;
  final TextEditingController? controller;
  final WebSocketService ws;

  /// Optional [WorkerConnection]; when supplied, the widget subscribes to
  /// its [WorkerConnection.modelCatalogGeneration] map and re-fetches
  /// whenever the host bumps the matching scope (e.g. after the user
  /// saves new credentials in this same dialog or completes a login).
  final WorkerConnection? connection;
  final bool dynamicEnabled;
  final String? fetchScope;
  final String? fetchProvider;
  final String? providerSelection;
  final String hintText;
  final bool compact;

  const _DynamicModelInput({
    required this.value,
    required this.seedOptions,
    required this.allowCustom,
    required this.onChanged,
    required this.ws,
    required this.dynamicEnabled,
    required this.hintText,
    required this.compact,
    this.controller,
    this.connection,
    this.fetchScope,
    this.fetchProvider,
    this.providerSelection,
  });

  @override
  State<_DynamicModelInput> createState() => _DynamicModelInputState();
}

class _DynamicModelInputState extends State<_DynamicModelInput> {
  List<Map<String, dynamic>> _liveOptions = const [];
  bool _loading = false;
  String _source = 'fallback';
  String? _error;
  DateTime? _fetchedAt;
  String? _lastFetchProvider;

  /// Controller passed to the inner DropdownMenu / TextField. We own it
  /// when the parent didn't supply one. Listening to it lets typed text
  /// (custom values not in the catalog) propagate to ``widget.onChanged``
  /// — without the listener, DropdownMenu only fires ``onSelected`` on
  /// entry pick and custom strings would silently disappear on save.
  late final TextEditingController _textController;
  bool _ownsController = false;
  bool _suppressTextListener = false;

  /// Last observed value of the host connection's catalog generation for
  /// ``widget.fetchScope``. When the listener fires and the current
  /// generation differs, we know credentials changed elsewhere in the
  /// form (key save, tool save, login flow) and re-fetch the catalog.
  int _seenCatalogGeneration = 0;

  @override
  void initState() {
    super.initState();
    if (widget.controller != null) {
      _textController = widget.controller!;
    } else {
      _textController = TextEditingController(text: widget.value);
      _ownsController = true;
    }
    _textController.addListener(_onTextChanged);
    _seenCatalogGeneration = _currentCatalogGeneration();
    widget.connection?.addListener(_onConnectionChanged);
    _maybeFetch();
  }

  @override
  void dispose() {
    widget.connection?.removeListener(_onConnectionChanged);
    _textController.removeListener(_onTextChanged);
    if (_ownsController) {
      _textController.dispose();
    }
    super.dispose();
  }

  @override
  void didUpdateWidget(covariant _DynamicModelInput oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.connection != widget.connection) {
      oldWidget.connection?.removeListener(_onConnectionChanged);
      widget.connection?.addListener(_onConnectionChanged);
      _seenCatalogGeneration = _currentCatalogGeneration();
    }
    if (oldWidget.providerSelection != widget.providerSelection ||
        oldWidget.fetchScope != widget.fetchScope ||
        oldWidget.fetchProvider != widget.fetchProvider) {
      _maybeFetch();
    }
  }

  int _currentCatalogGeneration() {
    final scope = widget.fetchScope;
    if (scope == null) return 0;
    return widget.connection?.modelCatalogGeneration[scope] ?? 0;
  }

  void _onConnectionChanged() {
    final current = _currentCatalogGeneration();
    if (current == _seenCatalogGeneration) return;
    _seenCatalogGeneration = current;
    _maybeFetch(refresh: true);
  }

  void _onTextChanged() {
    if (_suppressTextListener) return;
    final text = _textController.text;
    if (text == widget.value) return;
    // DropdownMenu writes the selected entry's *label* into the
    // controller, but the saved value should be the entry's raw id.
    // Map label → id when we can; otherwise treat the text as a custom
    // free-form value.
    final options = _mergedOptions();
    for (final o in options) {
      final lbl = (o['label'] as String?) ?? (o['value'] as String);
      final v = o['value'] as String? ?? '';
      if (v.isEmpty) continue;
      if (lbl == text) {
        if (v != widget.value) widget.onChanged(v);
        return;
      }
    }
    widget.onChanged(text);
  }

  String? _resolveProvider() {
    if (widget.fetchProvider != null && widget.fetchProvider!.isNotEmpty) {
      return widget.fetchProvider;
    }
    final p = widget.providerSelection;
    if (p == null || p.isEmpty) return null;
    return p;
  }

  Future<void> _maybeFetch({bool refresh = false}) async {
    if (!widget.dynamicEnabled) return;
    final scope = widget.fetchScope;
    final provider = _resolveProvider();
    if (scope == null || provider == null) {
      setState(() {
        _liveOptions = const [];
        _loading = false;
        _source = 'fallback';
        _error = null;
        _fetchedAt = null;
      });
      return;
    }
    // Skip duplicate fetch when nothing relevant changed.
    if (!refresh &&
        _lastFetchProvider == provider &&
        _liveOptions.isNotEmpty &&
        _source == 'live') {
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final body = await widget.ws.fetchModels(
        provider: provider,
        scope: scope,
        refresh: refresh,
      );
      final rawOptions = body['options'] as List<dynamic>? ?? [];
      final options = rawOptions
          .map((o) => Map<String, dynamic>.from(o as Map))
          .toList();
      DateTime? fetchedAt;
      final fetchedAtRaw = body['fetched_at'];
      if (fetchedAtRaw is String) {
        fetchedAt = DateTime.tryParse(fetchedAtRaw);
      }
      if (!mounted) return;
      setState(() {
        _liveOptions = options;
        _source = (body['source'] as String?) ?? 'fallback';
        _fetchedAt = fetchedAt?.toLocal();
        _error = body['error'] as String?;
        _loading = false;
        _lastFetchProvider = provider;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _liveOptions = const [];
        _source = 'fallback';
        _error = e.toString();
        _loading = false;
      });
    }
  }

  /// Merge live options over the seed list. Live values win when both
  /// list the same id; otherwise both sets show up (sorted: live first,
  /// seed-only entries appended).
  List<Map<String, dynamic>> _mergedOptions() {
    if (_liveOptions.isEmpty) return widget.seedOptions;
    final seenValues = <String>{};
    final merged = <Map<String, dynamic>>[];
    for (final o in _liveOptions) {
      final v = o['value'] as String?;
      if (v == null || v.isEmpty) continue;
      seenValues.add(v);
      merged.add(o);
    }
    for (final o in widget.seedOptions) {
      final v = o['value'] as String?;
      if (v == null || seenValues.contains(v)) continue;
      merged.add(o);
    }
    return merged;
  }

  String _statusLabel() {
    if (_loading) return 'Loading models…';
    if (_error != null && _source == 'fallback') return 'Offline (fallback list)';
    if (_source == 'fallback') return 'Fallback list';
    final ts = _fetchedAt;
    if (_source == 'live' && ts != null) {
      return 'Live · ${_formatRelative(ts)}';
    }
    if (_source == 'cached' && ts != null) {
      return 'Cached · ${_formatRelative(ts)}';
    }
    return _source;
  }

  Color _statusColor(BuildContext context) {
    if (_error != null && _source == 'fallback') return Colors.amber.shade600;
    if (_source == 'live') return context.appColors.successText;
    if (_source == 'cached') return context.appColors.textSecondary;
    return context.appColors.textMuted;
  }

  Widget _buildStatusRow(BuildContext context) {
    if (!widget.dynamicEnabled) return const SizedBox.shrink();
    final fontSize = widget.compact ? 10.0 : 11.0;
    return Padding(
      padding: EdgeInsets.only(bottom: widget.compact ? 4 : 6),
      child: Row(
        children: [
          if (_loading)
            SizedBox(
              width: 12,
              height: 12,
              child: CircularProgressIndicator(
                strokeWidth: 1.5,
                color: context.appColors.textSecondary,
              ),
            )
          else
            Icon(Icons.circle, size: 8, color: _statusColor(context)),
          const SizedBox(width: 6),
          Flexible(
            child: Tooltip(
              message: _error ?? '',
              child: Text(
                _statusLabel(),
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: context.appColors.textSecondary,
                  fontSize: fontSize,
                ),
              ),
            ),
          ),
          IconButton(
            icon: Icon(
              Icons.refresh,
              size: widget.compact ? 14 : 16,
              color: context.appColors.textSecondary,
            ),
            tooltip: 'Refresh model list',
            onPressed: _loading ? null : () => _maybeFetch(refresh: true),
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(minHeight: 24, minWidth: 24),
          ),
        ],
      ),
    );
  }

  Widget _buildInput(BuildContext context, List<Map<String, dynamic>> options) {
    final colors = context.appColors;
    final textSize = widget.compact ? 13.0 : 14.0;
    final radius = widget.compact ? 8.0 : 10.0;
    final padding = widget.compact
        ? const EdgeInsets.symmetric(horizontal: 10, vertical: 8)
        : const EdgeInsets.symmetric(horizontal: 12, vertical: 10);

    if (options.isEmpty) {
      return TextField(
        controller: _textController,
        style: TextStyle(color: colors.textPrimary, fontSize: textSize),
        decoration: InputDecoration(
          hintText: widget.hintText,
          fillColor: colors.bgElevated,
          filled: true,
          isDense: widget.compact,
          border: OutlineInputBorder(
            borderSide: BorderSide.none,
            borderRadius: BorderRadius.circular(radius),
          ),
          contentPadding: padding,
        ),
      );
    }

    final entries = options
        .map(
          (o) => DropdownMenuEntry<String>(
            value: o['value'] as String,
            label: (o['label'] as String?) ?? (o['value'] as String),
          ),
        )
        .toList();

    // Show a warning glyph on the dropdown when the saved value is a
    // custom string the catalog does not list. The user can still save —
    // this is purely informational so they know the value isn't one we
    // got from the upstream provider.
    final value = widget.value;
    final isCustomValue =
        value.isNotEmpty && !entries.any((e) => e.value == value);
    final iconSize = widget.compact ? 16.0 : 18.0;
    final Widget? warningIcon = isCustomValue
        ? Tooltip(
            message:
                'Model "$value" is not listed in the catalog. '
                'It will still be saved as-is.',
            triggerMode: TooltipTriggerMode.tap,
            waitDuration: const Duration(milliseconds: 200),
            showDuration: const Duration(seconds: 4),
            child: Icon(
              Icons.warning_amber_rounded,
              size: iconSize,
              color: Colors.amber.shade600,
            ),
          )
        : null;

    return DropdownMenu<String>(
      controller: _textController,
      initialSelection:
          entries.any((e) => e.value == widget.value) ? widget.value : null,
      dropdownMenuEntries: entries,
      enableFilter: true,
      enableSearch: true,
      requestFocusOnTap: true,
      leadingIcon: warningIcon,
      // Substring match on BOTH the human label and the raw model id, so
      // typing "5.5" surfaces every entry containing that fragment instead
      // of only those whose label starts with "5.5". When nothing
      // matches, return a single disabled "no matches" entry so the
      // dropdown doesn't render as a blank empty box — the user keeps
      // their typed text and can still save it as a custom value.
      filterCallback: (List<DropdownMenuEntry<String>> entries, String filter) {
        if (filter.isEmpty) return entries;
        final needle = filter.toLowerCase();
        final matches = entries.where((e) {
          final label = e.label.toLowerCase();
          final value = e.value.toLowerCase();
          return label.contains(needle) || value.contains(needle);
        }).toList();
        if (matches.isEmpty) {
          return [
            DropdownMenuEntry<String>(
              value: '__no_match__',
              label: 'No models match "$filter" — value will save as custom',
              enabled: false,
            ),
          ];
        }
        return matches;
      },
      // Default `searchCallback` selects the first prefix-matching entry to
      // highlight while typing. Keep that behaviour off so the cursor
      // doesn't jump while the user is filtering — they can pick from the
      // menu themselves.
      searchCallback: (entries, query) => null,
      expandedInsets: EdgeInsets.zero,
      textStyle: TextStyle(color: colors.textPrimary, fontSize: textSize),
      menuStyle: MenuStyle(
        backgroundColor: WidgetStatePropertyAll(colors.bgElevated),
      ),
      inputDecorationTheme: InputDecorationTheme(
        fillColor: colors.bgElevated,
        filled: true,
        border: OutlineInputBorder(
          borderSide: BorderSide.none,
          borderRadius: BorderRadius.circular(radius),
        ),
        contentPadding: padding,
        hintStyle: TextStyle(
          color: colors.textMuted,
          fontSize: textSize,
        ),
        isDense: true,
      ),
      onSelected: (v) {
        // Suppress the listener while we sync the controller text to the
        // selected entry's id, then re-fire onChanged ourselves. Without
        // the suppression DropdownMenu's own ``controller.text = label``
        // write would briefly mark the value as "custom" and flicker the
        // warning icon between the keystroke and our resync.
        if (v == null || v == '__no_match__') return;
        _suppressTextListener = true;
        try {
          if (_textController.text != v) _textController.text = v;
        } finally {
          _suppressTextListener = false;
        }
        widget.onChanged(v);
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final options = _mergedOptions();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _buildStatusRow(context),
        _buildInput(context, options),
      ],
    );
  }
}

String _formatRelative(DateTime ts) {
  final now = DateTime.now();
  final diff = now.difference(ts);
  if (diff.inSeconds < 30) return 'just now';
  if (diff.inMinutes < 1) return '${diff.inSeconds}s ago';
  if (diff.inHours < 1) return '${diff.inMinutes}m ago';
  if (diff.inDays < 1) return '${diff.inHours}h ago';
  return '${diff.inDays}d ago';
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
        title: Text(
          option.label,
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 14),
        ),
        value: value,
        activeTrackColor: context.appColors.accent,
        contentPadding: EdgeInsets.zero,
        onChanged: onChanged,
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// String list field (add/remove/reorder)
// ---------------------------------------------------------------------------

class _StringListField extends StatefulWidget {
  final ConfigOption option;
  final List<String> currentValue;
  final bool isModified;
  final ValueChanged<List<String>> onChanged;

  const _StringListField({
    required this.option,
    required this.currentValue,
    required this.isModified,
    required this.onChanged,
  });

  @override
  State<_StringListField> createState() => _StringListFieldState();
}

class _StringListFieldState extends State<_StringListField> {
  late List<TextEditingController> _controllers;
  final List<FocusNode> _focusNodes = [];

  @override
  void initState() {
    super.initState();
    _controllers = widget.currentValue
        .map((v) => TextEditingController(text: v))
        .toList();
    for (var i = 0; i < _controllers.length; i++) {
      _focusNodes.add(FocusNode());
    }
  }

  @override
  void didUpdateWidget(covariant _StringListField oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.currentValue.length != _controllers.length) {
      for (final c in _controllers) {
        c.dispose();
      }
      for (final f in _focusNodes) {
        f.dispose();
      }
      _controllers = widget.currentValue
          .map((v) => TextEditingController(text: v))
          .toList();
      _focusNodes.clear();
      for (var i = 0; i < _controllers.length; i++) {
        _focusNodes.add(FocusNode());
      }
    }
  }

  @override
  void dispose() {
    for (final c in _controllers) {
      c.dispose();
    }
    for (final f in _focusNodes) {
      f.dispose();
    }
    super.dispose();
  }

  void _emit() {
    final values = _controllers
        .map((c) => c.text.trim())
        .where((s) => s.isNotEmpty)
        .toList();
    widget.onChanged(values);
  }

  void _addEntry() {
    setState(() {
      _controllers.add(TextEditingController());
      _focusNodes.add(FocusNode());
    });
    _emit();
    // Focus the new field after build.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_focusNodes.isNotEmpty) {
        _focusNodes.last.requestFocus();
      }
    });
  }

  void _removeEntry(int index) {
    setState(() {
      _controllers[index].dispose();
      _controllers.removeAt(index);
      _focusNodes[index].dispose();
      _focusNodes.removeAt(index);
    });
    _emit();
  }

  @override
  Widget build(BuildContext context) {
    return _FieldWrapper(
      option: widget.option,
      isModified: widget.isModified,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (var i = 0; i < _controllers.length; i++)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Row(
                children: [
                  Icon(
                    Icons.folder_outlined,
                    color: context.appColors.textMuted,
                    size: 18,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: TextField(
                      controller: _controllers[i],
                      focusNode: _focusNodes[i],
                      style: TextStyle(
                        color: context.appColors.textPrimary,
                        fontSize: 14,
                      ),
                      onChanged: (_) => _emit(),
                      decoration: InputDecoration(
                        hintText: '~/Projects',
                        hintStyle: TextStyle(
                          color: context.appColors.textMuted,
                          fontSize: 13,
                        ),
                        fillColor: context.appColors.bgElevated,
                        filled: true,
                        border: OutlineInputBorder(
                          borderSide: BorderSide.none,
                          borderRadius: BorderRadius.circular(10),
                        ),
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: 12,
                          vertical: 10,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 4),
                  SizedBox(
                    width: 32,
                    height: 32,
                    child: IconButton(
                      padding: EdgeInsets.zero,
                      iconSize: 18,
                      tooltip: 'Remove folder',
                      icon: Icon(
                        Icons.delete_outline,
                        color: context.appColors.textMuted,
                      ),
                      onPressed: () => _removeEntry(i),
                    ),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 2),
          SizedBox(
            height: 32,
            child: TextButton.icon(
              onPressed: _addEntry,
              icon: Icon(
                Icons.add_rounded,
                size: 18,
                color: context.appColors.accentLight,
              ),
              label: Text(
                'Add folder',
                style: TextStyle(
                  color: context.appColors.accentLight,
                  fontSize: 12,
                ),
              ),
              style: TextButton.styleFrom(
                padding: const EdgeInsets.symmetric(horizontal: 10),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(8),
                ),
              ),
            ),
          ),
        ],
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
        Text(
          widget.label,
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 11,
          ),
        ),
        SizedBox(height: 4),
        if (!_editing) ...[
          Row(
            children: [
              Expanded(
                child: Text(
                  hasValue ? widget.maskedValue : 'Not set',
                  style: TextStyle(
                    color: hasValue
                        ? context.appColors.textPrimary
                        : context.appColors.textMuted,
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
                      borderRadius: BorderRadius.circular(6),
                    ),
                  ),
                  child: Text(
                    hasValue ? 'Change' : 'Set',
                    style: TextStyle(fontSize: 11),
                  ),
                ),
              ),
            ],
          ),
        ] else
          TextField(
            controller: widget.controller,
            obscureText: _obscure,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 13,
            ),
            onChanged: (v) => widget.onChanged(v),
            decoration: InputDecoration(
              hintText: 'Enter new value',
              hintStyle: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 12,
              ),
              fillColor: context.appColors.bgSurface,
              filled: true,
              border: OutlineInputBorder(
                borderSide: BorderSide.none,
                borderRadius: BorderRadius.circular(8),
              ),
              contentPadding: EdgeInsets.symmetric(horizontal: 10, vertical: 8),
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
                    icon: Icon(
                      Icons.close,
                      size: 18,
                      color: context.appColors.textMuted,
                    ),
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
            child: Text(
              widget.description,
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 10,
              ),
            ),
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
          backgroundColor: accent
              ? context.appColors.accent
              : context.appColors.bgElevated,
          foregroundColor: accent
              ? Colors.white
              : context.appColors.textSecondary,
          padding: EdgeInsets.symmetric(horizontal: 12),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        ),
        child: loading
            ? SizedBox(
                width: 14,
                height: 14,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: context.appColors.textSecondary,
                ),
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
    final double? pct = progressValue is num ? progressValue.toDouble() : null;

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
                    valueColor: AlwaysStoppedAnimation<Color>(
                      context.appColors.accent,
                    ),
                  )
                : LinearProgressIndicator(
                    backgroundColor: context.appColors.bgOverlay,
                    valueColor: AlwaysStoppedAnimation<Color>(
                      context.appColors.accent,
                    ),
                  ),
          ),
        ),
        if (message.isNotEmpty)
          Padding(
            padding: EdgeInsets.only(top: 3),
            child: Text(
              message,
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 10,
              ),
            ),
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

  const _SourceBadge({required this.label, required this.accent});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: accent
            ? context.appColors.accentDim
            : context.appColors.bgOverlay,
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: accent
              ? context.appColors.accentLight
              : context.appColors.textMuted,
          fontSize: 9,
        ),
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
                  color: isModified
                      ? context.appColors.accentLight
                      : context.appColors.textSecondary,
                  fontSize: 12,
                  fontWeight: isModified ? FontWeight.w600 : FontWeight.normal,
                ),
              ),
              if (option.restartRequired)
                Padding(
                  padding: EdgeInsets.only(left: 6),
                  child: Text(
                    'restart required',
                    style: TextStyle(
                      color: context.appColors.toolAccent,
                      fontSize: 10,
                    ),
                  ),
                ),
              if (isModified)
                Padding(
                  padding: EdgeInsets.only(left: 6),
                  child: Text(
                    '\u2022 modified',
                    style: TextStyle(
                      color: context.appColors.accentLight,
                      fontSize: 10,
                    ),
                  ),
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
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 11,
              ),
            ),
          ),
      ],
    );
  }
}
