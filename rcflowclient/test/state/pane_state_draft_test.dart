/// Tests for PaneState draft persistence behaviour.
///
/// Covers:
/// - registerDraftProvider / unregisterDraftProvider lifecycle
/// - triggerDraftSave saves when text differs from _lastLoadedDraft
/// - triggerDraftSave skips when text matches _lastLoadedDraft (multi-pane guard)
/// - Key routing: session ID for real sessions, "new_{workerId}" for new-session pane
/// - No save when neither _sessionId nor _workerId is set
/// - handleAck clears "new_{workerId}" draft and resets _lastLoadedDraft on new session
/// - handleAck does NOT clear draft when re-acking an existing session
/// - goHome saves current draft before navigation
/// - startNewChat saves current draft before navigation
/// - switchSession saves the outgoing draft and then loads the incoming one
/// - _loadNewSessionDraftAsync restores draft via setPendingInputText
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/app_notification.dart';
import 'package:rcflowclient/models/session_info.dart';
import 'package:rcflowclient/services/websocket_service.dart';
import 'package:rcflowclient/state/pane_state.dart';

// ---------------------------------------------------------------------------
// Recording PaneHost stub
// ---------------------------------------------------------------------------

/// PaneHost that records every saveDraft / clearDraft call and lets tests
/// pre-populate the draft store so getDraft returns controlled values.
class _RecordingPaneHost implements PaneHost {
  final List<({String key, String content})> savedDrafts = [];
  final List<String> clearedDrafts = [];

  /// Seed this before a test to control what getDraft returns.
  final Map<String, ({String content, DateTime? cachedAt})> store = {};

  final List<SessionInfo> _sessions;

  _RecordingPaneHost([this._sessions = const []]);

  // --- Draft ---

  @override
  ({String content, DateTime? cachedAt}) getDraft(String key) =>
      store[key] ?? (content: '', cachedAt: null);

  @override
  void saveDraft(String key, String content) {
    savedDrafts.add((key: key, content: content));
    store[key] = (content: content, cachedAt: DateTime.now());
  }

  @override
  void clearDraft(String key) => clearedDrafts.add(key);

  @override
  Map<String, dynamic>? getDraftPlucks(String key) => null;

  @override
  void saveDraftPlucks(String key, Map<String, dynamic> plucks) {}

  @override
  void clearDraftPlucks(String key) {}

  // --- PaneHost boilerplate ---

  @override
  bool get connected => false;

  @override
  List<SessionInfo> get sessions => _sessions;

  @override
  WebSocketService wsForWorker(String workerId) => WebSocketService();

  @override
  String? workerIdForSession(String sessionId) => 'worker1';

  @override
  String? get defaultWorkerId => 'worker1';

  @override
  void refreshSessions() {}

  @override
  void addSystemMessageToPane(
    String paneId,
    String text, {
    bool isError = false,
    String? label,
  }) {}

  @override
  void markSubscribed(String sessionId, {required String workerId}) {}

  @override
  void requestUnsubscribe(String sessionId, String workerId) {}

  @override
  void showNotification({
    required NotificationLevel level,
    required String title,
    String? body,
  }) {}

  @override
  bool workerSupportsAttachments(String? workerId) => false;

  @override
  bool workerSupportsImageAttachments(String? workerId) => false;

  @override
  String? defaultAgentForWorker(String? workerId) => null;

  @override
  String? getLastProjectForWorker(String workerId) => null;

  @override
  String? getLastAgentForWorker(String workerId) => null;

  @override
  Future<String?> resolveProjectOnWorker(
    String workerId,
    String projectName,
  ) async => null;

  @override
  bool isWorkerCavemanActive(String? workerId) => false;

  @override
  SessionInfo? sessionById(String sessionId) {
    try {
      return _sessions.firstWhere((s) => s.sessionId == sessionId);
    } catch (_) {
      return null;
    }
  }

  @override
  void muteSessionSound(String sessionId) {}
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Build a PaneState that has an active session (sessionId + workerId set).
PaneState _paneWithSession(
  _RecordingPaneHost host, {
  String sessionId = 'session-abc',
  String workerId = 'worker1',
}) {
  final pane = PaneState(paneId: 'p1', host: host);
  // handleAck sets _sessionId and _workerId, simulating a connected session.
  pane.handleAck(sessionId, workerId: workerId);
  return pane;
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  // -------------------------------------------------------------------------
  // registerDraftProvider / unregisterDraftProvider
  // -------------------------------------------------------------------------

  group('registerDraftProvider / unregisterDraftProvider', () {
    test('provider is called when triggerDraftSave is invoked', () {
      final host = _RecordingPaneHost();
      final pane = _paneWithSession(host);
      var calls = 0;
      pane.registerDraftProvider(() {
        calls++;
        return 'typed text';
      });

      pane.triggerDraftSave();

      expect(calls, 1);
    });

    test('unregisterDraftProvider prevents provider from being called', () {
      final host = _RecordingPaneHost();
      final pane = _paneWithSession(host);
      var calls = 0;
      pane.registerDraftProvider(() {
        calls++;
        return 'text';
      });

      pane.unregisterDraftProvider();
      pane.triggerDraftSave();

      // Provider must not be invoked after unregistration.
      expect(calls, 0);
    });
  });

  // -------------------------------------------------------------------------
  // triggerDraftSave — save/skip logic
  // -------------------------------------------------------------------------

  group('triggerDraftSave', () {
    test('saves draft when text differs from lastLoadedDraft', () {
      final host = _RecordingPaneHost();
      final pane = _paneWithSession(host, sessionId: 'session-1');
      pane.registerDraftProvider(() => 'new text');

      pane.triggerDraftSave();

      expect(host.savedDrafts, hasLength(1));
      expect(host.savedDrafts.first.content, 'new text');
    });

    test('skips save when text equals lastLoadedDraft (multi-pane guard)', () {
      final host = _RecordingPaneHost();
      final pane = _paneWithSession(host, sessionId: 'session-1');
      // After handleAck, _lastLoadedDraft == ''. Provider also returns '' →
      // text == _lastLoadedDraft → skip.
      pane.registerDraftProvider(() => '');

      pane.triggerDraftSave();

      expect(host.savedDrafts, isEmpty);
    });

    test('uses session ID as key for a real session', () {
      final host = _RecordingPaneHost();
      final pane = _paneWithSession(host, sessionId: 'session-xyz');
      pane.registerDraftProvider(() => 'hello');

      pane.triggerDraftSave();

      expect(host.savedDrafts.first.key, 'session-xyz');
    });

    test('uses "new_{workerId}" key for new-session pane', () {
      final host = _RecordingPaneHost();
      // New-session pane: no session ID, but workerId is default ('worker1').
      // We need to put the pane in new-session state. goHome resets to no
      // session. We start fresh (no handleAck) and let _workerId be set via
      // the host's defaultWorkerId — which _saveDraftIfChanged falls back to.
      //
      // However, _workerId is only set if explicitly passed or via handleAck.
      // Use a pane that had a session, went home (clearing _sessionId) but
      // keeping _workerId.
      final pane = PaneState(paneId: 'p1', host: host);
      pane.handleAck('old-session', workerId: 'worker1');
      // goHome clears _sessionId but preserves _workerId.
      pane.goHome();
      pane.registerDraftProvider(() => 'new-session draft');

      pane.triggerDraftSave();

      expect(host.savedDrafts, isNotEmpty);
      final saved = host.savedDrafts.last;
      expect(saved.key, 'new_worker1');
      expect(saved.content, 'new-session draft');
    });

    test('does not save when no session and no workerId', () {
      // Build a bare pane with no workerId via defaultWorkerId=null.
      final noWorkerHost = _RecordingPaneHostNoWorker();
      final pane = PaneState(paneId: 'p1', host: noWorkerHost);
      pane.registerDraftProvider(() => 'something');

      pane.triggerDraftSave();

      expect(noWorkerHost.savedDrafts, isEmpty);
    });
  });

  // -------------------------------------------------------------------------
  // handleAck — new-session draft clearing
  // -------------------------------------------------------------------------

  group('handleAck', () {
    test('clears new-session draft on first ack (wasNewSession=true)', () {
      final host = _RecordingPaneHost();
      // Pre-populate local draft for the new-session pane key.
      host.store['new_worker1'] = (content: 'unsent draft', cachedAt: DateTime.now());

      final pane = PaneState(paneId: 'p1', host: host);
      // No previous ack → wasNewSession == true.
      pane.handleAck('session-new', workerId: 'worker1');

      expect(host.clearedDrafts, contains('new_worker1'));
    });

    test('does not clear draft on re-ack of existing session', () {
      final host = _RecordingPaneHost();
      final pane = _paneWithSession(host, sessionId: 'session-existing');
      host.clearedDrafts.clear(); // ignore the initial ack's clear

      // Second ack for the same session.
      pane.handleAck('session-existing', workerId: 'worker1');

      expect(host.clearedDrafts, isEmpty);
    });

    test('resets _lastLoadedDraft to empty string after clearing', () {
      final host = _RecordingPaneHost();
      final pane = PaneState(paneId: 'p1', host: host);
      // Simulate that _lastLoadedDraft had some value before the ack by
      // registering a provider that asserts it read ''.
      pane.handleAck('session-new', workerId: 'worker1');

      // After ack, the multi-pane guard is reset: text == '' == lastLoadedDraft
      // so a triggerDraftSave with an empty provider must NOT save.
      pane.registerDraftProvider(() => '');
      pane.triggerDraftSave();

      expect(host.savedDrafts, isEmpty);
    });
  });

  // -------------------------------------------------------------------------
  // Draft save on navigation (goHome, startNewChat, switchSession)
  // -------------------------------------------------------------------------

  group('draft save on navigation', () {
    test('goHome saves draft before navigating away', () {
      final host = _RecordingPaneHost();
      final pane = _paneWithSession(host, sessionId: 'session-1');
      pane.registerDraftProvider(() => 'going home with this');

      pane.goHome();

      expect(
        host.savedDrafts.any(
          (d) => d.key == 'session-1' && d.content == 'going home with this',
        ),
        isTrue,
      );
    });

    test('startNewChat saves draft before resetting state', () {
      final host = _RecordingPaneHost();
      final pane = _paneWithSession(host, sessionId: 'session-1');
      pane.registerDraftProvider(() => 'mid-draft when new chat pressed');

      pane.startNewChat();

      expect(
        host.savedDrafts.any(
          (d) =>
              d.key == 'session-1' &&
              d.content == 'mid-draft when new chat pressed',
        ),
        isTrue,
      );
    });

    test('switchSession saves outgoing draft before switching', () {
      final host = _RecordingPaneHost([
        _session('session-2', 'active'),
      ]);
      final pane = _paneWithSession(host, sessionId: 'session-1');
      pane.registerDraftProvider(() => 'outgoing draft');

      pane.switchSession('session-2');

      expect(
        host.savedDrafts.any(
          (d) => d.key == 'session-1' && d.content == 'outgoing draft',
        ),
        isTrue,
      );
    });

    test('switchSession does not save if text matches lastLoadedDraft', () {
      final host = _RecordingPaneHost([
        _session('session-2', 'active'),
      ]);
      final pane = _paneWithSession(host, sessionId: 'session-1');
      // Provider returns same value as _lastLoadedDraft (both '').
      pane.registerDraftProvider(() => '');

      pane.switchSession('session-2');

      // The only saves that could happen are from the load path; none from
      // the pre-switch save since '' == _lastLoadedDraft ('').
      expect(
        host.savedDrafts.any((d) => d.key == 'session-1'),
        isFalse,
      );
    });
  });

  // -------------------------------------------------------------------------
  // _loadNewSessionDraftAsync — draft restoration
  // -------------------------------------------------------------------------

  group('draft restoration via _loadNewSessionDraftAsync', () {
    test('restores draft into pendingInputText for new-session pane', () async {
      final host = _RecordingPaneHost();
      host.store['new_worker1'] = (
        content: 'my saved draft',
        cachedAt: DateTime.now(),
      );

      final pane = PaneState(paneId: 'p1', host: host);
      pane.handleAck('prev-session', workerId: 'worker1');
      pane.goHome();

      // Allow the async _loadNewSessionDraftAsync to complete.
      await Future<void>.delayed(Duration.zero);

      expect(pane.pendingInputText, 'my saved draft');
    });

    test('does not set pendingInputText when stored draft is empty', () async {
      final host = _RecordingPaneHost();
      // No entry in store → getDraft returns ''.

      final pane = PaneState(paneId: 'p1', host: host);
      pane.handleAck('prev-session', workerId: 'worker1');
      pane.goHome();

      await Future<void>.delayed(Duration.zero);

      // pendingInputText is null when no draft was stored.
      expect(pane.pendingInputText, isNull);
    });
  });
}

// ---------------------------------------------------------------------------
// Minimal host with no worker (for "no key → no save" test)
// ---------------------------------------------------------------------------

class _RecordingPaneHostNoWorker extends _RecordingPaneHost {
  @override
  String? get defaultWorkerId => null;

  @override
  String? workerIdForSession(String sessionId) => null;
}

// ---------------------------------------------------------------------------
// Session builder helper
// ---------------------------------------------------------------------------

SessionInfo _session(String id, String status) => SessionInfo(
  sessionId: id,
  sessionType: 'conversational',
  status: status,
  workerId: 'worker1',
);
