import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:window_manager/window_manager.dart';

import 'models/worker_config.dart';
import 'services/foreground_service.dart';
import 'services/settings_service.dart';
import 'state/app_state.dart';
import 'theme.dart';
import 'ui/screens/home_screen.dart';

bool get _isDesktop =>
    Platform.isWindows || Platform.isLinux || Platform.isMacOS;

/// Migrate legacy single-server settings to the workers model.
void _migrateToWorkers(SettingsService settings) {
  if (settings.workers.isNotEmpty) return;

  final apiKey = settings.apiKey;
  if (apiKey.isEmpty) return;

  final worker = WorkerConfig(
    id: WorkerConfig.generateId(),
    name: 'My Server',
    host: settings.host,
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

    const windowOptions = WindowOptions(
      minimumSize: Size(800, 500),
      title: 'RCFlow',
      titleBarStyle: TitleBarStyle.hidden,
      windowButtonVisibility: false,
    );

    windowManager.waitUntilReadyToShow(windowOptions, () async {
      await windowManager.show();
      await windowManager.focus();
    });
  }

  ForegroundServiceHelper.init();

  final settings = SettingsService();
  await settings.init();

  _migrateToWorkers(settings);

  runApp(
    ChangeNotifierProvider(
      create: (_) => AppState(settings: settings),
      child: const RCFlowApp(),
    ),
  );
}

class RCFlowApp extends StatelessWidget {
  const RCFlowApp({super.key});

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
