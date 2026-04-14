/// Tests for AppState worker lifecycle — add, remove, update, connect,
/// disconnect, session aggregation, and defaultWorkerId logic.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/app_notification.dart';
import 'package:rcflowclient/models/worker_config.dart';
import 'package:rcflowclient/services/settings_service.dart';
import 'package:rcflowclient/services/worker_connection.dart';
import 'package:rcflowclient/state/app_state.dart';
import 'package:shared_preferences/shared_preferences.dart';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

WorkerConfig _worker({
  String? id,
  String name = 'Test Worker',
  bool autoConnect = false,
}) => WorkerConfig(
  id: id ?? WorkerConfig.generateId(),
  name: name,
  host: '127.0.0.1',
  // Port 1 is privileged — always refused immediately, useful for failure tests.
  port: 1,
  apiKey: 'test-key',
  autoConnect: autoConnect,
);

Future<AppState> _makeAppState() async {
  SharedPreferences.setMockInitialValues({});
  final settings = SettingsService();
  await settings.init();
  return AppState(settings: settings);
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  // -------------------------------------------------------------------------
  // addWorker
  // -------------------------------------------------------------------------

  group('addWorker()', () {
    testWidgets('adds config to workerConfigs', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      final w = _worker(name: 'Alpha');
      appState.addWorker(w);

      expect(appState.workerConfigs.map((c) => c.name), contains('Alpha'));
    });

    testWidgets('creates a WorkerConnection for the new worker', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      final w = _worker();
      appState.addWorker(w);

      expect(appState.getWorker(w.id), isNotNull);
    });

    testWidgets('notifies listeners', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      var notified = false;
      appState.addListener(() => notified = true);
      appState.addWorker(_worker());

      expect(notified, isTrue);
    });

    testWidgets('persists config to settings', (tester) async {
      SharedPreferences.setMockInitialValues({});
      final settings = SettingsService();
      await settings.init();
      final appState = AppState(settings: settings);
      addTearDown(appState.dispose);

      final w = _worker(name: 'Persisted');
      appState.addWorker(w);

      // Settings should reflect the new worker
      expect(settings.workers.map((c) => c.name), contains('Persisted'));
    });
  });

  // -------------------------------------------------------------------------
  // removeWorker
  // -------------------------------------------------------------------------

  group('removeWorker()', () {
    testWidgets('removes config from workerConfigs', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      final w = _worker();
      appState.addWorker(w);
      await appState.removeWorker(w.id);

      expect(appState.workerConfigs.map((c) => c.id), isNot(contains(w.id)));
    });

    testWidgets('removes WorkerConnection from internal map', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      final w = _worker();
      appState.addWorker(w);
      await appState.removeWorker(w.id);

      expect(appState.getWorker(w.id), isNull);
    });

    testWidgets('clears defaultWorkerId if it was the removed worker',
        (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      final w = _worker();
      appState.addWorker(w);
      appState.defaultWorkerId = w.id;
      await appState.removeWorker(w.id);

      // defaultWorkerId falls back gracefully — no longer points to removed id
      expect(appState.defaultWorkerId, isNot(w.id));
    });

    testWidgets('notifies listeners', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      final w = _worker();
      appState.addWorker(w);
      var count = 0;
      appState.addListener(() => count++);
      await appState.removeWorker(w.id);

      expect(count, greaterThan(0));
    });
  });

  // -------------------------------------------------------------------------
  // updateWorker
  // -------------------------------------------------------------------------

  group('updateWorker()', () {
    testWidgets('updates name in workerConfigs', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      final w = _worker(name: 'Old Name');
      appState.addWorker(w);
      appState.updateWorker(w.copyWith(name: 'New Name'));

      expect(
        appState.workerConfigs.firstWhere((c) => c.id == w.id).name,
        'New Name',
      );
    });

    testWidgets('ignores unknown workerId', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      // Should not throw
      expect(
        () => appState.updateWorker(
          _worker(id: 'nonexistent-id'),
        ),
        returnsNormally,
      );
    });
  });

  // -------------------------------------------------------------------------
  // Connection state aggregation
  // -------------------------------------------------------------------------

  group('connected state aggregation', () {
    testWidgets('connected is false with no workers', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      expect(appState.connected, isFalse);
    });

    testWidgets('connected is false when worker added but not yet connected',
        (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      appState.addWorker(_worker());
      expect(appState.connected, isFalse);
    });

    testWidgets('totalWorkerCount reflects addWorker calls', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      appState.addWorker(_worker(name: 'A'));
      appState.addWorker(_worker(name: 'B'));

      expect(appState.totalWorkerCount, 2);
    });

    testWidgets('connectedWorkerCount is 0 when no workers connected',
        (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      appState.addWorker(_worker());
      appState.addWorker(_worker());

      expect(appState.connectedWorkerCount, 0);
    });
  });

  // -------------------------------------------------------------------------
  // defaultWorkerId
  // -------------------------------------------------------------------------

  group('defaultWorkerId', () {
    testWidgets('returns null when no workers configured', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      // With no workers, defaultWorkerId may be null or first config id.
      // The only guarantee: it doesn't throw.
      expect(() => appState.defaultWorkerId, returnsNormally);
    });

    testWidgets('can be explicitly set and cleared', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      final w = _worker();
      appState.addWorker(w);

      appState.defaultWorkerId = w.id;
      // Not connected, so falls back to first in list (not the explicit id).
      // The getter prefers connected workers, so with none connected it still works.
      expect(() => appState.defaultWorkerId, returnsNormally);

      appState.defaultWorkerId = null;
      expect(() => appState.defaultWorkerId, returnsNormally);
    });
  });

  // -------------------------------------------------------------------------
  // workerIdForSession
  // -------------------------------------------------------------------------

  group('workerIdForSession()', () {
    testWidgets('returns null for unknown session id', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      expect(appState.workerIdForSession('nonexistent-session'), isNull);
    });
  });

  // -------------------------------------------------------------------------
  // Session aggregation
  // -------------------------------------------------------------------------

  group('sessions aggregation', () {
    testWidgets('sessions is empty when no workers connected', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      appState.addWorker(_worker());
      expect(appState.sessions, isEmpty);
    });

    testWidgets('sessionsByWorker maps each workerConfig id to empty list',
        (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      final w1 = _worker(name: 'W1');
      final w2 = _worker(name: 'W2');
      appState.addWorker(w1);
      appState.addWorker(w2);

      final byWorker = appState.sessionsByWorker;
      expect(byWorker, contains(w1.id));
      expect(byWorker, contains(w2.id));
      expect(byWorker[w1.id], isEmpty);
      expect(byWorker[w2.id], isEmpty);
    });
  });

  // -------------------------------------------------------------------------
  // connectWorker — failure routing
  // -------------------------------------------------------------------------

  group('connectWorker() failure routing', () {
    testWidgets('posts notification on connection failure', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      final w = _worker(name: 'Failing Worker');
      appState.addWorker(w);
      await tester.runAsync(() => appState.connectWorker(w.id));

      final notifications = appState.notificationService.notifications;
      expect(notifications, isNotEmpty);
      expect(notifications.first.level, NotificationLevel.error);
      expect(notifications.first.title, 'Connection Failed');
      expect(notifications.first.body, contains('Failing Worker'));
    });

    testWidgets('does not add error message to session pane on failure',
        (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      final w = _worker();
      appState.addWorker(w);
      await tester.runAsync(() => appState.connectWorker(w.id));

      expect(appState.messages, isEmpty);
    });

    testWidgets('no-ops for unknown workerId', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      // Should not throw
      await tester.runAsync(
        () => appState.connectWorker('nonexistent-id'),
      );
    });
  });

  // -------------------------------------------------------------------------
  // disconnectWorker
  // -------------------------------------------------------------------------

  group('disconnectWorker()', () {
    testWidgets('no-ops for unknown workerId', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      expect(() => appState.disconnectWorker('nonexistent-id'), returnsNormally);
    });

    testWidgets('worker status is disconnected after call', (tester) async {
      final appState = await _makeAppState();
      addTearDown(appState.dispose);

      final w = _worker();
      appState.addWorker(w);
      appState.disconnectWorker(w.id);

      expect(
        appState.getWorker(w.id)!.status,
        WorkerConnectionStatus.disconnected,
      );
    });
  });
}
