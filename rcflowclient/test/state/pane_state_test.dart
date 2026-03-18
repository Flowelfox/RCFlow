/// Tests for [PaneState.switchSession] project-panel auto-open behaviour.
///
/// When a session already has a [SessionInfo.mainProjectPath] (i.e. the user
/// used @ProjectName at least once), switching to that session must auto-open
/// the project panel — even if the session is ended, paused, or cancelled.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/app_notification.dart';
import 'package:rcflowclient/models/session_info.dart';
import 'package:rcflowclient/services/websocket_service.dart';
import 'package:rcflowclient/state/pane_state.dart';

// ---------------------------------------------------------------------------
// Minimal PaneHost stub
// ---------------------------------------------------------------------------

class _StubPaneHost implements PaneHost {
  final List<SessionInfo> _sessions;
  _StubPaneHost(this._sessions);

  @override
  bool get connected => false;

  @override
  List<SessionInfo> get sessions => _sessions;

  @override
  // WebSocketService.subscribe() guards against null channel so it is safe
  // to return a disconnected instance; no actual network call is made.
  WebSocketService wsForWorker(String workerId) => WebSocketService();

  @override
  String? workerIdForSession(String sessionId) => 'worker1';

  @override
  String? get defaultWorkerId => 'worker1';

  @override
  void refreshSessions() {}

  @override
  void addSystemMessageToPane(String paneId, String text,
      {bool isError = false, String? label}) {}

  @override
  void muteSessionSound(String sessionId) {}

  @override
  void markSubscribed(String sessionId, {required String workerId}) {}

  @override
  void requestUnsubscribe(String sessionId, String workerId) {}

  @override
  void showNotification(
      {required NotificationLevel level,
      required String title,
      String? body}) {}

  @override
  bool workerSupportsAttachments(String? workerId) => false;

  @override
  bool workerSupportsImageAttachments(String? workerId) => false;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

SessionInfo _session(
  String id,
  String status, {
  String? mainProjectPath,
}) =>
    SessionInfo(
      sessionId: id,
      sessionType: 'conversational',
      status: status,
      workerId: 'worker1',
      mainProjectPath: mainProjectPath,
    );

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  group('PaneState.switchSession — project panel auto-open', () {
    test('opens project panel for active session with mainProjectPath', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([
          _session('s1', 'active',
              mainProjectPath: '/home/user/Projects/MyApp'),
        ]),
      );

      pane.switchSession('s1');

      expect(pane.activeRightPanel, 'project');
    });

    test('opens project panel for paused session with mainProjectPath', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([
          _session('s1', 'paused',
              mainProjectPath: '/home/user/Projects/MyApp'),
        ]),
      );

      pane.switchSession('s1');

      expect(pane.activeRightPanel, 'project');
    });

    test('opens project panel for completed (ended) session with mainProjectPath',
        () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([
          _session('s1', 'completed',
              mainProjectPath: '/home/user/Projects/MyApp'),
        ]),
      );

      pane.switchSession('s1');

      expect(pane.activeRightPanel, 'project');
    });

    test('opens project panel for cancelled session with mainProjectPath', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([
          _session('s1', 'cancelled',
              mainProjectPath: '/home/user/Projects/MyApp'),
        ]),
      );

      pane.switchSession('s1');

      expect(pane.activeRightPanel, 'project');
    });

    test('does not open project panel when session has no mainProjectPath', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([
          _session('s1', 'completed'),
        ]),
      );

      pane.switchSession('s1');

      expect(pane.activeRightPanel, isNull);
    });

    test('restores project panel when switching between sessions', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([
          _session('s1', 'completed',
              mainProjectPath: '/home/user/Projects/Alpha'),
          _session('s2', 'completed'), // no project
          _session('s3', 'completed',
              mainProjectPath: '/home/user/Projects/Beta'),
        ]),
      );

      pane.switchSession('s1');
      expect(pane.activeRightPanel, 'project',
          reason: 's1 has a project — panel should open');

      pane.switchSession('s2');
      expect(pane.activeRightPanel, isNull,
          reason: 's2 has no project — panel should stay closed');

      pane.switchSession('s3');
      expect(pane.activeRightPanel, 'project',
          reason: 's3 has a project — panel should reopen');
    });
  });

  // ---------------------------------------------------------------------------
  // toggleRightPanel / openProjectPanel — close-and-reopen cycle
  // ---------------------------------------------------------------------------

  group('PaneState — project panel close and reopen', () {
    PaneState _paneWithProject() => PaneState(
          paneId: 'p1',
          host: _StubPaneHost([
            _session('s1', 'active',
                mainProjectPath: '/home/user/Projects/MyApp'),
          ]),
        )..switchSession('s1');

    test('toggleRightPanel closes an open project panel', () {
      final pane = _paneWithProject();
      expect(pane.activeRightPanel, 'project');

      pane.toggleRightPanel('project');

      expect(pane.activeRightPanel, isNull);
    });

    test('toggleRightPanel reopens a closed project panel', () {
      final pane = _paneWithProject();
      pane.toggleRightPanel('project'); // close
      expect(pane.activeRightPanel, isNull);

      pane.toggleRightPanel('project'); // reopen

      expect(pane.activeRightPanel, 'project');
    });

    test('openProjectPanel opens a closed panel', () {
      final pane = _paneWithProject();
      pane.toggleRightPanel('project'); // close
      expect(pane.activeRightPanel, isNull);

      pane.openProjectPanel();

      expect(pane.activeRightPanel, 'project');
    });

    test('openProjectPanel is a no-op when panel is already open', () {
      final pane = _paneWithProject();
      var notifyCount = 0;
      pane.addListener(() => notifyCount++);

      pane.openProjectPanel(); // already open — should not notify

      expect(pane.activeRightPanel, 'project');
      expect(notifyCount, 0, reason: 'no rebuild needed when panel already open');
    });

    test('closing then switching back to a project session reopens panel', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([
          _session('s1', 'active',
              mainProjectPath: '/home/user/Projects/MyApp'),
          _session('s2', 'active'), // no project
        ]),
      );

      pane.switchSession('s1');
      expect(pane.activeRightPanel, 'project');

      pane.toggleRightPanel('project'); // user manually closes panel
      expect(pane.activeRightPanel, isNull);

      pane.switchSession('s2'); // navigate away
      expect(pane.activeRightPanel, isNull);

      pane.switchSession('s1'); // return to project session

      // Auto-open fires again because switchSession re-evaluates mainProjectPath
      expect(pane.activeRightPanel, 'project',
          reason: 'panel should reopen when switching back to a project session');
    });
  });
}
