/// Tests for PaneState message streaming and tool block lifecycle.
///
/// These cover the core state transitions that would be affected by any
/// extraction of a MessageStreamController from PaneState (structural Fix #4).
/// Run these as regression coverage before refactoring the streaming logic.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/app_notification.dart';
import 'package:rcflowclient/models/session_info.dart';
import 'package:rcflowclient/models/ws_messages.dart';
import 'package:rcflowclient/services/websocket_service.dart';
import 'package:rcflowclient/state/pane_state.dart';

// ---------------------------------------------------------------------------
// Minimal stub
// ---------------------------------------------------------------------------

class _StubHost implements PaneHost {
  @override
  bool get connected => false;

  @override
  List<SessionInfo> get sessions => [];

  @override
  WebSocketService wsForWorker(String workerId) => WebSocketService();

  @override
  String? workerIdForSession(String sessionId) => null;

  @override
  String? get defaultWorkerId => null;

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

PaneState _pane() => PaneState(paneId: 'test', host: _StubHost());

// ---------------------------------------------------------------------------
// Assistant text streaming
// ---------------------------------------------------------------------------

void main() {
  group('PaneState — assistant text streaming', () {
    test('appendAssistantChunk creates an assistant message', () {
      final pane = _pane();
      pane.appendAssistantChunk('Hello ');
      expect(pane.messages.length, 1);
      expect(pane.messages.first.type, DisplayMessageType.assistant);
      expect(pane.messages.first.content, 'Hello ');
      expect(pane.messages.first.finished, isFalse);
    });

    test('multiple chunks accumulate into a single message', () {
      final pane = _pane();
      pane.appendAssistantChunk('Hello ');
      pane.appendAssistantChunk('world');
      pane.appendAssistantChunk('!');
      expect(pane.messages.length, 1);
      expect(pane.messages.first.content, 'Hello world!');
    });

    test('finalizeStream marks the assistant message as finished', () {
      final pane = _pane();
      pane.appendAssistantChunk('Hi');
      expect(pane.messages.first.finished, isFalse);

      pane.finalizeStream();

      expect(pane.messages.first.finished, isTrue);
    });

    test('chunks after finalizeStream start a new message', () {
      final pane = _pane();
      pane.appendAssistantChunk('First turn');
      pane.finalizeStream();
      pane.appendAssistantChunk('Second turn');

      expect(pane.messages.length, 2);
      expect(pane.messages[0].content, 'First turn');
      expect(pane.messages[0].finished, isTrue);
      expect(pane.messages[1].content, 'Second turn');
      expect(pane.messages[1].finished, isFalse);
    });

    test('lastStreamMessage is null before any chunks', () {
      expect(_pane().lastStreamMessage, isNull);
    });

    test('lastStreamMessage points to the unfinished assistant message', () {
      final pane = _pane();
      pane.appendAssistantChunk('partial');
      expect(pane.lastStreamMessage, isNotNull);
      expect(pane.lastStreamMessage!.type, DisplayMessageType.assistant);
    });

    test('lastStreamMessage is null after finalizeStream', () {
      final pane = _pane();
      pane.appendAssistantChunk('partial');
      pane.finalizeStream();
      expect(pane.lastStreamMessage, isNull);
    });
  });

  // ---------------------------------------------------------------------------
  // Tool block lifecycle
  // ---------------------------------------------------------------------------

  group('PaneState — tool block lifecycle', () {
    test('startToolBlock adds a toolBlock message', () {
      final pane = _pane();
      pane.startToolBlock('bash', {'command': 'ls'});

      expect(pane.messages.length, 1);
      final msg = pane.messages.first;
      expect(msg.type, DisplayMessageType.toolBlock);
      expect(msg.toolName, 'bash');
      expect(msg.toolInput, {'command': 'ls'});
      expect(msg.finished, isFalse);
    });

    test('appendToolOutput appends to the last tool block', () {
      final pane = _pane();
      pane.startToolBlock('read_file', {'path': '/etc/passwd'});
      pane.appendToolOutput('file contents here');

      expect(pane.messages.last.content, 'file contents here');
    });

    test('multiple appendToolOutput calls accumulate content', () {
      final pane = _pane();
      pane.startToolBlock('bash', {'command': 'cat big_file.txt'});
      pane.appendToolOutput('line 1\n');
      pane.appendToolOutput('line 2\n');
      pane.appendToolOutput('line 3\n');

      expect(pane.messages.last.content, 'line 1\nline 2\nline 3\n');
    });

    test('finalizeStream marks open toolBlock as finished', () {
      final pane = _pane();
      pane.startToolBlock('bash', null);
      expect(pane.messages.last.finished, isFalse);

      pane.finalizeStream();

      expect(pane.messages.last.finished, isTrue);
    });

    test('tool error output sets isError flag', () {
      final pane = _pane();
      pane.startToolBlock('bash', {'command': 'rm -rf /'});
      pane.appendToolOutput('Permission denied', isError: true);

      expect(pane.messages.last.isError, isTrue);
    });

    test('applyDiffToLastToolBlock sets fileDiff on the last tool block', () {
      final pane = _pane();
      pane.startToolBlock('edit_file', {'path': 'main.dart'});
      pane.appendToolOutput('replaced 3 lines');
      const fakeDiff = '--- a/main.dart\n+++ b/main.dart\n@@ -1 +1 @@\n-old\n+new';
      pane.applyDiffToLastToolBlock(fakeDiff);

      expect(pane.messages.last.fileDiff, fakeDiff);
    });

    test('sequential tool blocks each start unfinished', () {
      final pane = _pane();
      pane.startToolBlock('tool_a', null);
      pane.finalizeStream();
      pane.startToolBlock('tool_b', null);

      expect(pane.messages.length, 2);
      expect(pane.messages[0].finished, isTrue);
      expect(pane.messages[1].finished, isFalse);
    });
  });

  // ---------------------------------------------------------------------------
  // Agent group lifecycle
  // ---------------------------------------------------------------------------

  group('PaneState — agent group lifecycle', () {
    test('startAgentGroup creates an agentGroup message', () {
      final pane = _pane();
      pane.startAgentGroup(
        'claude_code',
        {'working_directory': '/repo'},
        displayName: 'Claude Code',
      );

      expect(pane.messages.length, 1);
      final group = pane.messages.first;
      expect(group.type, DisplayMessageType.agentGroup);
      expect(group.toolName, 'claude_code');
      expect(group.displayName, 'Claude Code');
      expect(group.children, isEmpty);
      expect(group.finished, isFalse);
    });

    test('startToolBlock inside agent group appends to children', () {
      final pane = _pane();
      pane.startAgentGroup('claude_code', null);
      pane.startToolBlock('bash', {'command': 'ls'});

      final group = pane.messages.first;
      expect(group.children!.length, 1);
      expect(group.children!.first.type, DisplayMessageType.toolBlock);
      // The group's child is a tool block, not a top-level message.
      expect(pane.messages.length, 1);
    });

    test('endAgentGroup marks the group as finished', () {
      final pane = _pane();
      pane.startAgentGroup('claude_code', null);
      pane.startToolBlock('bash', null);
      pane.appendToolOutput('done');

      pane.endAgentGroup();

      final group = pane.messages.first;
      expect(group.finished, isTrue);
    });

    test('messages after endAgentGroup go to top-level', () {
      final pane = _pane();
      pane.startAgentGroup('claude_code', null);
      pane.startToolBlock('bash', null);
      pane.endAgentGroup();

      pane.appendAssistantChunk('After agent');

      expect(pane.messages.length, 2);
      expect(pane.messages[1].type, DisplayMessageType.assistant);
    });

    test('multiple tool blocks inside an agent group are all in children', () {
      final pane = _pane();
      pane.startAgentGroup('claude_code', null);
      pane.startToolBlock('tool_a', null);
      pane.appendToolOutput('output a');
      pane.startToolBlock('tool_b', null);
      pane.appendToolOutput('output b');
      pane.endAgentGroup();

      final group = pane.messages.first;
      expect(group.children!.length, 2);
      expect(group.children![0].toolName, 'tool_a');
      expect(group.children![1].toolName, 'tool_b');
    });

    test('finalizeStream inside an agent group closes the open tool block', () {
      final pane = _pane();
      pane.startAgentGroup('claude_code', null);
      pane.startToolBlock('bash', null);
      expect(pane.messages.first.children!.last.finished, isFalse);

      pane.finalizeStream();

      expect(pane.messages.first.children!.last.finished, isTrue);
    });
  });

  // ---------------------------------------------------------------------------
  // addDisplayMessage / addSystemMessage
  // ---------------------------------------------------------------------------

  group('PaneState — addDisplayMessage and addSystemMessage', () {
    test('addDisplayMessage appends to the message list', () {
      final pane = _pane();
      pane.addDisplayMessage(
        DisplayMessage(
          type: DisplayMessageType.system,
          content: 'Connected',
          finished: true,
        ),
      );
      expect(pane.messages.length, 1);
      expect(pane.messages.first.content, 'Connected');
    });

    test('addSystemMessage creates a system-type message', () {
      final pane = _pane();
      pane.addSystemMessage('Worker offline');
      expect(pane.messages.length, 1);
      expect(pane.messages.first.type, DisplayMessageType.system);
      expect(pane.messages.first.isError, isFalse);
    });

    test('addSystemMessage with isError=true marks the message as error', () {
      final pane = _pane();
      pane.addSystemMessage('Fatal error', isError: true);
      expect(pane.messages.first.isError, isTrue);
    });

    test('notifyListeners is called after addDisplayMessage', () {
      final pane = _pane();
      var notifyCount = 0;
      pane.addListener(() => notifyCount++);

      pane.addDisplayMessage(
        DisplayMessage(type: DisplayMessageType.system, content: 'x'),
      );

      expect(notifyCount, greaterThan(0));
    });
  });
}
