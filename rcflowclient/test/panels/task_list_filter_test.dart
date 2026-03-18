/// Unit tests for [filterLinearIssuesByQuery] — the helper used by
/// [TaskListPanel] to apply the active search query to the Unlinked Issues
/// section.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/linear_issue_info.dart';
import 'package:rcflowclient/ui/widgets/session_panel/task_list_panel.dart';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

LinearIssueInfo _issue({
  String id = 'id',
  String identifier = 'ENG-1',
  String title = 'Test issue',
  String? assigneeName,
}) =>
    LinearIssueInfo(
      id: id,
      linearId: 'lin-$id',
      identifier: identifier,
      title: title,
      priority: 0,
      stateName: 'Todo',
      stateType: 'unstarted',
      teamId: 'team1',
      url: 'https://linear.app/issue/$id',
      labels: [],
      createdAt: DateTime(2025),
      updatedAt: DateTime(2025),
      syncedAt: DateTime(2025),
      assigneeName: assigneeName,
      workerId: 'w1',
      workerName: 'Worker',
    );

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  group('filterLinearIssuesByQuery', () {
    test('empty query returns all issues', () {
      final issues = [
        _issue(id: '1', title: 'Alpha'),
        _issue(id: '2', title: 'Beta'),
      ];
      expect(filterLinearIssuesByQuery(issues, ''), issues);
    });

    test('filters by title (case-insensitive)', () {
      final issues = [
        _issue(id: '1', title: 'Fix login bug'),
        _issue(id: '2', title: 'Update dashboard'),
        _issue(id: '3', title: 'Login page redesign'),
      ];
      final result = filterLinearIssuesByQuery(issues, 'login');
      expect(result.map((i) => i.id), containsAll(['1', '3']));
      expect(result.length, 2);
    });

    test('filters by identifier (case-insensitive)', () {
      final issues = [
        _issue(id: '1', identifier: 'ENG-42'),
        _issue(id: '2', identifier: 'ENG-100'),
        _issue(id: '3', identifier: 'DESIGN-7'),
      ];
      final result = filterLinearIssuesByQuery(issues, 'eng-');
      expect(result.map((i) => i.id), containsAll(['1', '2']));
      expect(result.length, 2);
    });

    test('filters by assignee name (case-insensitive)', () {
      final issues = [
        _issue(id: '1', assigneeName: 'Alice'),
        _issue(id: '2', assigneeName: 'Bob'),
        _issue(id: '3', assigneeName: null),
      ];
      final result = filterLinearIssuesByQuery(issues, 'alice');
      expect(result.map((i) => i.id), equals(['1']));
    });

    test('returns empty list when nothing matches', () {
      final issues = [
        _issue(id: '1', title: 'Alpha', identifier: 'ENG-1'),
        _issue(id: '2', title: 'Beta', identifier: 'ENG-2'),
      ];
      expect(filterLinearIssuesByQuery(issues, 'zzznomatch'), isEmpty);
    });

    test('null assigneeName does not throw', () {
      final issues = [_issue(id: '1', assigneeName: null)];
      expect(
        () => filterLinearIssuesByQuery(issues, 'alice'),
        returnsNormally,
      );
    });
  });
}
