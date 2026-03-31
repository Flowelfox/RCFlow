import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';

import 'package:rcflowclient/main.dart';
import 'package:rcflowclient/state/app_state.dart';
import 'package:rcflowclient/services/settings_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  testWidgets('App renders RCFlow title and empty state', (
    WidgetTester tester,
  ) async {
    SharedPreferences.setMockInitialValues({});
    final settings = SettingsService();
    await settings.init();

    await tester.pumpWidget(
      ChangeNotifierProvider(
        create: (_) => AppState(settings: settings),
        child: const RCFlowApp(),
      ),
    );

    expect(find.text('RCFlow'), findsOneWidget);
    expect(find.text('Welcome to RCFlow'), findsOneWidget);
  });
}
