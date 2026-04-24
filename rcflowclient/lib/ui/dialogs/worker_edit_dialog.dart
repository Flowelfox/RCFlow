import 'dart:async';
import 'dart:io' as io;

import 'package:flutter/material.dart';

import '../../models/worker_config.dart';
import '../../services/server_url.dart';
import '../../services/worker_connection.dart';
import '../../theme.dart';
import '../screens/server_config_screen.dart';

/// Opens a dialog to create or edit a [WorkerConfig].
///
/// When [worker] is provided, a "Server" tab is shown. If connected it
/// displays the server configuration; if disconnected it shows a prompt
/// with a connect button.
///
/// [prefilled] seeds initial values for an *add* flow (e.g. from an
/// `rcflow://add-worker` deep link). Unlike [existing], the dialog still
/// opens in "Add" mode and generates a fresh id on save.
///
/// Returns the resulting config on save, or `null` if cancelled.
Future<WorkerConfig?> showWorkerEditDialog(
  BuildContext context, {
  WorkerConfig? existing,
  WorkerConfig? prefilled,
  int sortOrder = 0,
  WorkerConnection? worker,
}) {
  return showDialog<WorkerConfig>(
    context: context,
    builder: (_) => _WorkerEditDialog(
      existing: existing,
      prefilled: prefilled,
      sortOrder: sortOrder,
      worker: worker,
    ),
  );
}

// ---------------------------------------------------------------------------
// Dialog widget
// ---------------------------------------------------------------------------

class _WorkerEditDialog extends StatefulWidget {
  final WorkerConfig? existing;
  final WorkerConfig? prefilled;
  final int sortOrder;
  final WorkerConnection? worker;

  const _WorkerEditDialog({
    required this.existing,
    required this.prefilled,
    required this.sortOrder,
    this.worker,
  });

  @override
  State<_WorkerEditDialog> createState() => _WorkerEditDialogState();
}

enum _TestStatus { idle, testing, success, failure }

class _WorkerEditDialogState extends State<_WorkerEditDialog>
    with TickerProviderStateMixin {
  late final TabController _tabController;
  late final TextEditingController _nameCtrl;
  late final TextEditingController _hostCtrl;
  late final TextEditingController _portCtrl;
  late final TextEditingController _apiKeyCtrl;
  bool _obscureKey = true;
  late bool _useSSL;
  late bool _allowSelfSigned;
  late bool _autoConnect;
  String? _defaultAgent;

  // Validation
  bool _submitted = false;

  // Test connection
  _TestStatus _testStatus = _TestStatus.idle;
  String _testMessage = '';

  final _serverConfigKey = GlobalKey<ServerConfigContentState>();

  bool get _hasWorker => widget.worker != null;
  int get _tabCount => _hasWorker ? 2 : 1;

  @override
  void initState() {
    super.initState();
    // Seed controllers from `existing` (edit mode) or `prefilled` (add mode
    // with pre-filled values from a deep link). `existing` takes precedence.
    final seed = widget.existing ?? widget.prefilled;
    _tabController = TabController(length: _tabCount, vsync: this);
    _nameCtrl = TextEditingController(text: seed?.name ?? '');
    _hostCtrl = TextEditingController(text: seed?.host ?? '');
    _portCtrl = TextEditingController(
      text: seed != null ? seed.port.toString() : '53890',
    );
    _apiKeyCtrl = TextEditingController(text: seed?.apiKey ?? '');
    _useSSL = seed?.useSSL ?? false;
    _allowSelfSigned = seed?.allowSelfSigned ?? true;
    _autoConnect = seed?.autoConnect ?? true;
    _defaultAgent = seed?.defaultAgent;

    widget.worker?.addListener(_onWorkerChanged);
  }

  @override
  void dispose() {
    widget.worker?.removeListener(_onWorkerChanged);
    _tabController.dispose();
    _nameCtrl.dispose();
    _hostCtrl.dispose();
    _portCtrl.dispose();
    _apiKeyCtrl.dispose();
    super.dispose();
  }

  void _onWorkerChanged() {
    if (mounted) setState(() {});
  }

  bool get _isEdit => widget.existing != null;

  String? _fieldError(TextEditingController ctrl) {
    if (!_submitted) return null;
    return ctrl.text.trim().isEmpty ? 'Required' : null;
  }

  Future<void> _save() async {
    setState(() => _submitted = true);
    final name = _nameCtrl.text.trim();
    final host = _hostCtrl.text.trim();
    final portStr = _portCtrl.text.trim();
    final apiKey = _apiKeyCtrl.text.trim();
    if (name.isEmpty || host.isEmpty || portStr.isEmpty || apiKey.isEmpty) {
      return;
    }
    final port = int.tryParse(portStr);
    if (port == null || port < 1 || port > 65535) return;

    // Save server config + tool settings if the Server tab is active
    final serverState = _serverConfigKey.currentState;
    if (serverState != null && serverState.hasUnsavedChanges) {
      await serverState.saveAll();
    }

    if (!mounted) return;

    final config = WorkerConfig(
      id: widget.existing?.id ?? WorkerConfig.generateId(),
      name: name,
      host: host,
      port: port,
      apiKey: apiKey,
      useSSL: _useSSL,
      allowSelfSigned: _allowSelfSigned,
      autoConnect: _autoConnect,
      sortOrder: widget.existing?.sortOrder ?? widget.sortOrder,
      defaultAgent: _defaultAgent,
    );
    Navigator.of(context).pop(config);
  }

  Future<void> _confirmCancel() async {
    final hasServerChanges =
        _serverConfigKey.currentState?.hasUnsavedChanges ?? false;
    // If there are no changes at all, just close immediately
    if (!hasServerChanges) {
      Navigator.of(context).pop();
      return;
    }
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        title: Text(
          'Discard changes?',
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 16),
        ),
        content: Text(
          'Your unsaved changes will be lost.',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 14,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text(
              'No',
              style: TextStyle(color: context.appColors.textSecondary),
            ),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: context.appColors.accent,
            ),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Yes', style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
    if (confirmed == true && mounted) {
      Navigator.of(context).pop();
    }
  }

  Future<void> _testConnection() async {
    final host = _hostCtrl.text.trim();
    final portStr = _portCtrl.text.trim();
    final apiKey = _apiKeyCtrl.text.trim();
    if (host.isEmpty || portStr.isEmpty || apiKey.isEmpty) {
      setState(() {
        _testStatus = _TestStatus.failure;
        _testMessage = 'Host, Port, and API Key are required';
      });
      return;
    }
    final port = int.tryParse(portStr);
    if (port == null || port < 1 || port > 65535) {
      setState(() {
        _testStatus = _TestStatus.failure;
        _testMessage = 'Port must be between 1 and 65535';
      });
      return;
    }

    setState(() {
      _testStatus = _TestStatus.testing;
      _testMessage = '';
    });

    final url = ServerUrl(
      rawHost: '$host:$port',
      apiKey: apiKey,
      secure: _useSSL,
    );

    try {
      // 1. HTTP health check
      final httpClient = io.HttpClient();
      if (_allowSelfSigned) {
        httpClient.badCertificateCallback = (cert, host, port) => true;
      }
      httpClient.connectionTimeout = const Duration(seconds: 5);
      final healthUri = url.http('/api/health');
      final request = await httpClient.getUrl(healthUri);
      final response = await request.close().timeout(
        const Duration(seconds: 8),
      );
      final statusCode = response.statusCode;
      await response.drain<void>();
      httpClient.close(force: true);
      if (statusCode != 200) {
        _setFailure('Health check returned $statusCode');
        return;
      }

      // 2. WebSocket input channel test
      io.HttpClient? wsClient;
      if (_useSSL && _allowSelfSigned) {
        wsClient = io.HttpClient()
          ..badCertificateCallback = (cert, host, port) => true;
      }
      final wsInput = await io.WebSocket.connect(
        url.wsInputText().toString(),
        customClient: wsClient,
      ).timeout(const Duration(seconds: 8));
      unawaited(wsInput.close());

      // 3. WebSocket output channel test
      io.HttpClient? wsClient2;
      if (_useSSL && _allowSelfSigned) {
        wsClient2 = io.HttpClient()
          ..badCertificateCallback = (cert, host, port) => true;
      }
      final wsOutput = await io.WebSocket.connect(
        url.wsOutputText().toString(),
        customClient: wsClient2,
      ).timeout(const Duration(seconds: 8));
      unawaited(wsOutput.close());

      if (!mounted) return;
      setState(() {
        _testStatus = _TestStatus.success;
        _testMessage = 'All checks passed';
      });
    } on TimeoutException {
      _setFailure('Connection timed out');
    } on io.SocketException catch (e) {
      _setFailure(e.message);
    } catch (e) {
      _setFailure(_shortenError(e.toString()));
    }
  }

  void _setFailure(String message) {
    if (!mounted) return;
    setState(() {
      _testStatus = _TestStatus.failure;
      _testMessage = message;
    });
  }

  static String _shortenError(String raw) {
    var msg = raw
        .replaceFirst('Exception: ', '')
        .replaceFirst(RegExp(r'^.*?:\s*'), '');
    if (msg.length > 120) msg = '${msg.substring(0, 117)}...';
    return msg;
  }

  @override
  Widget build(BuildContext context) {
    final screenHeight = MediaQuery.of(context).size.height;
    final contentHeight = _hasWorker
        ? (screenHeight * 0.7).clamp(400.0, 700.0)
        : 480.0;

    return Dialog(
      backgroundColor: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      child: SizedBox(
        width: _hasWorker ? 600 : 500,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            // Title
            Padding(
              padding: EdgeInsets.fromLTRB(24, 24, 24, 0),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  _isEdit ? 'Edit Worker' : 'Add Worker',
                  style: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 18,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ),
            SizedBox(height: 16),
            // Tab bar (only shown when Server tab is available)
            if (_hasWorker)
              TabBar(
                controller: _tabController,
                indicatorColor: context.appColors.accent,
                labelColor: context.appColors.textPrimary,
                unselectedLabelColor: context.appColors.textMuted,
                dividerColor: context.appColors.divider,
                tabs: [
                  const Tab(text: 'Main'),
                  const Tab(text: 'Server'),
                ],
              ),
            // Tab views
            Flexible(
              child: ConstrainedBox(
                constraints: BoxConstraints(maxHeight: contentHeight),
                child: _hasWorker
                    ? TabBarView(
                        controller: _tabController,
                        children: [_buildMainTab(), _buildServerTab()],
                      )
                    : _buildMainTab(),
              ),
            ),
            // Test connection area
            _buildTestArea(),
            Divider(height: 1, color: context.appColors.divider),
            // Action buttons
            Padding(
              padding: EdgeInsets.fromLTRB(24, 12, 24, 16),
              child: Row(
                children: [
                  Spacer(),
                  TextButton(
                    onPressed: _confirmCancel,
                    child: Text(
                      'Cancel',
                      style: TextStyle(color: context.appColors.textSecondary),
                    ),
                  ),
                  SizedBox(width: 8),
                  FilledButton(
                    style: FilledButton.styleFrom(
                      backgroundColor: context.appColors.accent,
                    ),
                    onPressed: _save,
                    child: Text(
                      _isEdit ? 'Save' : 'Add',
                      style: const TextStyle(color: Colors.white),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildMainTab() {
    return SingleChildScrollView(
      padding: EdgeInsets.fromLTRB(24, 20, 24, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          _buildLabel('Name', required: true),
          SizedBox(height: 6),
          TextField(
            controller: _nameCtrl,
            autofocus: true,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 15,
            ),
            decoration: InputDecoration(
              hintText: 'Home Server',
              prefixIcon: Icon(
                Icons.label_outlined,
                color: context.appColors.textMuted,
                size: 20,
              ),
              fillColor: context.appColors.bgElevated,
              border: OutlineInputBorder(
                borderSide: BorderSide.none,
                borderRadius: BorderRadius.circular(14),
              ),
              errorText: _fieldError(_nameCtrl),
            ),
            onChanged: (_) {
              if (_submitted) setState(() {});
            },
          ),
          SizedBox(height: 16),
          _buildLabel('Host', required: true),
          SizedBox(height: 6),
          TextField(
            controller: _hostCtrl,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 15,
            ),
            decoration: InputDecoration(
              hintText: '127.0.0.1',
              prefixIcon: Icon(
                Icons.dns_outlined,
                color: context.appColors.textMuted,
                size: 20,
              ),
              fillColor: context.appColors.bgElevated,
              border: OutlineInputBorder(
                borderSide: BorderSide.none,
                borderRadius: BorderRadius.circular(14),
              ),
              errorText: _fieldError(_hostCtrl),
            ),
            onChanged: (_) {
              if (_submitted) setState(() {});
            },
          ),
          SizedBox(height: 16),
          _buildLabel('Port', required: true),
          SizedBox(height: 6),
          TextField(
            controller: _portCtrl,
            keyboardType: TextInputType.number,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 15,
            ),
            decoration: InputDecoration(
              hintText: '53890',
              prefixIcon: Icon(
                Icons.tag,
                color: context.appColors.textMuted,
                size: 20,
              ),
              fillColor: context.appColors.bgElevated,
              border: OutlineInputBorder(
                borderSide: BorderSide.none,
                borderRadius: BorderRadius.circular(14),
              ),
              errorText: _fieldError(_portCtrl),
            ),
            onChanged: (_) {
              if (_submitted) setState(() {});
            },
          ),
          SizedBox(height: 16),
          _buildLabel('API Key', required: true),
          SizedBox(height: 6),
          TextField(
            controller: _apiKeyCtrl,
            obscureText: _obscureKey,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 15,
            ),
            decoration: InputDecoration(
              hintText: 'Enter API key',
              prefixIcon: Icon(
                Icons.key_outlined,
                color: context.appColors.textMuted,
                size: 20,
              ),
              suffixIcon: IconButton(
                icon: Icon(
                  _obscureKey
                      ? Icons.visibility_off_outlined
                      : Icons.visibility_outlined,
                  color: context.appColors.textMuted,
                  size: 20,
                ),
                onPressed: () => setState(() => _obscureKey = !_obscureKey),
              ),
              fillColor: context.appColors.bgElevated,
              border: OutlineInputBorder(
                borderSide: BorderSide.none,
                borderRadius: BorderRadius.circular(14),
              ),
              errorText: _fieldError(_apiKeyCtrl),
            ),
            onChanged: (_) {
              if (_submitted) setState(() {});
            },
          ),
          SizedBox(height: 16),
          SwitchListTile(
            title: Text(
              'Use SSL (wss://)',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 14,
              ),
            ),
            value: _useSSL,
            activeTrackColor: context.appColors.accent,
            contentPadding: EdgeInsets.zero,
            onChanged: (v) => setState(() => _useSSL = v),
          ),
          SwitchListTile(
            title: Text(
              'Allow self-signed certificate',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 14,
              ),
            ),
            subtitle: Text(
              'Trust servers with self-signed TLS certificates',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 12,
              ),
            ),
            value: _allowSelfSigned,
            activeTrackColor: context.appColors.accent,
            contentPadding: EdgeInsets.zero,
            onChanged: (v) => setState(() => _allowSelfSigned = v),
          ),
          SwitchListTile(
            title: Text(
              'Auto-connect',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 14,
              ),
            ),
            subtitle: Text(
              'Connect automatically on app start',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 12,
              ),
            ),
            value: _autoConnect,
            activeTrackColor: context.appColors.accent,
            contentPadding: EdgeInsets.zero,
            onChanged: (v) => setState(() => _autoConnect = v),
          ),
          SizedBox(height: 16),
          _buildLabel('Default coding agent'),
          SizedBox(height: 6),
          DropdownButtonFormField<String?>(
            initialValue: _defaultAgent,
            dropdownColor: context.appColors.bgElevated,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 14,
            ),
            decoration: InputDecoration(
              prefixIcon: Icon(
                Icons.smart_toy_outlined,
                color: context.appColors.textMuted,
                size: 20,
              ),
              fillColor: context.appColors.bgElevated,
              border: OutlineInputBorder(
                borderSide: BorderSide.none,
                borderRadius: BorderRadius.circular(14),
              ),
            ),
            items: const [
              DropdownMenuItem(
                value: null,
                child: Text('None (let LLM decide)'),
              ),
              DropdownMenuItem(
                value: 'claude_code',
                child: Text('Claude Code'),
              ),
              DropdownMenuItem(value: 'codex', child: Text('Codex')),
              DropdownMenuItem(value: 'opencode', child: Text('OpenCode')),
            ],
            onChanged: (v) => setState(() => _defaultAgent = v),
          ),
        ],
      ),
    );
  }

  Widget _buildServerTab() {
    final worker = widget.worker!;
    if (worker.isConnected) {
      return ServerConfigContent(
        key: _serverConfigKey,
        ws: worker.ws,
        workerName: widget.existing?.name ?? '',
        embedded: true,
      );
    }

    final isConnecting = worker.isConnecting;
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.link_off_rounded,
            color: context.appColors.textMuted,
            size: 40,
          ),
          SizedBox(height: 16),
          Text(
            'Not connected to server',
            style: TextStyle(
              color: context.appColors.textSecondary,
              fontSize: 15,
            ),
          ),
          SizedBox(height: 4),
          Text(
            'Connect to view and manage server settings',
            style: TextStyle(color: context.appColors.textMuted, fontSize: 13),
          ),
          const SizedBox(height: 20),
          FilledButton.icon(
            onPressed: isConnecting ? null : () => worker.connect(),
            icon: isConnecting
                ? SizedBox(
                    width: 16,
                    height: 16,
                    child: CircularProgressIndicator(
                      strokeWidth: 2,
                      color: Colors.white,
                    ),
                  )
                : Icon(Icons.link_rounded, size: 18),
            label: Text(isConnecting ? 'Connecting...' : 'Connect'),
            style: FilledButton.styleFrom(
              backgroundColor: context.appColors.accent,
              foregroundColor: Colors.white,
              disabledBackgroundColor: context.appColors.accentDim,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(10),
              ),
              padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildTestArea() {
    return Padding(
      padding: EdgeInsets.fromLTRB(24, 8, 24, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              OutlinedButton.icon(
                onPressed: _testStatus == _TestStatus.testing
                    ? null
                    : _testConnection,
                icon: Icon(Icons.wifi_tethering_rounded, size: 18),
                label: Text('Test Connection'),
                style: OutlinedButton.styleFrom(
                  foregroundColor: context.appColors.textSecondary,
                  side: BorderSide(color: context.appColors.divider),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                  padding: EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                ),
              ),
              if (_testStatus == _TestStatus.testing) ...[
                SizedBox(width: 12),
                SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: context.appColors.accentLight,
                  ),
                ),
              ],
            ],
          ),
          if (_testStatus == _TestStatus.success) ...[
            SizedBox(height: 8),
            Row(
              children: [
                Icon(
                  Icons.check_circle_rounded,
                  color: context.appColors.successText,
                  size: 18,
                ),
                SizedBox(width: 6),
                Flexible(
                  child: Text(
                    _testMessage,
                    style: TextStyle(
                      color: context.appColors.successText,
                      fontSize: 13,
                    ),
                  ),
                ),
              ],
            ),
          ],
          if (_testStatus == _TestStatus.failure) ...[
            SizedBox(height: 8),
            Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Padding(
                  padding: EdgeInsets.only(top: 1),
                  child: Icon(
                    Icons.cancel_rounded,
                    color: context.appColors.errorText,
                    size: 18,
                  ),
                ),
                SizedBox(width: 6),
                Flexible(
                  child: Text(
                    _testMessage,
                    style: TextStyle(
                      color: context.appColors.errorText,
                      fontSize: 13,
                    ),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildLabel(String text, {bool required = false}) {
    return RichText(
      text: TextSpan(
        text: text,
        style: TextStyle(color: context.appColors.textSecondary, fontSize: 13),
        children: [
          if (required)
            TextSpan(
              text: ' *',
              style: TextStyle(
                color: context.appColors.accentLight,
                fontSize: 13,
              ),
            ),
        ],
      ),
    );
  }
}
