/// ViewModel for the setup wizard dialog.
///
/// Owns all non-widget state and async business logic for the 5-step wizard:
/// connection testing, worker creation, tool-status loading, and agent config.
///
/// The widget [_SetupWizardState] holds only UI-lifecycle objects
/// (PageController, TextEditingControllers, GlobalKey) and delegates every
/// business operation here.
library;

import 'dart:async';
import 'dart:io' as io;

import 'package:flutter/foundation.dart';

import '../models/worker_config.dart';
import '../services/server_url.dart';
import '../state/app_state.dart';

// ---------------------------------------------------------------------------
// Enums
// ---------------------------------------------------------------------------

enum SetupTestStatus { idle, testing, success, failure }

// ---------------------------------------------------------------------------
// SetupWizardViewModel
// ---------------------------------------------------------------------------

class SetupWizardViewModel extends ChangeNotifier {
  final AppState _appState;

  // --- Navigation ---

  int currentStep = 0;

  // --- Step 1: Worker connection form ---

  bool obscureKey = true;
  bool useSSL = false;
  bool allowSelfSigned = true;
  bool autoConnect = true;

  /// True once the user has attempted to submit step 1 (enables inline errors).
  bool submitted = false;

  SetupTestStatus testStatus = SetupTestStatus.idle;
  String testMessage = '';

  // --- Step 1: Connection result ---

  String? createdWorkerId;
  bool connecting = false;
  String? connectError;

  // --- Step 3: Agent / tool configuration ---

  String? defaultAgent;
  Map<String, dynamic>? tools;
  bool toolsLoading = false;
  String? toolsError;

  // ---------------------------------------------------------------------------
  // Constructor
  // ---------------------------------------------------------------------------

  SetupWizardViewModel(this._appState);

  // ---------------------------------------------------------------------------
  // Navigation helpers
  // ---------------------------------------------------------------------------

  void goToStep(int step) {
    currentStep = step;
    notifyListeners();
  }

  void markComplete() {
    _appState.settings.setupComplete = true;
  }

  void saveDefaultAgent() {
    if (createdWorkerId == null || defaultAgent == null) return;
    final configs = _appState.settings.workers;
    final idx = configs.indexWhere((w) => w.id == createdWorkerId);
    if (idx < 0) return;
    configs[idx].defaultAgent = defaultAgent;
    _appState.settings.workers = configs;
    _appState.updateWorker(configs[idx]);
  }

  // ---------------------------------------------------------------------------
  // Step 1: Test connection (no side effects — purely probes the server)
  // ---------------------------------------------------------------------------

  Future<void> testConnection({
    required String host,
    required String portStr,
    required String apiKey,
  }) async {
    if (host.isEmpty || portStr.isEmpty || apiKey.isEmpty) {
      testStatus = SetupTestStatus.failure;
      testMessage = 'Host, Port, and API Key are required';
      notifyListeners();
      return;
    }
    final port = int.tryParse(portStr);
    if (port == null || port < 1 || port > 65535) {
      testStatus = SetupTestStatus.failure;
      testMessage = 'Port must be between 1 and 65535';
      notifyListeners();
      return;
    }

    testStatus = SetupTestStatus.testing;
    testMessage = '';
    notifyListeners();

    final url = ServerUrl(
      rawHost: '$host:$port',
      apiKey: apiKey,
      secure: useSSL,
    );

    try {
      final httpClient = io.HttpClient();
      if (allowSelfSigned) {
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

      io.HttpClient? wsClient;
      if (useSSL && allowSelfSigned) {
        wsClient = io.HttpClient()
          ..badCertificateCallback = (cert, host, port) => true;
      }
      final wsInput = await io.WebSocket.connect(
        url.wsInputText().toString(),
        customClient: wsClient,
      ).timeout(const Duration(seconds: 8));
      unawaited(wsInput.close());

      io.HttpClient? wsClient2;
      if (useSSL && allowSelfSigned) {
        wsClient2 = io.HttpClient()
          ..badCertificateCallback = (cert, host, port) => true;
      }
      final wsOutput = await io.WebSocket.connect(
        url.wsOutputText().toString(),
        customClient: wsClient2,
      ).timeout(const Duration(seconds: 8));
      unawaited(wsOutput.close());

      testStatus = SetupTestStatus.success;
      testMessage = 'All checks passed';
      notifyListeners();
    } on TimeoutException {
      _setFailure('Connection timed out');
    } on io.SocketException catch (e) {
      _setFailure(e.message);
    } catch (e) {
      _setFailure(_shortenError(e.toString()));
    }
  }

  // ---------------------------------------------------------------------------
  // Step 1: Create worker and connect
  // ---------------------------------------------------------------------------

  /// Validates inputs, creates a [WorkerConfig], adds it to [AppState], and
  /// attempts to connect. Returns `true` if the connection succeeded.
  Future<bool> createAndConnect({
    required String name,
    required String host,
    required String portStr,
    required String apiKey,
  }) async {
    submitted = true;
    notifyListeners();

    if (name.isEmpty || host.isEmpty || portStr.isEmpty || apiKey.isEmpty) {
      return false;
    }
    final port = int.tryParse(portStr);
    if (port == null || port < 1 || port > 65535) return false;

    final config = WorkerConfig(
      id: WorkerConfig.generateId(),
      name: name,
      host: host,
      port: port,
      apiKey: apiKey,
      useSSL: useSSL,
      allowSelfSigned: allowSelfSigned,
      autoConnect: autoConnect,
      sortOrder: 0,
    );

    connecting = true;
    connectError = null;
    notifyListeners();

    _appState.addWorker(config);
    createdWorkerId = config.id;

    try {
      await _appState.connectWorker(config.id);
      final worker = _appState.getWorker(config.id);
      connecting = false;
      if (worker != null && worker.isConnected) {
        notifyListeners();
        return true;
      } else {
        connectError = 'Connection failed. You can retry or skip.';
        notifyListeners();
        return false;
      }
    } catch (e) {
      connecting = false;
      connectError = _shortenError(e.toString());
      notifyListeners();
      return false;
    }
  }

  // ---------------------------------------------------------------------------
  // Step 3: Load tool status from connected worker
  // ---------------------------------------------------------------------------

  Future<void> loadToolStatus() async {
    if (createdWorkerId == null) return;
    final worker = _appState.getWorker(createdWorkerId!);
    if (worker == null || !worker.isConnected) return;

    toolsLoading = true;
    toolsError = null;
    notifyListeners();

    try {
      final result = await worker.ws.fetchToolStatus();
      tools = result['tools'] as Map<String, dynamic>?;
      toolsLoading = false;
      notifyListeners();
    } catch (e) {
      toolsError = _shortenError(e.toString());
      toolsLoading = false;
      notifyListeners();
    }
  }

  // ---------------------------------------------------------------------------
  // Misc helpers
  // ---------------------------------------------------------------------------

  void setObscureKey(bool value) {
    obscureKey = value;
    notifyListeners();
  }

  void setUseSSL(bool value) {
    useSSL = value;
    notifyListeners();
  }

  void setAllowSelfSigned(bool value) {
    allowSelfSigned = value;
    notifyListeners();
  }

  void setAutoConnect(bool value) {
    autoConnect = value;
    notifyListeners();
  }

  void setDefaultAgent(String? agent) {
    defaultAgent = agent;
    notifyListeners();
  }

  void _setFailure(String message) {
    testStatus = SetupTestStatus.failure;
    testMessage = message;
    notifyListeners();
  }

  static String _shortenError(String raw) {
    var msg = raw
        .replaceFirst('Exception: ', '')
        .replaceFirst(RegExp(r'^.*?:\s*'), '');
    if (msg.length > 120) msg = '${msg.substring(0, 117)}...';
    return msg;
  }
}
