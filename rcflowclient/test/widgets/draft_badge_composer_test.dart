import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/badge_spec.dart';
import 'package:rcflowclient/models/worker_config.dart';
import 'package:rcflowclient/ui/badges/draft_badge_composer.dart';

void main() {
  const composer = DraftBadgeComposer();

  group('DraftBadgeComposer — agentType', () {
    test('produces agent badge for claude_code', () {
      final badges = composer.compose(agentType: 'claude_code');
      expect(badges.length, 1);
      final agent = badges.first;
      expect(agent.type, 'agent');
      expect(agent.label, 'ClaudeCode');
      expect(agent.priority, BadgePriority.agent);
      expect(agent.visible, true);
      expect(agent.interactive, false);
      expect(agent.payload['agent_type'], 'claude_code');
    });

    test('produces agent badge for codex', () {
      final badges = composer.compose(agentType: 'codex');
      final agent = badges.first;
      expect(agent.label, 'Codex');
      expect(agent.payload['agent_type'], 'codex');
    });

    test('produces agent badge for opencode', () {
      final badges = composer.compose(agentType: 'opencode');
      final agent = badges.first;
      expect(agent.label, 'OpenCode');
    });

    test('unknown agent type uses raw name as label', () {
      final badges = composer.compose(agentType: 'custom_agent');
      final agent = badges.first;
      expect(agent.label, 'custom_agent');
      expect(agent.payload['agent_type'], 'custom_agent');
    });

    test('no agent badge when agentType is null', () {
      final badges = composer.compose();
      expect(badges.where((b) => b.type == 'agent'), isEmpty);
    });

    test('agent badge coexists with worker and project badges', () {
      final badges = composer.compose(
        worker: WorkerConfig(id: 'w1', name: 'Home', host: 'localhost', apiKey: ''),
        agentType: 'claude_code',
        projectPath: '/home/user/MyProject',
      );
      expect(badges.length, 3);
      expect(badges.map((b) => b.type).toSet(), {'worker', 'agent', 'project'});
    });

    test('badge ordering respects priority', () {
      final badges = composer.compose(
        worker: WorkerConfig(id: 'w1', name: 'Home', host: 'localhost', apiKey: ''),
        agentType: 'claude_code',
        projectPath: '/home/user/Proj',
        worktreePath: '/home/user/Proj-wt',
      );
      final priorities = badges.map((b) => b.priority).toList();
      for (int i = 0; i < priorities.length - 1; i++) {
        expect(priorities[i], lessThanOrEqualTo(priorities[i + 1]),
            reason: 'badge[$i] priority must be <= badge[${i + 1}]');
      }
    });
  });
}
