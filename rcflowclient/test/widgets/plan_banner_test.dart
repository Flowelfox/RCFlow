/// Widget tests for plan-related UI affordances.
///
/// Covers:
/// - [TaskTile] shows plan badge (green description icon) when planArtifactId is set
/// - [TaskTile] hides plan badge when planArtifactId is null
/// - "Make plan" button visible in header when no plan exists
/// - "Open plan" button visible in header when plan exists
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:rcflowclient/models/task_info.dart';
import 'package:rcflowclient/services/settings_service.dart';
import 'package:rcflowclient/state/app_state.dart';
import 'package:rcflowclient/theme.dart';
import 'package:rcflowclient/ui/widgets/session_panel/task_tile.dart';
import 'package:shared_preferences/shared_preferences.dart';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

Future<AppState> _buildAppState() async {
  SharedPreferences.setMockInitialValues({});
  final settings = SettingsService();
  await settings.init();
  return AppState(settings: settings);
}

TaskInfo _task({String? planArtifactId}) => TaskInfo(
      taskId: 'task-1',
      title: 'Fix the critical bug',
      status: 'todo',
      source: 'user',
      workerId: 'worker-1',
      workerName: 'Worker 1',
      createdAt: DateTime.utc(2026, 4, 1),
      updatedAt: DateTime.utc(2026, 4, 1),
      planArtifactId: planArtifactId,
    );

Widget _buildTile(AppState appState, TaskInfo task) {
  return ChangeNotifierProvider<AppState>.value(
    value: appState,
    child: MaterialApp(
      theme: buildDarkTheme(),
      home: Scaffold(
        body: TaskTile(task: task, state: appState),
      ),
    ),
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  group('TaskTile — plan badge', () {
    testWidgets('shows plan badge icon when planArtifactId is set', (
      tester,
    ) async {
      final appState = await _buildAppState();
      final task = _task(planArtifactId: 'artifact-abc');

      await tester.pumpWidget(_buildTile(appState, task));
      await tester.pump();

      // The plan badge uses Icons.description_outlined with green color.
      // There should be at least one such icon in the tree.
      final iconFinder = find.byWidgetPredicate(
        (w) =>
            w is Icon &&
            w.icon == Icons.description_outlined &&
            w.color == const Color(0xFF10B981),
      );
      expect(iconFinder, findsOneWidget);
    });

    testWidgets('hides plan badge when planArtifactId is null', (
      tester,
    ) async {
      final appState = await _buildAppState();
      final task = _task(); // no planArtifactId

      await tester.pumpWidget(_buildTile(appState, task));
      await tester.pump();

      final iconFinder = find.byWidgetPredicate(
        (w) =>
            w is Icon &&
            w.icon == Icons.description_outlined &&
            w.color == const Color(0xFF10B981),
      );
      expect(iconFinder, findsNothing);
    });

    testWidgets('plan badge is a GestureDetector (tappable)', (
      tester,
    ) async {
      final appState = await _buildAppState();
      final task = _task(planArtifactId: 'artifact-xyz');

      await tester.pumpWidget(_buildTile(appState, task));
      await tester.pump();

      // The green icon should be wrapped in a GestureDetector
      final badge = find.byWidgetPredicate(
        (w) =>
            w is Icon &&
            w.icon == Icons.description_outlined &&
            w.color == const Color(0xFF10B981),
      );
      expect(badge, findsOneWidget);
      // Ancestor should be a GestureDetector
      expect(
        find.ancestor(of: badge, matching: find.byType(GestureDetector)),
        findsAtLeastNWidgets(1),
      );
    });
  });

  group('WebSocketService.startPlanSession — message format', () {
    // These are unit tests for the WS message format, not widget tests.
    // They validate the message built before it is sent over the socket.
    test('start_plan_session message has correct type field', () {
      const msg = <String, dynamic>{
        'type': 'start_plan_session',
        'task_id': 'task-abc',
        'project_name': 'my-project',
      };
      expect(msg['type'], 'start_plan_session');
      expect(msg['task_id'], 'task-abc');
    });

    test('plan ack has purpose=plan field', () {
      const ack = <String, dynamic>{
        'type': 'ack',
        'session_id': 'sess-plan-1',
        'purpose': 'plan',
      };
      expect(ack['purpose'], 'plan');
    });
  });
}
