import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/badge_spec.dart';
import 'package:rcflowclient/ui/badges/badge_registry.dart';
import 'package:rcflowclient/ui/badges/renderers/agent_badge_renderer.dart';

Widget _shell(Widget child) => MaterialApp(home: Scaffold(body: child));

BadgeSpec _agentBadge(String label) => BadgeSpec(
      type: 'agent',
      label: label,
      priority: BadgePriority.agent,
      visible: true,
      interactive: false,
      payload: {'agent_type': label},
    );

void main() {
  setUpAll(() {
    registerAgentBadge(BadgeRegistry.instance);
  });

  group('AgentBadgeRenderer', () {
    testWidgets('renders "Claude Code" for raw type claude_code', (tester) async {
      final badge = _agentBadge('claude_code');
      await tester.pumpWidget(
        _shell(Builder(
          builder: (ctx) => BadgeRegistry.instance.render(ctx, badge),
        )),
      );
      expect(find.text('Claude Code'), findsOneWidget);
    });

    testWidgets('renders "Claude Code" for server label ClaudeCode', (tester) async {
      final badge = _agentBadge('ClaudeCode');
      await tester.pumpWidget(
        _shell(Builder(
          builder: (ctx) => BadgeRegistry.instance.render(ctx, badge),
        )),
      );
      expect(find.text('Claude Code'), findsOneWidget);
    });

    testWidgets('renders "Codex" for both raw and display label', (tester) async {
      for (final label in ['codex', 'Codex']) {
        await tester.pumpWidget(
          _shell(Builder(
            builder: (ctx) => BadgeRegistry.instance.render(ctx, _agentBadge(label)),
          )),
        );
        expect(find.text('Codex'), findsOneWidget);
      }
    });

    testWidgets('renders "OpenCode" for both raw and display label', (tester) async {
      for (final label in ['opencode', 'OpenCode']) {
        await tester.pumpWidget(
          _shell(Builder(
            builder: (ctx) => BadgeRegistry.instance.render(ctx, _agentBadge(label)),
          )),
        );
        expect(find.text('OpenCode'), findsOneWidget);
      }
    });

    testWidgets('renders unknown agent type as-is', (tester) async {
      final badge = _agentBadge('my_custom_agent');
      await tester.pumpWidget(
        _shell(Builder(
          builder: (ctx) => BadgeRegistry.instance.render(ctx, badge),
        )),
      );
      expect(find.text('my_custom_agent'), findsOneWidget);
    });
  });
}
