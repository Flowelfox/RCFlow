import 'dart:io' show Platform;

import 'package:flutter_foreground_task/flutter_foreground_task.dart';

/// Whether we're on a platform that supports foreground services.
bool get _isMobile => Platform.isAndroid || Platform.isIOS;

/// Top-level callback required by flutter_foreground_task.
/// Called when the service isolate starts — we just wire up the handler.
@pragma('vm:entry-point')
void _startCallback() {
  try {
    FlutterForegroundTask.setTaskHandler(_KeepAliveTaskHandler());
  } catch (_) {
    // Silently ignore on unsupported platforms.
  }
}

/// Manages an Android foreground service to keep the process (and its
/// WebSocket connection) alive when the screen is off / app is backgrounded.
///
/// The service itself does no work — it exists solely to hold a wake lock
/// and prevent Doze mode from killing the process.
///
/// On desktop platforms, all methods are no-ops.
class ForegroundServiceHelper {
  ForegroundServiceHelper._();

  /// Call once before [runApp] to configure the foreground task plugin.
  static void init() {
    if (!_isMobile) return;
    FlutterForegroundTask.init(
      androidNotificationOptions: AndroidNotificationOptions(
        channelId: 'rcflow_foreground',
        channelName: 'RCFlow Connection',
        channelDescription: 'Keeps the server connection alive',
        channelImportance: NotificationChannelImportance.LOW,
        priority: NotificationPriority.LOW,
      ),
      iosNotificationOptions: const IOSNotificationOptions(),
      foregroundTaskOptions: ForegroundTaskOptions(
        eventAction: ForegroundTaskEventAction.repeat(5 * 60 * 1000),
        autoRunOnBoot: false,
        autoRunOnMyPackageReplaced: false,
        allowWakeLock: true,
        allowWifiLock: true,
      ),
    );
  }

  /// Start the foreground service (shows persistent notification).
  static Future<void> start() async {
    if (!_isMobile) return;
    if (await FlutterForegroundTask.isRunningService) return;
    await FlutterForegroundTask.startService(
      notificationTitle: 'RCFlow',
      notificationText: 'Connected to server',
      callback: _startCallback,
    );
  }

  /// Stop the foreground service (removes notification).
  static Future<void> stop() async {
    if (!_isMobile) return;
    if (!await FlutterForegroundTask.isRunningService) return;
    await FlutterForegroundTask.stopService();
  }
}

/// No-op task handler — the package requires one but we don't need
/// periodic work. The WebSocket runs in the main isolate.
class _KeepAliveTaskHandler extends TaskHandler {
  @override
  Future<void> onStart(DateTime timestamp, TaskStarter starter) async {}

  @override
  void onRepeatEvent(DateTime timestamp) {}

  @override
  Future<void> onDestroy(DateTime timestamp, bool isTimeout) async {}
}
