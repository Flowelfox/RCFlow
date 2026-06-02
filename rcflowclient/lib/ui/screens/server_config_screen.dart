import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../models/server_config.dart';
import '../../services/websocket_service.dart';
import '../../services/worker_connection.dart';
import '../../theme.dart';
import '../widgets/custom_title_bar.dart';

part 'config_layout.dart';
part 'config_fields_text.dart';
part 'config_fields_model.dart';
part 'config_fields_misc.dart';
part 'config_fields_tool.dart';
part 'config_field_wrapper.dart';

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
