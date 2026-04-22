import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:provider/provider.dart';
import 'package:window_manager/window_manager.dart';

import 'models/worker_config.dart';
import 'services/foreground_service.dart';
import 'services/settings_service.dart';
import 'state/app_state.dart';
import 'theme.dart';
import 'ui/badges/badge_registry.dart';
import 'ui/badges/renderers/agent_badge_renderer.dart';
import 'ui/badges/renderers/caveman_badge_renderer.dart';
import 'ui/badges/renderers/project_badge_renderer.dart';
import 'ui/badges/renderers/status_badge_renderer.dart';
import 'ui/badges/renderers/worker_badge_renderer.dart';
import 'ui/badges/renderers/worktree_badge_renderer.dart';
import 'ui/screens/home_screen.dart';

bool get _isDesktop =>
    Platform.isWindows || Platform.isLinux || Platform.isMacOS;

bool get _isMobile => Platform.isAndroid || Platform.isIOS;

/// How long the app must stay backgrounded before worker WebSockets are
/// torn down. Short app switches (reading a notification, answering a
/// quick call) should not thrash the connection.
const Duration _backgroundHibernateDelay = Duration(seconds: 30);

void _registerBadges(BadgeRegistry registry) {
  registerStatusBadge(registry);
  registerWorkerBadge(registry);
  registerAgentBadge(registry);
  registerCavemanBadge(registry);
  registerProjectBadge(registry);
  registerWorktreeBadge(registry);
}

/// Migrate legacy single-server settings to the workers model.
void _migrateToWorkers(SettingsService settings) {
  if (settings.workers.isNotEmpty) return;

  final apiKey = settings.apiKey;
  if (apiKey.isEmpty) return;

  // Legacy host may contain port (e.g. "192.168.1.100:8765")
  final legacyHost = settings.host;
  String host;
  int port;
  if (legacyHost.contains(':')) {
    final parts = legacyHost.split(':');
    host = parts[0];
    port = int.tryParse(parts[1]) ?? 53890;
  } else {
    host = legacyHost;
    port = 53890;
  }

  final worker = WorkerConfig(
    id: WorkerConfig.generateId(),
    name: 'My Server',
    host: host,
    port: port,
    apiKey: apiKey,
    useSSL: settings.useSSL,
    autoConnect: true,
    sortOrder: 0,
  );
  settings.workers = [worker];

  // Migrate last session
  final lastSession = settings.lastSessionId;
  if (lastSession != null) {
    settings.setLastSessionId(worker.id, lastSession);
  }

  // Migrate cached sessions
  final cached = settings.cachedSessions;
  if (cached != null) {
    settings.setCachedSessions(worker.id, cached);
  }
}

void main() async {
  WidgetsFlutterBinding.ensureInitialized();

  if (_isDesktop) {
    await windowManager.ensureInitialized();

    final windowOptions = WindowOptions(
      minimumSize: Size(800, 500),
      title: 'RCFlow',
      titleBarStyle: TitleBarStyle.hidden,
      windowButtonVisibility: Platform.isMacOS,
    );

    windowManager.waitUntilReadyToShow(windowOptions, () async {
      await windowManager.show();
      await windowManager.focus();
    });
  }

  ForegroundServiceHelper.init();

  // Register badge renderers once at startup.
  _registerBadges(BadgeRegistry.instance);

  final settings = SettingsService();
  await settings.init();

  // Persist the running version (strip build number after '+') so
  // UpdateService can compare it against the latest release without an
  // async PackageInfo call at runtime.
  final packageInfo = await PackageInfo.fromPlatform();
  settings.currentVersion = packageInfo.version.split('+').first;

  _migrateToWorkers(settings);

  // Existing users (who already have workers configured) should not see the
  // setup wizard. Mark setup as complete for them.
  if (settings.workers.isNotEmpty && !settings.setupComplete) {
    settings.setupComplete = true;
    settings.onboardingComplete = true;
  }

  final appState = AppState(settings: settings);
  // Fire the first update-check (non-blocking, fire-and-forget).
  // ignore: unawaited_futures
  appState.initAsync();

  runApp(
    ChangeNotifierProvider<AppState>.value(
      value: appState,
      child: const RCFlowApp(),
    ),
  );
}

class RCFlowApp extends StatefulWidget {
  const RCFlowApp({super.key});

  @override
  State<RCFlowApp> createState() => _RCFlowAppState();
}

class _RCFlowAppState extends State<RCFlowApp> with WidgetsBindingObserver {
  /// Fires [_backgroundHibernateDelay] after the app goes to background.
  /// Cancelled when the app resumes before the delay elapses.
  Timer? _hibernateTimer;

  /// True once [_hibernateTimer] has fired and [AppState.hibernateForBackground]
  /// has been called. Guards [AppState.wakeFromBackground] so spurious
  /// `resumed` events (e.g. at cold start) do not trigger a pointless wake.
  bool _hibernated = false;

  @override
  void initState() {
    super.initState();
    if (_isMobile) {
      WidgetsBinding.instance.addObserver(this);
    }
  }

  @override
  void dispose() {
    _hibernateTimer?.cancel();
    if (_isMobile) {
      WidgetsBinding.instance.removeObserver(this);
    }
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (!_isMobile) return;
    switch (state) {
      case AppLifecycleState.paused:
      case AppLifecycleState.hidden:
      case AppLifecycleState.detached:
        _hibernateTimer ??= Timer(_backgroundHibernateDelay, _runHibernate);
      case AppLifecycleState.resumed:
        _hibernateTimer?.cancel();
        _hibernateTimer = null;
        if (_hibernated) {
          _hibernated = false;
          // Fire-and-forget — wakeFromBackground swallows connect errors.
          unawaited(context.read<AppState>().wakeFromBackground());
        }
      case AppLifecycleState.inactive:
        // Transient state (dialog, incoming call, control-center pull on
        // iOS). Don't tear down or the UX will thrash.
        break;
    }
  }

  void _runHibernate() {
    _hibernateTimer = null;
    if (!mounted) return;
    _hibernated = true;
    context.read<AppState>().hibernateForBackground();
  }

  @override
  Widget build(BuildContext context) {
    final appState = context.watch<AppState>();
    final themeMode = switch (appState.settings.themeMode) {
      'light' => ThemeMode.light,
      'dark' => ThemeMode.dark,
      _ => ThemeMode.system,
    };

    return MaterialApp(
      title: 'RCFlow',
      theme: buildLightTheme(),
      darkTheme: buildDarkTheme(),
      themeMode: themeMode,
      home: const HomeScreen(),
      debugShowCheckedModeBanner: false,
    );
  }
}
