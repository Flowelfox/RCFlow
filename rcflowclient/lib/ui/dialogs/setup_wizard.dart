import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../state/app_state.dart';
import '../../state/setup_wizard_view_model.dart';
import '../../theme.dart';
import '../screens/server_config_screen.dart';

/// Shows the first-run setup wizard as a non-dismissible full-screen dialog.
///
/// Returns `true` if setup was completed or skipped (so the caller can
/// proceed to the onboarding tour).
Future<bool> showSetupWizard(BuildContext context) async {
  final result = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    barrierColor: Colors.black87,
    builder: (_) => const _SetupWizard(),
  );
  return result ?? false;
}

// ---------------------------------------------------------------------------
// Wizard widget
// ---------------------------------------------------------------------------

class _SetupWizard extends StatefulWidget {
  const _SetupWizard();

  @override
  State<_SetupWizard> createState() => _SetupWizardState();
}

class _SetupWizardState extends State<_SetupWizard> {
  late SetupWizardViewModel _vm;
  final _pageController = PageController();

  // Text controllers — owned by State since they are widget-lifecycle objects.
  late final TextEditingController _nameCtrl;
  late final TextEditingController _hostCtrl;
  late final TextEditingController _portCtrl;
  late final TextEditingController _apiKeyCtrl;

  // GlobalKey for the embedded ServerConfigContent in step 2.
  final _serverConfigKey = GlobalKey<ServerConfigContentState>();

  static const _totalSteps = 5;

  @override
  void initState() {
    super.initState();
    _vm = SetupWizardViewModel(context.read<AppState>());
    _vm.addListener(_onVmChanged);
    _nameCtrl = TextEditingController(text: 'My Server');
    _hostCtrl = TextEditingController();
    _portCtrl = TextEditingController(text: '53890');
    _apiKeyCtrl = TextEditingController();
  }

  void _onVmChanged() => setState(() {});

  @override
  void dispose() {
    _vm.removeListener(_onVmChanged);
    _vm.dispose();
    _pageController.dispose();
    _nameCtrl.dispose();
    _hostCtrl.dispose();
    _portCtrl.dispose();
    _apiKeyCtrl.dispose();
    super.dispose();
  }

  void _goTo(int step) {
    _vm.goToStep(step);
    _pageController.animateToPage(
      step,
      duration: const Duration(milliseconds: 300),
      curve: Curves.easeInOut,
    );
  }

  void _skip() {
    _vm.markComplete();
    Navigator.of(context).pop(true);
  }

  void _finish() {
    _vm.markComplete();
    Navigator.of(context).pop(true);
  }

  // --- Step 1: Worker connection helpers ---

  String? _fieldError(TextEditingController ctrl) {
    if (!_vm.submitted) return null;
    return ctrl.text.trim().isEmpty ? 'Required' : null;
  }

  // --- Build ---

  @override
  Widget build(BuildContext context) {
    return Dialog(
      backgroundColor: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(20)),
      insetPadding: const EdgeInsets.symmetric(horizontal: 40, vertical: 24),
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 600, maxHeight: 620),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            _buildStepIndicator(),
            Expanded(
              child: PageView(
                controller: _pageController,
                physics: const NeverScrollableScrollPhysics(),
                children: [
                  _buildWelcome(),
                  _buildWorkerStep(),
                  _buildLLMStep(),
                  _buildAgentStep(),
                  _buildSummary(),
                ],
              ),
            ),
            _buildBottomBar(),
          ],
        ),
      ),
    );
  }

  Widget _buildStepIndicator() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 20, 24, 0),
      child: Row(
        children: List.generate(_totalSteps, (i) {
          final isActive = i == _vm.currentStep;
          final isDone = i < _vm.currentStep;
          return Expanded(
            child: Padding(
              padding: EdgeInsets.only(right: i < _totalSteps - 1 ? 4 : 0),
              child: Container(
                height: 4,
                decoration: BoxDecoration(
                  borderRadius: BorderRadius.circular(2),
                  color: isDone
                      ? context.appColors.accent
                      : isActive
                      ? context.appColors.accentLight
                      : context.appColors.bgOverlay,
                ),
              ),
            ),
          );
        }),
      ),
    );
  }

  Widget _buildBottomBar() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(24, 8, 24, 20),
      child: Row(
        children: [
          if (_vm.currentStep < _totalSteps - 1)
            TextButton(
              onPressed: _skip,
              child: Text(
                'Skip Setup',
                style: TextStyle(color: context.appColors.textMuted),
              ),
            ),
          const Spacer(),
          if (_vm.currentStep > 0 && _vm.currentStep < _totalSteps - 1)
            TextButton(
              onPressed: () => _goTo(_vm.currentStep - 1),
              child: Text(
                'Back',
                style: TextStyle(color: context.appColors.textSecondary),
              ),
            ),
          if (_vm.currentStep > 0) const SizedBox(width: 8),
          _buildForwardButton(),
        ],
      ),
    );
  }

  Widget _buildForwardButton() {
    switch (_vm.currentStep) {
      case 0:
        return FilledButton(
          style: _accentButtonStyle(),
          onPressed: () => _goTo(1),
          child: const Text(
            'Get Started',
            style: TextStyle(color: Colors.white),
          ),
        );
      case 1:
        return FilledButton(
          style: _accentButtonStyle(),
          onPressed: _vm.connecting
              ? null
              : () async {
                  final ok = await _vm.createAndConnect(
                    name: _nameCtrl.text.trim(),
                    host: _hostCtrl.text.trim(),
                    portStr: _portCtrl.text.trim(),
                    apiKey: _apiKeyCtrl.text.trim(),
                  );
                  if (ok && mounted) _goTo(2);
                },
          child: _vm.connecting
              ? const SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: Colors.white,
                  ),
                )
              : const Text(
                  'Connect & Continue',
                  style: TextStyle(color: Colors.white),
                ),
        );
      case 2:
        return FilledButton(
          style: _accentButtonStyle(),
          onPressed: () async {
            await _serverConfigKey.currentState?.saveAll();
            unawaited(_vm.loadToolStatus());
            _goTo(3);
          },
          child: const Text('Next', style: TextStyle(color: Colors.white)),
        );
      case 3:
        return FilledButton(
          style: _accentButtonStyle(),
          onPressed: () {
            _vm.saveDefaultAgent();
            _goTo(4);
          },
          child: const Text('Next', style: TextStyle(color: Colors.white)),
        );
      default:
        return FilledButton(
          style: _accentButtonStyle(),
          onPressed: _finish,
          child: const Text('Finish', style: TextStyle(color: Colors.white)),
        );
    }
  }

  ButtonStyle _accentButtonStyle() {
    return FilledButton.styleFrom(
      backgroundColor: context.appColors.accent,
      disabledBackgroundColor: context.appColors.accentDim,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      padding: const EdgeInsets.symmetric(horizontal: 24, vertical: 14),
    );
  }

  // ---------------------------------------------------------------------------
  // Step 0: Welcome
  // ---------------------------------------------------------------------------

  Widget _buildWelcome() {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 32),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(
            Icons.rocket_launch_rounded,
            size: 64,
            color: context.appColors.accent,
          ),
          const SizedBox(height: 24),
          Text(
            'Welcome to RCFlow',
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 24,
              fontWeight: FontWeight.bold,
            ),
          ),
          const SizedBox(height: 12),
          Text(
            'Manage AI coding agents from anywhere.\n'
            'Let\'s get you connected to your first server.',
            textAlign: TextAlign.center,
            style: TextStyle(
              color: context.appColors.textSecondary,
              fontSize: 15,
              height: 1.5,
            ),
          ),
        ],
      ),
    );
  }

  // ---------------------------------------------------------------------------
  // Step 1: Worker connection
  // ---------------------------------------------------------------------------

  Widget _buildWorkerStep() {
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(24, 16, 24, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Connect to a Server',
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 18,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            'Enter your RCFlow server details.',
            style: TextStyle(color: context.appColors.textMuted, fontSize: 13),
          ),
          const SizedBox(height: 20),
          _label('Name', required: true),
          const SizedBox(height: 6),
          TextField(
            controller: _nameCtrl,
            autofocus: true,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 15,
            ),
            decoration: _inputDecoration(
              hint: 'Home Server',
              icon: Icons.label_outlined,
              error: _fieldError(_nameCtrl),
            ),
            onChanged: (_) {
              if (_vm.submitted) setState(() {});
            },
          ),
          const SizedBox(height: 14),
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                flex: 3,
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _label('Host', required: true),
                    const SizedBox(height: 6),
                    TextField(
                      controller: _hostCtrl,
                      style: TextStyle(
                        color: context.appColors.textPrimary,
                        fontSize: 15,
                      ),
                      decoration: _inputDecoration(
                        hint: '127.0.0.1',
                        icon: Icons.dns_outlined,
                        error: _fieldError(_hostCtrl),
                      ),
                      onChanged: (_) {
                        if (_vm.submitted) setState(() {});
                      },
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    _label('Port', required: true),
                    const SizedBox(height: 6),
                    TextField(
                      controller: _portCtrl,
                      keyboardType: TextInputType.number,
                      style: TextStyle(
                        color: context.appColors.textPrimary,
                        fontSize: 15,
                      ),
                      decoration: _inputDecoration(
                        hint: '53890',
                        error: _fieldError(_portCtrl),
                      ),
                      onChanged: (_) {
                        if (_vm.submitted) setState(() {});
                      },
                    ),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          _label('API Key', required: true),
          const SizedBox(height: 6),
          TextField(
            controller: _apiKeyCtrl,
            obscureText: _vm.obscureKey,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 15,
            ),
            decoration: _inputDecoration(
              hint: 'Enter API key',
              icon: Icons.key_outlined,
              error: _fieldError(_apiKeyCtrl),
            ).copyWith(
              suffixIcon: IconButton(
                icon: Icon(
                  _vm.obscureKey
                      ? Icons.visibility_off_outlined
                      : Icons.visibility_outlined,
                  color: context.appColors.textMuted,
                  size: 20,
                ),
                onPressed: () => _vm.setObscureKey(!_vm.obscureKey),
              ),
            ),
            onChanged: (_) {
              if (_vm.submitted) setState(() {});
            },
          ),
          const SizedBox(height: 12),
          SwitchListTile(
            title: Text(
              'Use SSL (wss://)',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 14,
              ),
            ),
            value: _vm.useSSL,
            activeTrackColor: context.appColors.accent,
            contentPadding: EdgeInsets.zero,
            onChanged: _vm.setUseSSL,
          ),
          if (_vm.useSSL)
            SwitchListTile(
              title: Text(
                'Allow self-signed certificate',
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 14,
                ),
              ),
              value: _vm.allowSelfSigned,
              activeTrackColor: context.appColors.accent,
              contentPadding: EdgeInsets.zero,
              onChanged: _vm.setAllowSelfSigned,
            ),
          SwitchListTile(
            title: Text(
              'Auto-connect on start',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 14,
              ),
            ),
            value: _vm.autoConnect,
            activeTrackColor: context.appColors.accent,
            contentPadding: EdgeInsets.zero,
            onChanged: _vm.setAutoConnect,
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              OutlinedButton.icon(
                onPressed: _vm.testStatus == SetupTestStatus.testing
                    ? null
                    : () => _vm.testConnection(
                          host: _hostCtrl.text.trim(),
                          portStr: _portCtrl.text.trim(),
                          apiKey: _apiKeyCtrl.text.trim(),
                        ),
                icon: const Icon(Icons.wifi_tethering_rounded, size: 18),
                label: const Text('Test'),
                style: OutlinedButton.styleFrom(
                  foregroundColor: context.appColors.textSecondary,
                  side: BorderSide(color: context.appColors.divider),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 14,
                    vertical: 10,
                  ),
                ),
              ),
              const SizedBox(width: 12),
              if (_vm.testStatus == SetupTestStatus.testing)
                SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(
                    strokeWidth: 2,
                    color: context.appColors.accentLight,
                  ),
                ),
              if (_vm.testStatus == SetupTestStatus.success) ...[
                Icon(
                  Icons.check_circle_rounded,
                  color: context.appColors.successText,
                  size: 18,
                ),
                const SizedBox(width: 6),
                Flexible(
                  child: Text(
                    _vm.testMessage,
                    style: TextStyle(
                      color: context.appColors.successText,
                      fontSize: 13,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
              if (_vm.testStatus == SetupTestStatus.failure) ...[
                Icon(
                  Icons.cancel_rounded,
                  color: context.appColors.errorText,
                  size: 18,
                ),
                const SizedBox(width: 6),
                Flexible(
                  child: Text(
                    _vm.testMessage,
                    style: TextStyle(
                      color: context.appColors.errorText,
                      fontSize: 13,
                    ),
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ],
          ),
          if (_vm.connectError != null) ...[
            const SizedBox(height: 8),
            Text(
              _vm.connectError!,
              style: TextStyle(
                color: context.appColors.errorText,
                fontSize: 13,
              ),
            ),
          ],
        ],
      ),
    );
  }

  // ---------------------------------------------------------------------------
  // Step 2: LLM configuration
  // ---------------------------------------------------------------------------

  Widget _buildLLMStep() {
    final appState = context.read<AppState>();
    final worker = _vm.createdWorkerId != null
        ? appState.getWorker(_vm.createdWorkerId!)
        : null;

    if (worker == null || !worker.isConnected) {
      return _centeredMessage(
        icon: Icons.link_off_rounded,
        title: 'Not connected',
        subtitle: 'Go back and connect to a server first.',
      );
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(24, 16, 24, 0),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'LLM Configuration',
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 18,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                'Configure the language model provider for your server.',
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 13,
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: ServerConfigContent(
            key: _serverConfigKey,
            ws: worker.ws,
            workerName: worker.config.name,
            embedded: true,
            sectionFilter: 'LLM',
          ),
        ),
      ],
    );
  }

  // ---------------------------------------------------------------------------
  // Step 3: Agent selection
  // ---------------------------------------------------------------------------

  Widget _buildAgentStep() {
    return SingleChildScrollView(
      padding: const EdgeInsets.fromLTRB(24, 16, 24, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            'Default Coding Agent',
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 18,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            'Choose which agent to use by default for new sessions. '
            'You can always override this per-session with the # selector.',
            style: TextStyle(color: context.appColors.textMuted, fontSize: 13),
          ),
          const SizedBox(height: 20),
          if (_vm.toolsLoading)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 32),
              child: Center(
                child: CircularProgressIndicator(
                  color: context.appColors.accent,
                ),
              ),
            )
          else if (_vm.toolsError != null)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 16),
              child: Text(
                'Could not load tool status: ${_vm.toolsError}',
                style: TextStyle(
                  color: context.appColors.errorText,
                  fontSize: 13,
                ),
              ),
            )
          else if (_vm.tools != null)
            ..._vm.tools!.entries.map(
              (e) => _buildToolCard(e.key, e.value as Map<String, dynamic>),
            ),
          if (_vm.tools == null && !_vm.toolsLoading && _vm.toolsError == null)
            Padding(
              padding: const EdgeInsets.symmetric(vertical: 16),
              child: Text(
                'No tool information available.',
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 13,
                ),
              ),
            ),
          const SizedBox(height: 20),
          _label('Default agent'),
          const SizedBox(height: 6),
          DropdownButtonFormField<String?>(
            initialValue: _vm.defaultAgent,
            dropdownColor: context.appColors.bgElevated,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 14,
            ),
            decoration: _inputDecoration(icon: Icons.smart_toy_outlined),
            items: const [
              DropdownMenuItem(
                value: null,
                child: Text('No preference'),
              ),
              DropdownMenuItem(
                value: 'claude_code',
                child: Text('Claude Code'),
              ),
              DropdownMenuItem(value: 'codex', child: Text('Codex')),
              DropdownMenuItem(value: 'opencode', child: Text('OpenCode')),
            ],
            onChanged: _vm.setDefaultAgent,
          ),
        ],
      ),
    );
  }

  Widget _buildToolCard(String toolKey, Map<String, dynamic> info) {
    final displayNames = {
      'claude_code': 'Claude Code',
      'codex': 'Codex',
      'opencode': 'OpenCode',
    };
    final displayName = displayNames[toolKey] ?? toolKey;
    final installed = info['installed'] == true;
    final version = info['version'] as String?;
    final managed = info['managed'] == true;

    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Container(
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: context.appColors.bgElevated,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: _vm.defaultAgent == toolKey
                ? context.appColors.accent.withAlpha(120)
                : context.appColors.divider,
          ),
        ),
        child: Row(
          children: [
            Icon(
              Icons.smart_toy_outlined,
              color: installed
                  ? context.appColors.accent
                  : context.appColors.textMuted,
              size: 24,
            ),
            const SizedBox(width: 12),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    displayName,
                    style: TextStyle(
                      color: context.appColors.textPrimary,
                      fontSize: 14,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
                  const SizedBox(height: 2),
                  Text(
                    installed
                        ? 'Installed${version != null ? ' (v$version)' : ''}${managed ? ' \u2022 Managed' : ''}'
                        : 'Not installed',
                    style: TextStyle(
                      color: installed
                          ? context.appColors.textMuted
                          : context.appColors.errorText,
                      fontSize: 12,
                    ),
                  ),
                ],
              ),
            ),
            if (installed)
              Icon(
                Icons.check_circle_rounded,
                color: context.appColors.successText,
                size: 20,
              ),
          ],
        ),
      ),
    );
  }

  // ---------------------------------------------------------------------------
  // Step 4: Summary
  // ---------------------------------------------------------------------------

  Widget _buildSummary() {
    final name = _nameCtrl.text.trim();
    final host = _hostCtrl.text.trim();
    final port = _portCtrl.text.trim();
    final agentNames = {
      'claude_code': 'Claude Code',
      'codex': 'Codex',
      'opencode': 'OpenCode',
    };
    final agentLabel = _vm.defaultAgent != null
        ? agentNames[_vm.defaultAgent] ?? _vm.defaultAgent!
        : 'None';

    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: 32),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(
            Icons.check_circle_outline_rounded,
            size: 56,
            color: context.appColors.successText,
          ),
          const SizedBox(height: 20),
          Text(
            'You\'re all set!',
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 22,
              fontWeight: FontWeight.bold,
            ),
          ),
          const SizedBox(height: 24),
          Container(
            width: double.infinity,
            padding: const EdgeInsets.all(16),
            decoration: BoxDecoration(
              color: context.appColors.bgElevated,
              borderRadius: BorderRadius.circular(14),
            ),
            child: Column(
              children: [
                _summaryRow('Server', name.isNotEmpty ? name : 'My Server'),
                const SizedBox(height: 8),
                _summaryRow('Address', '$host:$port'),
                const SizedBox(height: 8),
                _summaryRow('SSL', _vm.useSSL ? 'Enabled' : 'Disabled'),
                const SizedBox(height: 8),
                _summaryRow('Default Agent', agentLabel),
              ],
            ),
          ),
          const SizedBox(height: 20),
          Text(
            'You can change these settings anytime from the Settings menu.',
            textAlign: TextAlign.center,
            style: TextStyle(color: context.appColors.textMuted, fontSize: 13),
          ),
        ],
      ),
    );
  }

  Widget _summaryRow(String label, String value) {
    return Row(
      children: [
        SizedBox(
          width: 110,
          child: Text(
            label,
            style: TextStyle(color: context.appColors.textMuted, fontSize: 13),
          ),
        ),
        Expanded(
          child: Text(
            value,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 14,
            ),
          ),
        ),
      ],
    );
  }

  // ---------------------------------------------------------------------------
  // Helpers
  // ---------------------------------------------------------------------------

  Widget _label(String text, {bool required = false}) {
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

  InputDecoration _inputDecoration({
    String? hint,
    IconData? icon,
    String? error,
  }) {
    return InputDecoration(
      hintText: hint,
      prefixIcon: icon != null
          ? Icon(icon, color: context.appColors.textMuted, size: 20)
          : null,
      fillColor: context.appColors.bgElevated,
      border: OutlineInputBorder(
        borderSide: BorderSide.none,
        borderRadius: BorderRadius.circular(14),
      ),
      errorText: error,
    );
  }

  Widget _centeredMessage({
    required IconData icon,
    required String title,
    required String subtitle,
  }) {
    return Center(
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(icon, color: context.appColors.textMuted, size: 40),
          const SizedBox(height: 16),
          Text(
            title,
            style: TextStyle(
              color: context.appColors.textSecondary,
              fontSize: 15,
            ),
          ),
          const SizedBox(height: 4),
          Text(
            subtitle,
            style: TextStyle(color: context.appColors.textMuted, fontSize: 13),
          ),
        ],
      ),
    );
  }
}

// ignore_for_file: unawaited_futures
void unawaited(Future<void> future) {}
