import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/badge_spec.dart';
import 'package:rcflowclient/ui/badges/badge_bar.dart';
import 'package:rcflowclient/ui/badges/badge_registry.dart';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Wrap a widget in the minimal material shell needed for widget tests.
Widget _shell(Widget child) => MaterialApp(home: Scaffold(body: child));

/// Register a simple text renderer for [type] that renders [BadgeSpec.label].
void _registerSimple(String type) {
  BadgeRegistry.instance.register(
    type,
    (context, badge) => Text(badge.label, key: ValueKey('badge_$type')),
  );
}

BadgeSpec _badge(
  String type,
  String label, {
  int priority = 0,
  bool visible = true,
}) =>
    BadgeSpec(
      type: type,
      label: label,
      priority: priority,
      visible: visible,
      interactive: false,
    );

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  setUpAll(() {
    _registerSimple('status');
    _registerSimple('worker');
    _registerSimple('agent');
    _registerSimple('project');
    _registerSimple('caveman');
  });

  group('BadgeBar', () {
    testWidgets('renders nothing when badge list is empty', (tester) async {
      await tester.pumpWidget(_shell(const BadgeBar(badges: [])));
      expect(find.byType(SizedBox), findsWidgets); // SizedBox.shrink
      expect(find.byType(Text), findsNothing);
    });

    testWidgets('renders nothing when all badges are invisible', (tester) async {
      final badges = [
        _badge('status', 'active', visible: false),
        _badge('caveman', 'Caveman', visible: false),
      ];
      await tester.pumpWidget(_shell(BadgeBar(badges: badges)));
      expect(find.byType(Text), findsNothing);
    });

    testWidgets('renders visible badges', (tester) async {
      final badges = [
        _badge('status', 'active'),
        _badge('worker', 'HomeServer'),
      ];
      await tester.pumpWidget(_shell(BadgeBar(badges: badges)));
      await tester.pump();
      expect(find.text('active'), findsOneWidget);
      expect(find.text('HomeServer'), findsOneWidget);
    });

    testWidgets('respects slotFilter — shows only matching types', (tester) async {
      final badges = [
        _badge('status', 'active'),
        _badge('caveman', 'Caveman'),
        _badge('worker', 'Home'),
        _badge('agent', 'claude_code'),
      ];
      await tester.pumpWidget(_shell(
        BadgeBar(badges: badges, slotFilter: {'status', 'caveman'}),
      ));
      await tester.pump();
      expect(find.text('active'), findsOneWidget);
      expect(find.text('Caveman'), findsOneWidget);
      expect(find.text('Home'), findsNothing);
      expect(find.text('claude_code'), findsNothing);
    });

    testWidgets('renders badges sorted by priority', (tester) async {
      // Intentionally out-of-priority order
      final badges = [
        _badge('caveman', 'Caveman', priority: 50),
        _badge('status', 'active', priority: 0),
        _badge('worker', 'Home', priority: 10),
      ];
      await tester.pumpWidget(_shell(BadgeBar(badges: badges)));
      await tester.pump();

      // Collect x-positions of the rendered text widgets
      final active = tester.getTopLeft(find.text('active')).dx;
      final home = tester.getTopLeft(find.text('Home')).dx;
      final caveman = tester.getTopLeft(find.text('Caveman')).dx;

      expect(active, lessThan(home), reason: 'status (0) before worker (10)');
      expect(home, lessThan(caveman), reason: 'worker (10) before caveman (50)');
    });

    testWidgets('unknown badge type renders generic grey chip', (tester) async {
      // Do not register 'mystery' type — it should fall through to generic chip.
      final badges = [
        BadgeSpec(
          type: 'mystery',
          label: 'Mystery',
          priority: 99,
          visible: true,
          interactive: false,
        ),
      ];
      await tester.pumpWidget(_shell(BadgeBar(badges: badges)));
      await tester.pump();
      // Generic chip renders the label text
      expect(find.text('Mystery'), findsOneWidget);
    });

    testWidgets('invisible badge with slot matching is still hidden', (tester) async {
      final badges = [
        _badge('status', 'active', visible: false),
      ];
      await tester.pumpWidget(_shell(
        BadgeBar(badges: badges, slotFilter: {'status'}),
      ));
      await tester.pump();
      expect(find.text('active'), findsNothing);
    });
  });
}
