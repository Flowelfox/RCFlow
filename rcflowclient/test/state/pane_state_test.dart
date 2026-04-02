/// Tests for [PaneState.switchSession] project-panel auto-open behaviour.
///
/// When a session already has a [SessionInfo.mainProjectPath] (i.e. the user
/// used @ProjectName at least once), switching to that session must auto-open
/// the project panel — even if the session is ended, paused, or cancelled.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/app_notification.dart';
import 'package:rcflowclient/models/session_info.dart';
import 'package:rcflowclient/models/subprocess_info.dart';
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
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

SessionInfo _session(
  String id,
  String status, {
  String? mainProjectPath,
  String? selectedWorktreePath,
}) => SessionInfo(
  sessionId: id,
  sessionType: 'conversational',
  status: status,
  workerId: 'worker1',
  mainProjectPath: mainProjectPath,
  selectedWorktreePath: selectedWorktreePath,
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
          _session(
            's1',
            'active',
            mainProjectPath: '/home/user/Projects/MyApp',
          ),
        ]),
      );

      pane.switchSession('s1');

      expect(pane.activeRightPanel, 'project');
    });

    test('opens project panel for paused session with mainProjectPath', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([
          _session(
            's1',
            'paused',
            mainProjectPath: '/home/user/Projects/MyApp',
          ),
        ]),
      );

      pane.switchSession('s1');

      expect(pane.activeRightPanel, 'project');
    });

    test(
      'opens project panel for completed (ended) session with mainProjectPath',
      () {
        final pane = PaneState(
          paneId: 'p1',
          host: _StubPaneHost([
            _session(
              's1',
              'completed',
              mainProjectPath: '/home/user/Projects/MyApp',
            ),
          ]),
        );

        pane.switchSession('s1');

        expect(pane.activeRightPanel, 'project');
      },
    );

    test('opens project panel for cancelled session with mainProjectPath', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([
          _session(
            's1',
            'cancelled',
            mainProjectPath: '/home/user/Projects/MyApp',
          ),
        ]),
      );

      pane.switchSession('s1');

      expect(pane.activeRightPanel, 'project');
    });

    test('does not open project panel when session has no mainProjectPath', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([_session('s1', 'completed')]),
      );

      pane.switchSession('s1');

      expect(pane.activeRightPanel, isNull);
    });

    test('restores project panel when switching between sessions', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([
          _session(
            's1',
            'completed',
            mainProjectPath: '/home/user/Projects/Alpha',
          ),
          _session('s2', 'completed'), // no project
          _session(
            's3',
            'completed',
            mainProjectPath: '/home/user/Projects/Beta',
          ),
        ]),
      );

      pane.switchSession('s1');
      expect(
        pane.activeRightPanel,
        'project',
        reason: 's1 has a project — panel should open',
      );

      pane.switchSession('s2');
      expect(
        pane.activeRightPanel,
        isNull,
        reason: 's2 has no project — panel should stay closed',
      );

      pane.switchSession('s3');
      expect(
        pane.activeRightPanel,
        'project',
        reason: 's3 has a project — panel should reopen',
      );
    });
  });

  // ---------------------------------------------------------------------------
  // toggleRightPanel / openProjectPanel — close-and-reopen cycle
  // ---------------------------------------------------------------------------

  group('PaneState — project panel close and reopen', () {
    PaneState paneWithProject() => PaneState(
      paneId: 'p1',
      host: _StubPaneHost([
        _session('s1', 'active', mainProjectPath: '/home/user/Projects/MyApp'),
      ]),
    )..switchSession('s1');

    test('toggleRightPanel closes an open project panel', () {
      final pane = paneWithProject();
      expect(pane.activeRightPanel, 'project');

      pane.toggleRightPanel('project');

      expect(pane.activeRightPanel, isNull);
    });

    test('toggleRightPanel reopens a closed project panel', () {
      final pane = paneWithProject();
      pane.toggleRightPanel('project'); // close
      expect(pane.activeRightPanel, isNull);

      pane.toggleRightPanel('project'); // reopen

      expect(pane.activeRightPanel, 'project');
    });

    test('openProjectPanel opens a closed panel', () {
      final pane = paneWithProject();
      pane.toggleRightPanel('project'); // close
      expect(pane.activeRightPanel, isNull);

      pane.openProjectPanel();

      expect(pane.activeRightPanel, 'project');
    });

    test('openProjectPanel is a no-op when panel is already open', () {
      final pane = paneWithProject();
      var notifyCount = 0;
      pane.addListener(() => notifyCount++);

      pane.openProjectPanel(); // already open — should not notify

      expect(pane.activeRightPanel, 'project');
      expect(
        notifyCount,
        0,
        reason: 'no rebuild needed when panel already open',
      );
    });

    test('closing then switching back to a project session reopens panel', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([
          _session(
            's1',
            'active',
            mainProjectPath: '/home/user/Projects/MyApp',
          ),
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
      expect(
        pane.activeRightPanel,
        'project',
        reason: 'panel should reopen when switching back to a project session',
      );
    });
  });

  group('PaneState.setSelectedProject — pre-session tagging', () {
    // Verifies that after tagging @ProjectName (with the full path resolved from
    // the server's project list), the project panel can show real content
    // immediately — before any prompt is sent or session created.

    test(
      'effectiveProjectPath uses the pre-resolved path before any session',
      () {
        final pane = PaneState(paneId: 'p1', host: _StubPaneHost([]));

        pane.setSelectedProject(
          'MyProject',
          path: '/home/user/Projects/MyProject',
        );

        expect(
          pane.selectedProjectName,
          'MyProject',
          reason: 'chip name must reflect the user selection immediately',
        );
        expect(
          pane.activeRightPanel,
          'project',
          reason: 'project panel must open on @-mention',
        );
        expect(
          pane.effectiveProjectPath,
          '/home/user/Projects/MyProject',
          reason:
              'panel must have the full path to fetch worktrees/artifacts '
              'immediately without waiting for a session',
        );
      },
    );

    test('effectiveProjectPath is null when no project is selected', () {
      final pane = PaneState(paneId: 'p1', host: _StubPaneHost([]));

      expect(pane.selectedProjectName, isNull);
      expect(pane.effectiveProjectPath, isNull);
    });

    test('effectiveProjectPath is null when path was not provided', () {
      final pane = PaneState(paneId: 'p1', host: _StubPaneHost([]));

      pane.setSelectedProject('MyProject');

      expect(pane.selectedProjectName, 'MyProject');
      expect(
        pane.effectiveProjectPath,
        isNull,
        reason: 'no path provided — panel cannot fetch content yet',
      );
    });

    test('clearing the chip clears both name and path', () {
      final pane = PaneState(paneId: 'p1', host: _StubPaneHost([]));

      pane.setSelectedProject(
        'MyProject',
        path: '/home/user/Projects/MyProject',
      );
      expect(pane.effectiveProjectPath, '/home/user/Projects/MyProject');

      pane.setSelectedProject(null);
      expect(pane.selectedProjectName, isNull);
      expect(pane.effectiveProjectPath, isNull);
    });

    test('session-confirmed path takes precedence over pre-resolved path', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([
          _session(
            's1',
            'active',
            mainProjectPath: '/home/user/Projects/MyProject',
          ),
        ]),
      );

      pane.setSelectedProject(
        'MyProject',
        path: '/home/user/Projects/MyProject',
      );
      pane.switchSession('s1');

      expect(
        pane.effectiveProjectPath,
        '/home/user/Projects/MyProject',
        reason: 'confirmed session path is returned once session is active',
      );
    });
  });

  // ---------------------------------------------------------------------------
  // Subprocess tracking
  // ---------------------------------------------------------------------------

  group('PaneState — runningSubprocess', () {
    PaneState makePaneState() =>
        PaneState(paneId: 'p1', host: _StubPaneHost([]));

    SubprocessInfo makeInfo({String? tool}) => SubprocessInfo(
      subprocessType: 'claude_code',
      displayName: 'Claude Code',
      workingDirectory: '/repo',
      currentTool: tool,
      startedAt: DateTime.utc(2026, 3, 20),
    );

    test('is null by default', () {
      expect(makePaneState().runningSubprocess, isNull);
    });

    test('setRunningSubprocess updates state and notifies listeners', () {
      final pane = makePaneState();
      var notified = 0;
      pane.addListener(() => notified++);

      pane.setRunningSubprocess(makeInfo());

      expect(pane.runningSubprocess, isNotNull);
      expect(pane.runningSubprocess!.subprocessType, 'claude_code');
      expect(notified, 1);
    });

    test('setRunningSubprocess(null) clears state', () {
      final pane = makePaneState();
      pane.setRunningSubprocess(makeInfo());
      expect(pane.runningSubprocess, isNotNull);

      pane.setRunningSubprocess(null);

      expect(pane.runningSubprocess, isNull);
    });

    test('currentTool is reflected in runningSubprocess', () {
      final pane = makePaneState();

      pane.setRunningSubprocess(makeInfo(tool: 'Bash'));
      expect(pane.runningSubprocess!.currentTool, 'Bash');

      pane.setRunningSubprocess(makeInfo(tool: null));
      expect(pane.runningSubprocess!.currentTool, isNull);
    });

    test('switchSession clears runningSubprocess', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([_session('s1', 'active')]),
      );
      pane.setRunningSubprocess(makeInfo());
      expect(pane.runningSubprocess, isNotNull);

      pane.switchSession('s1');

      expect(pane.runningSubprocess, isNull);
    });
  });

  // ---------------------------------------------------------------------------
  // Worker settings pane state
  // ---------------------------------------------------------------------------

  group('PaneState — workerSettings', () {
    PaneState makePaneState() =>
        PaneState(paneId: 'p1', host: _StubPaneHost([]));

    test('workerSettingsTool and section are null by default', () {
      final pane = makePaneState();
      expect(pane.workerSettingsTool, isNull);
      expect(pane.workerSettingsSection, isNull);
    });

    test('setWorkerSettings sets tool and default section', () {
      final pane = makePaneState();
      pane.setWorkerSettings('claude_code');
      expect(pane.workerSettingsTool, 'claude_code');
      expect(pane.workerSettingsSection, 'plugins');
    });

    test('setWorkerSettings accepts custom section', () {
      final pane = makePaneState();
      pane.setWorkerSettings('codex', section: 'config');
      expect(pane.workerSettingsTool, 'codex');
      expect(pane.workerSettingsSection, 'config');
    });

    test('setWorkerSettings notifies listeners', () {
      final pane = makePaneState();
      var notified = 0;
      pane.addListener(() => notified++);
      pane.setWorkerSettings('claude_code');
      expect(notified, 1);
    });

    test('clearWorkerSettings nulls out both fields', () {
      final pane = makePaneState();
      pane.setWorkerSettings('claude_code');
      pane.clearWorkerSettings();
      expect(pane.workerSettingsTool, isNull);
      expect(pane.workerSettingsSection, isNull);
    });

    test('clearWorkerSettings notifies listeners', () {
      final pane = makePaneState();
      pane.setWorkerSettings('claude_code');
      var notified = 0;
      pane.addListener(() => notified++);
      pane.clearWorkerSettings();
      expect(notified, 1);
    });
  });

  // ---------------------------------------------------------------------------
  // Tool mention chip
  // ---------------------------------------------------------------------------

  group('PaneState.setSelectedTool — tool mention chip', () {
    PaneState makePaneState() =>
        PaneState(paneId: 'p1', host: _StubPaneHost([]));

    test('selectedToolMention is null by default', () {
      expect(makePaneState().selectedToolMention, isNull);
    });

    test('setSelectedTool sets the tool name and notifies', () {
      final pane = makePaneState();
      var notified = 0;
      pane.addListener(() => notified++);

      pane.setSelectedTool('ClaudeCode');

      expect(pane.selectedToolMention, 'ClaudeCode');
      expect(notified, 1);
    });

    test('setSelectedTool(null) clears the tool name', () {
      final pane = makePaneState();
      pane.setSelectedTool('ClaudeCode');
      expect(pane.selectedToolMention, 'ClaudeCode');

      pane.setSelectedTool(null);

      expect(pane.selectedToolMention, isNull);
    });

    test('goHome clears selectedToolMention', () {
      final pane = makePaneState();
      pane.setSelectedTool('ClaudeCode');

      pane.goHome();

      expect(pane.selectedToolMention, isNull);
    });

    test('startNewChat clears selectedToolMention', () {
      final pane = makePaneState();
      pane.setSelectedTool('ClaudeCode');

      pane.startNewChat();

      expect(pane.selectedToolMention, isNull);
    });

    test('switchSession clears selectedToolMention', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([_session('s1', 'active')]),
      );
      pane.setSelectedTool('ClaudeCode');

      pane.switchSession('s1');

      expect(pane.selectedToolMention, isNull);
    });
  });

  // ---------------------------------------------------------------------------
  // Active worktree display on pane switch
  // ---------------------------------------------------------------------------

  group('PaneState.currentSelectedWorktreePath — active worktree', () {
    test('returns worktree path for session with a selected worktree', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([
          _session(
            's1',
            'active',
            mainProjectPath: '/home/user/Projects/MyApp',
            selectedWorktreePath:
                '/home/user/Projects/MyApp/.worktrees/feat-login',
          ),
        ]),
      );

      pane.switchSession('s1');

      expect(
        pane.currentSelectedWorktreePath,
        '/home/user/Projects/MyApp/.worktrees/feat-login',
      );
    });

    test('returns null for session without a selected worktree', () {
      final pane = PaneState(
        paneId: 'p1',
        host: _StubPaneHost([
          _session(
            's1',
            'active',
            mainProjectPath: '/home/user/Projects/MyApp',
          ),
        ]),
      );

      pane.switchSession('s1');

      expect(pane.currentSelectedWorktreePath, isNull);
    });

    test(
      'updates when switching between sessions with different worktrees',
      () {
        final pane = PaneState(
          paneId: 'p1',
          host: _StubPaneHost([
            _session(
              's1',
              'active',
              mainProjectPath: '/home/user/Projects/MyApp',
              selectedWorktreePath:
                  '/home/user/Projects/MyApp/.worktrees/feat-a',
            ),
            _session(
              's2',
              'active',
              mainProjectPath: '/home/user/Projects/MyApp',
              selectedWorktreePath:
                  '/home/user/Projects/MyApp/.worktrees/feat-b',
            ),
            _session(
              's3',
              'active',
              mainProjectPath: '/home/user/Projects/MyApp',
            ),
          ]),
        );

        pane.switchSession('s1');
        expect(
          pane.currentSelectedWorktreePath,
          '/home/user/Projects/MyApp/.worktrees/feat-a',
        );

        pane.switchSession('s2');
        expect(
          pane.currentSelectedWorktreePath,
          '/home/user/Projects/MyApp/.worktrees/feat-b',
        );

        pane.switchSession('s3');
        expect(
          pane.currentSelectedWorktreePath,
          isNull,
          reason: 's3 has no worktree — should return null',
        );
      },
    );

    test('returns null when no session is active', () {
      final pane = PaneState(paneId: 'p1', host: _StubPaneHost([]));

      expect(pane.currentSelectedWorktreePath, isNull);
    });
  });

  group('PaneState.startNewChat — default agent', () {
    test('pre-selects default agent when worker has one configured', () {
      final host = _StubPaneHostWithDefaultAgent([]);
      final pane = PaneState(paneId: 'pane1', host: host);
      pane.startNewChat();
      expect(pane.selectedToolMention, 'ClaudeCode');
    });

    test('leaves selectedToolMention null when no default agent', () {
      final host = _StubPaneHost([]);
      final pane = PaneState(paneId: 'pane1', host: host);
      pane.startNewChat();
      expect(pane.selectedToolMention, isNull);
    });
  });
}

class _StubPaneHostWithDefaultAgent extends _StubPaneHost {
  _StubPaneHostWithDefaultAgent(super.sessions);

  @override
  String? defaultAgentForWorker(String? workerId) => 'ClaudeCode';
}
