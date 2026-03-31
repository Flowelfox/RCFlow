/// Tests verifying that MAC/worker connection failures are routed to the
/// notification panel only and do NOT add error messages to the session pane.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/app_notification.dart';
import 'package:rcflowclient/models/worker_config.dart';
import 'package:rcflowclient/services/settings_service.dart';
import 'package:rcflowclient/state/app_state.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  group('Connection failure routing', () {
    testWidgets(
      'connectWorker failure posts notification and leaves session pane empty',
      (tester) async {
        SharedPreferences.setMockInitialValues({});
        final settings = SettingsService();
        await settings.init();
        final appState = AppState(settings: settings);
        addTearDown(appState.dispose);

        // Port 1 is privileged and always refused — failure is near-instant.
        final worker = WorkerConfig(
          id: WorkerConfig.generateId(),
          name: 'MAC',
          host: '127.0.0.1',
          port: 1,
          apiKey: 'test-key',
          autoConnect: false,
        );

        appState.addWorker(worker);
        await tester.runAsync(() => appState.connectWorker(worker.id));

        // The session pane must remain message-free — no error injected there.
        expect(
          appState.messages,
          isEmpty,
          reason: 'Connection errors must not be written into the session pane',
        );

        // A notification must have been shown with the failure details.
        final notifications = appState.notificationService.notifications;
        expect(
          notifications,
          isNotEmpty,
          reason: 'Connection error must appear as a notification',
        );
        expect(notifications.first.level, NotificationLevel.error);
        expect(notifications.first.title, 'Connection Failed');
        expect(
          notifications.first.body,
          contains('MAC'),
          reason: 'Notification body must identify the worker by name',
        );
      },
    );
  });
}
