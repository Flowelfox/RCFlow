import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:package_info_plus/package_info_plus.dart';
import 'package:provider/provider.dart';
import 'package:window_manager/window_manager.dart';

import 'models/worker_config.dart';
import 'services/deep_link_service.dart';
import 'services/foreground_service.dart';
import 'services/settings_service.dart';
import 'state/app_state.dart';
import 'theme.dart';
import 'ui/dialogs/worker_edit_dialog.dart';
import 'ui/badges/badge_registry.dart';
import 'ui/badges/renderers/agent_badge_renderer.dart';
import 'ui/badges/renderers/caveman_badge_renderer.dart';
import 'ui/badges/renderers/project_badge_renderer.dart';
import 'ui/badges/renderers/status_badge_renderer.dart';
import 'ui/badges/renderers/worker_badge_renderer.dart';
import 'ui/badges/renderers/worktree_badge_renderer.dart';
import 'ui/screens/android_shell.dart';
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

  // Bridge for the Windows runner's external-input intercepts. Two methods:
  //  - "paste": fired on WM_PASTE arrival (accessibility tools that paste via
  //    the standard message). Routes through the existing clipboard-read path.
  //  - "clipboard_changed": fired on WM_CLIPBOARDUPDATE while RCFlow is the
  //    foreground window. Carries the captured text payload so we can insert
  //    Wispr Flow's dictation result even though its TSF injection itself
  //    doesn't reach Flutter's TextField. Deduped against our own copies.
  if (Platform.isWindows) {
    const channel = MethodChannel('rcflow/external_paste');
    channel.setMethodCallHandler((call) async {
      switch (call.method) {
        case 'paste':
          appState.requestPasteToInput();
        case 'clipboard_changed':
          final args = call.arguments;
          if (args is! Map) break;
          final text = args['text'] as String?;
          if (text == null || text.isEmpty) break;
          final previousText = args['previousText'] as String?;
          final isOwn = args['isOwn'] as bool? ?? false;
          final isForeground = args['isForeground'] as bool? ?? false;
          final seqJumped = args['seqJumped'] as bool? ?? false;
          appState.handleClipboardEvent(
            text: text,
            previousText: previousText,
            isOwn: isOwn,
            isForeground: isForeground,
            seqJumped: seqJumped,
          );
      }
      return null;
    });
  }

  // Register the rcflow:// URL scheme handler before runApp so cold-start
  // links are captured. Warm links arrive via the stream subscription in
  // _RCFlowAppState.
  await DeepLinkService.instance.init();

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

  /// Navigator key so deep-link handlers can push dialogs without holding a
  /// widget BuildContext.
  final GlobalKey<NavigatorState> _navigatorKey = GlobalKey<NavigatorState>();

  StreamSubscription<AddWorkerLink>? _deepLinkSub;

  @override
  void initState() {
    super.initState();
    if (_isMobile) {
      WidgetsBinding.instance.addObserver(this);
    }
    // The service buffers cold-start links and replays them once the first
    // listener subscribes, so a single subscription covers both warm and cold
    // launches.
    _deepLinkSub =
        DeepLinkService.instance.stream.listen(_handleAddWorkerLink);
  }

  @override
  void dispose() {
    _hibernateTimer?.cancel();
    _deepLinkSub?.cancel();
    if (_isMobile) {
      WidgetsBinding.instance.removeObserver(this);
    }
    super.dispose();
  }

  Future<void> _handleAddWorkerLink(AddWorkerLink link) async {
    // The replayed cold-start link can arrive before MaterialApp has built
    // the navigator. Defer to the next frame so the dialog has somewhere to
    // attach.
    if (_navigatorKey.currentState == null) {
      if (!mounted) return;
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _handleAddWorkerLink(link);
      });
      return;
    }

    // Bring the desktop window forward so the dialog isn't hidden behind
    // the worker GUI the user clicked from.
    if (_isDesktop) {
      unawaited(windowManager.show());
      unawaited(windowManager.focus());
    }

    final navState = _navigatorKey.currentState;
    if (navState == null) return;
    final ctx = navState.context;
    final appState = ctx.read<AppState>();

    final existing = appState.findWorkerByHostPortToken(
      link.host,
      link.port,
      link.token,
    );
    if (existing != null) {
      await showDialog<void>(
        context: ctx,
        builder: (dialogCtx) => AlertDialog(
          title: const Text('Worker already added'),
          content: Text(
            "This worker is already in your list as '${existing.name}'.",
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogCtx).pop(),
              child: const Text('OK'),
            ),
          ],
        ),
      );
      return;
    }

    final seed = WorkerConfig(
      id: WorkerConfig.generateId(),
      name: link.name ?? '',
      host: link.host,
      port: link.port,
      apiKey: link.token,
      useSSL: link.ssl,
      sortOrder: appState.workerConfigs.length,
    );
    final created = await showWorkerEditDialog(
      ctx,
      prefilled: seed,
      sortOrder: seed.sortOrder,
    );
    if (created != null) {
      appState.addWorker(created);
    }
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
      navigatorKey: _navigatorKey,
      home: Platform.isAndroid ? const AndroidShell() : const HomeScreen(),
      debugShowCheckedModeBanner: false,
    );
  }
}
