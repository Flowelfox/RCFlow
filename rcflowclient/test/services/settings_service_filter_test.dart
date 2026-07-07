/// Unit tests for the task-filter persistence keys on [SettingsService]:
/// assignees, priorities and labels (added for the unified filter popover),
/// exercised together with the pre-existing search/status/source keys.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/task_filter.dart';
import 'package:rcflowclient/services/settings_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

Future<SettingsService> _buildSettings([
  Map<String, Object> initial = const {},
]) async {
  SharedPreferences.setMockInitialValues(initial);
  final settings = SettingsService();
  await settings.init();
  return settings;
}

void main() {
  test('all six task-filter dimensions round-trip', () async {
    final s = await _buildSettings();
    s.tasksFilterSearch = 'login';
    s.tasksFilterStatus = ['todo', 'done'];
    s.tasksFilterSource = ['ai'];
    s.tasksFilterAssignees = ['user-1', kAssigneeMe];
    s.tasksFilterPriorities = [1, 0];
    s.tasksFilterLabels = ['bug', 'infra'];

    expect(s.tasksFilterSearch, 'login');
    expect(s.tasksFilterStatus, ['todo', 'done']);
    expect(s.tasksFilterSource, ['ai']);
    expect(s.tasksFilterAssignees, ['user-1', kAssigneeMe]);
    expect(s.tasksFilterPriorities, [1, 0]);
    expect(s.tasksFilterLabels, ['bug', 'infra']);
  });

  test('new keys default to empty lists', () async {
    final s = await _buildSettings();
    expect(s.tasksFilterAssignees, isEmpty);
    expect(s.tasksFilterPriorities, isEmpty);
    expect(s.tasksFilterLabels, isEmpty);
  });

  test('corrupt priority entries are dropped on read', () async {
    final s = await _buildSettings({
      'rcflow_tasks_filter_priorities': '["1","oops","3"]',
    });
    expect(s.tasksFilterPriorities, [1, 3]);
  });

  test('assignee sentinels survive persistence', () async {
    final s = await _buildSettings();
    s.tasksFilterAssignees = [kAssigneeMe, kAssigneeUnassigned];
    expect(s.tasksFilterAssignees, [kAssigneeMe, kAssigneeUnassigned]);
  });
}
