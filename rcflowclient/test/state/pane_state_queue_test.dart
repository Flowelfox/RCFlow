/// Tests for [PaneState] queued-message reconciliation.
///
/// Covers the protocol flows defined in ``Queued User Messages`` in
/// ``Design.md``: ack promotion, message_queued upsert, message_dequeued
/// removal with renumbering, message_queued_updated edit, and
/// applyQueueSnapshot reconnect reconcile.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/app_notification.dart';
import 'package:rcflowclient/models/session_info.dart';
import 'package:rcflowclient/models/ws_messages.dart';
import 'package:rcflowclient/services/websocket_service.dart';
import 'package:rcflowclient/state/pane_state.dart';

class _StubHost implements PaneHost {
  @override
  bool get connected => false;

  @override
  List<SessionInfo> get sessions => const [];

  @override
  WebSocketService wsForWorker(String workerId) => WebSocketService();

  @override
  String? workerIdForSession(String sessionId) => 'w1';

  @override
  String? get defaultWorkerId => 'w1';

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
  void muteSessionSound(String sessionId) {}

  @override
  ({String content, DateTime? cachedAt}) getDraft(String key) =>
      (content: '', cachedAt: null);

  @override
  void saveDraft(String key, String content) {}

  @override
  void clearDraft(String key) {}

  @override
  Map<String, dynamic>? getDraftPlucks(String key) => null;

  @override
  void saveDraftPlucks(String key, Map<String, dynamic> plucks) {}

  @override
  void clearDraftPlucks(String key) {}

  @override
  bool isWorkerCavemanActive(String? workerId) => false;

  @override
  SessionInfo? sessionById(String sessionId) => null;
}

PaneState _pane() => PaneState(paneId: 'p1', host: _StubHost());

Map<String, dynamic> _queuedEvent({
  required String id,
  required int position,
  String content = 'msg',
  String? display,
}) => {
  'type': 'message_queued',
  'session_id': 'sess',
  'queued_id': id,
  'position': position,
  'content': content,
  'display_content': display ?? content,
  'submitted_at': DateTime.now().toIso8601String(),
};

void main() {
  group('PaneState queued-message lifecycle', () {
    test('applyMessageQueued inserts and sorts by position', () {
      final pane = _pane();
      pane.applyMessageQueued(_queuedEvent(id: 'b', position: 1));
      pane.applyMessageQueued(_queuedEvent(id: 'a', position: 0));
      final queue = pane.queuedMessages;
      expect(queue.map((q) => q.queuedId), ['a', 'b']);
    });

    test('applyMessageQueued upserts an existing entry', () {
      final pane = _pane();
      pane.applyMessageQueued(_queuedEvent(id: 'x', position: 0, content: 'v1'));
      pane.applyMessageQueued(_queuedEvent(id: 'x', position: 0, content: 'v2'));
      final queue = pane.queuedMessages;
      expect(queue, hasLength(1));
      expect(queue.first.content, 'v2');
    });

    test('applyMessageDequeued removes entry and renumbers the rest', () {
      final pane = _pane();
      for (var i = 0; i < 3; i++) {
        pane.applyMessageQueued(_queuedEvent(id: 'q$i', position: i));
      }
      pane.applyMessageDequeued('q1');
      final queue = pane.queuedMessages;
      expect(queue.map((q) => q.queuedId), ['q0', 'q2']);
      expect(queue.map((q) => q.position), [0, 1]);
    });

    test('applyMessageQueuedUpdated mutates content + updatedAt', () {
      final pane = _pane();
      pane.applyMessageQueued(_queuedEvent(id: 'q', position: 0));
      pane.applyMessageQueuedUpdated({
        'type': 'message_queued_updated',
        'queued_id': 'q',
        'content': 'edited',
        'display_content': 'edited',
        'updated_at': DateTime.now().toIso8601String(),
      });
      expect(pane.queuedMessages.first.content, 'edited');
    });

    test('applyQueueSnapshot replaces the local queue', () {
      final pane = _pane();
      pane.applyMessageQueued(_queuedEvent(id: 'stale', position: 0));
      pane.applyQueueSnapshot([
        {
          'queued_id': 'fresh-a',
          'position': 0,
          'display_content': 'a',
          'submitted_at': DateTime.now().toIso8601String(),
          'updated_at': DateTime.now().toIso8601String(),
        },
        {
          'queued_id': 'fresh-b',
          'position': 1,
          'display_content': 'b',
          'submitted_at': DateTime.now().toIso8601String(),
          'updated_at': DateTime.now().toIso8601String(),
        },
      ]);
      final queue = pane.queuedMessages;
      expect(queue.map((q) => q.queuedId), ['fresh-a', 'fresh-b']);
    });

    test('handleAck with queued=true promotes pending-echo into queue', () {
      final pane = _pane();
      // Simulate sendPrompt having added an optimistic echo.
      pane.addDisplayMessage(
        DisplayMessage(
          type: DisplayMessageType.user,
          content: 'hello',
          pendingLocalEcho: true,
        ),
      );
      pane.handleAck('sess', queued: true, queuedId: 'qid1');
      expect(pane.queuedMessages, hasLength(1));
      expect(pane.queuedMessages.first.queuedId, 'qid1');
      // The optimistic DisplayMessage was removed so it doesn't double up.
      expect(pane.messages.where((m) => m.pendingLocalEcho), isEmpty);
    });
  });
}
