import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/badge_spec.dart';
import 'package:rcflowclient/models/legacy_badge_adapter.dart';

void main() {
  group('BadgeSpec', () {
    test('fromJson parses all fields', () {
      final json = {
        'type': 'status',
        'label': 'active',
        'priority': 0,
        'visible': true,
        'interactive': false,
        'payload': {'activity_state': 'idle'},
      };
      final spec = BadgeSpec.fromJson(json);
      expect(spec.type, 'status');
      expect(spec.label, 'active');
      expect(spec.priority, 0);
      expect(spec.visible, true);
      expect(spec.interactive, false);
      expect(spec.payload['activity_state'], 'idle');
    });

    test('toJson roundtrip', () {
      final original = BadgeSpec(
        type: 'caveman',
        label: 'Caveman',
        priority: 50,
        visible: true,
        interactive: false,
        payload: {'level': 'full'},
      );
      final json = original.toJson();
      final restored = BadgeSpec.fromJson(json);
      expect(restored.type, original.type);
      expect(restored.label, original.label);
      expect(restored.priority, original.priority);
      expect(restored.visible, original.visible);
      expect(restored.interactive, original.interactive);
      expect(restored.payload['level'], 'full');
    });

    test('listFromJson null returns empty list', () {
      expect(BadgeSpec.listFromJson(null), isEmpty);
    });

    test('listFromJson empty list returns empty', () {
      expect(BadgeSpec.listFromJson([]), isEmpty);
    });

    test('listFromJson parses multiple specs', () {
      final list = [
        {'type': 'status', 'label': 'active', 'priority': 0, 'visible': true, 'interactive': false},
        {'type': 'caveman', 'label': 'Caveman', 'priority': 50, 'visible': true, 'interactive': false},
      ];
      final specs = BadgeSpec.listFromJson(list);
      expect(specs.length, 2);
      expect(specs[0].type, 'status');
      expect(specs[1].type, 'caveman');
    });

    test('default payload is empty map', () {
      final spec = BadgeSpec(
        type: 'worker',
        label: 'Home',
        priority: 10,
        visible: true,
        interactive: false,
      );
      expect(spec.payload, isEmpty);
    });

    test('fromJson handles missing optional fields gracefully', () {
      // Only required fields — all others default
      final spec = BadgeSpec.fromJson({'type': 'x', 'label': 'X', 'priority': 5, 'visible': true, 'interactive': false});
      expect(spec.type, 'x');
      expect(spec.payload, isEmpty);
    });
  });

  group('BadgePriority', () {
    test('constants are strictly ascending', () {
      final priorities = [
        BadgePriority.status,
        BadgePriority.worker,
        BadgePriority.agent,
        BadgePriority.project,
        BadgePriority.worktree,
        BadgePriority.caveman,
      ];
      for (int i = 0; i < priorities.length - 1; i++) {
        expect(priorities[i], lessThan(priorities[i + 1]),
            reason: 'priority[$i] must be less than priority[${i + 1}]');
      }
    });

    test('status is 0', () => expect(BadgePriority.status, 0));
    test('caveman is 50', () => expect(BadgePriority.caveman, 50));
  });

  group('LegacyBadgeAdapter', () {
    test('produces status badge from flat status field', () {
      final badges = LegacyBadgeAdapter.adapt({'status': 'active', 'activity_state': 'idle'});
      final sb = badges.where((b) => b.type == 'status').toList();
      expect(sb.length, 1);
      expect(sb.first.label, 'active');
    });

    test('produces caveman badge when caveman_mode is true', () {
      final badges = LegacyBadgeAdapter.adapt({
        'status': 'active',
        'caveman_mode': true,
      });
      expect(badges.any((b) => b.type == 'caveman'), isTrue);
    });

    test('no caveman badge when caveman_mode is false', () {
      final badges = LegacyBadgeAdapter.adapt({
        'status': 'active',
        'caveman_mode': false,
      });
      expect(badges.any((b) => b.type == 'caveman'), isFalse);
    });

    test('produces agent badge when agent_type present', () {
      final badges = LegacyBadgeAdapter.adapt({
        'status': 'active',
        'agent_type': 'claude_code',
      });
      final ab = badges.where((b) => b.type == 'agent').toList();
      expect(ab.length, 1);
      expect(ab.first.label, 'claude_code');
    });

    test('no agent badge when agent_type is null', () {
      final badges = LegacyBadgeAdapter.adapt({'status': 'active'});
      expect(badges.any((b) => b.type == 'agent'), isFalse);
    });

    test('produces project badge when main_project_path present', () {
      final badges = LegacyBadgeAdapter.adapt({
        'status': 'active',
        'main_project_path': '/home/user/Projects/RCFlow',
      });
      final pb = badges.where((b) => b.type == 'project').toList();
      expect(pb.length, 1);
      expect(pb.first.label, 'RCFlow');
    });

    test('produces project badge when only error present', () {
      final badges = LegacyBadgeAdapter.adapt({
        'status': 'active',
        'project_name_error': 'not found',
      });
      expect(badges.any((b) => b.type == 'project'), isTrue);
    });

    test('produces worktree badge when worktree map present', () {
      final badges = LegacyBadgeAdapter.adapt({
        'status': 'active',
        'worktree': {'repo_path': '/home/user/RCFlow', 'branch': 'feat/x', 'last_action': 'new'},
      });
      final wb = badges.where((b) => b.type == 'worktree').toList();
      expect(wb.length, 1);
      expect(wb.first.label, 'feat/x');
    });

    test('produces worker badge when workerLabel provided', () {
      final badges = LegacyBadgeAdapter.adapt({'status': 'active'}, workerLabel: 'HomeServer');
      final wb = badges.where((b) => b.type == 'worker').toList();
      expect(wb.length, 1);
      expect(wb.first.label, 'HomeServer');
    });

    test('no worker badge when workerLabel is empty', () {
      final badges = LegacyBadgeAdapter.adapt({'status': 'active'});
      expect(badges.any((b) => b.type == 'worker'), isFalse);
    });

    test('empty message produces at least status badge', () {
      final badges = LegacyBadgeAdapter.adapt({});
      expect(badges.any((b) => b.type == 'status'), isTrue);
    });
  });
}
