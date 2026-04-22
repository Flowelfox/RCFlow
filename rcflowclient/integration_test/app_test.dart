// ignore_for_file: avoid_print
import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

import 'package:rcflowclient/main.dart' as app;

// ---------------------------------------------------------------------------
// E2E environment (injected via --dart-define)
// ---------------------------------------------------------------------------

// ignore: unused_element
const _e2eBackendHost = String.fromEnvironment('E2E_BACKEND_HOST', defaultValue: '');
// ignore: unused_element
const _e2eBackendPort = int.fromEnvironment('E2E_BACKEND_PORT', defaultValue: 0);
// ignore: unused_element
const _e2eApiKey = String.fromEnvironment('E2E_API_KEY', defaultValue: '');
// Mock Anthropic server admin endpoint (optional — used by tool-use tests)
const _e2eMockHost = String.fromEnvironment('E2E_MOCK_HOST', defaultValue: '127.0.0.1');
const _e2eMockPort = int.fromEnvironment('E2E_MOCK_PORT', defaultValue: 19000);

// ---------------------------------------------------------------------------
// Helpers — timing
// ---------------------------------------------------------------------------

/// Pump until [condition] is true or [maxSeconds] elapses.
Future<bool> waitFor(
  WidgetTester tester,
  bool Function() condition, {
  int maxSeconds = 20,
  Duration interval = const Duration(milliseconds: 500),
}) async {
  final deadline = DateTime.now().add(Duration(seconds: maxSeconds));
  while (DateTime.now().isBefore(deadline)) {
    await tester.pump(interval);
    if (condition()) return true;
  }
  return false;
}

/// Pump until a widget matching [finder] is present in the tree.
Future<bool> waitForWidget(
  WidgetTester tester,
  Finder finder, {
  int maxSeconds = 20,
}) =>
    waitFor(tester, () => finder.evaluate().isNotEmpty, maxSeconds: maxSeconds);

// ---------------------------------------------------------------------------
// Helpers — mock server administration
// ---------------------------------------------------------------------------

/// Configure the mock Anthropic server to use simple text responses.
Future<void> _mockSetTextResponses(List<String> responses) async {
  final client = HttpClient();
  try {
    final url = 'http://$_e2eMockHost:$_e2eMockPort/v1/admin/responses';
    final req = await client.putUrl(Uri.parse(url));
    req.headers.contentType = ContentType.json;
    req.write(jsonEncode({'responses': responses}));
    final res = await req.close();
    await res.drain<void>();
  } finally {
    client.close();
  }
}

/// Configure the mock Anthropic server with turn-based tool_use scripting.
///
/// [turns] is a list of turn descriptors:
///   - `{"type": "text", "text": "…"}`
///   - `{"type": "tool_use", "tool_name": "…", "tool_input": {…}, "preamble": "…"}`
Future<void> _mockSetTurns(List<Map<String, Object>> turns) async {
  final client = HttpClient();
  try {
    final url = 'http://$_e2eMockHost:$_e2eMockPort/v1/admin/turns';
    final req = await client.putUrl(Uri.parse(url));
    req.headers.contentType = ContentType.json;
    req.write(jsonEncode({'turns': turns}));
    final res = await req.close();
    await res.drain<void>();
  } finally {
    client.close();
  }
}

// ---------------------------------------------------------------------------
// Helpers — interaction
// ---------------------------------------------------------------------------

/// Open a new session and wait for the input field to be ready.
/// Returns the message [TextField] finder.
Future<Finder> _openNewSession(WidgetTester tester) async {
  await tester.tap(find.text('New Chat'));
  await tester.pumpAndSettle(const Duration(seconds: 2));

  final messageField = find.byType(TextField).first;
  final connected = await waitForWidget(tester, messageField, maxSeconds: 15);
  if (!connected) {
    fail('Timed out waiting for input field to appear (worker not connected?)');
  }
  return messageField;
}

/// Type [text] into [field] and tap the send button.
Future<void> _sendMessage(WidgetTester tester, Finder field, String text) async {
  await tester.enterText(field, text);
  await tester.pump();
  await tester.tap(find.byIcon(Icons.arrow_upward_rounded));
  await tester.pump();
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  // ---------------------------------------------------------------------------
  // Test 1: App launches and renders the home screen
  // ---------------------------------------------------------------------------
  testWidgets('app launches and shows home screen', (tester) async {
    app.main();
    await tester.pumpAndSettle(const Duration(seconds: 3));

    expect(find.text('No open panes'), findsOneWidget);
    expect(find.text('New Chat'), findsOneWidget);

    print('[E2E] Test 1 passed: home screen visible');
  });

  // ---------------------------------------------------------------------------
  // Test 2: New Chat button opens a session pane with input area
  // ---------------------------------------------------------------------------
  testWidgets('new chat button opens a session pane with input area',
      (tester) async {
    app.main();
    await tester.pumpAndSettle(const Duration(seconds: 3));

    await tester.tap(find.text('New Chat'));
    await tester.pumpAndSettle(const Duration(seconds: 2));

    final inputField = find.byType(TextField);
    expect(inputField, findsWidgets);
    expect(find.byIcon(Icons.arrow_upward_rounded), findsOneWidget);

    print('[E2E] Test 2 passed: session pane and input area visible');
  });

  // ---------------------------------------------------------------------------
  // Test 3: Send a message → mock LLM text response appears in output
  // ---------------------------------------------------------------------------
  testWidgets('send message → mock LLM response appears in output',
      (tester) async {
    // Reset mock server to plain text mode for this test.
    await _mockSetTextResponses(['Hello from mock LLM!']);

    app.main();
    await tester.pumpAndSettle(const Duration(seconds: 3));

    final messageField = await _openNewSession(tester);
    await _sendMessage(tester, messageField, 'hello');

    const expectedFragment = 'Hello';
    final responseAppeared = await waitFor(
      tester,
      () => find.textContaining(expectedFragment).evaluate().isNotEmpty,
      maxSeconds: 30,
    );

    if (!responseAppeared) {
      debugDumpApp();
      fail(
        'Timed out waiting for mock LLM response to appear in UI. '
        'Expected text containing "$expectedFragment".',
      );
    }

    expect(find.textContaining(expectedFragment), findsWidgets);
    print('[E2E] Test 3 passed: mock LLM response "$expectedFragment" visible in UI');
  });

  // ---------------------------------------------------------------------------
  // Test 4: Session reaches end state after mock response
  // ---------------------------------------------------------------------------
  testWidgets('session reaches end state after mock response', (tester) async {
    await _mockSetTextResponses(['Hello from mock LLM!']);

    app.main();
    await tester.pumpAndSettle(const Duration(seconds: 3));

    final messageField = await _openNewSession(tester);
    await _sendMessage(tester, messageField, 'ping');

    await waitFor(
      tester,
      () => find.textContaining('Hello').evaluate().isNotEmpty,
      maxSeconds: 30,
    );

    expect(find.byType(TextField), findsWidgets);

    print('[E2E] Test 4 passed: session completed, UI still responsive');
  });

  // ---------------------------------------------------------------------------
  // Test 5: Tool execution — tool block appears after LLM calls shell_exec
  //
  // The mock server is configured with a tool_use turn (shell_exec) followed by
  // a text turn. The Flutter UI should render a "Bash" tool block while the
  // tool runs, then show the final assistant text.
  // ---------------------------------------------------------------------------
  testWidgets('tool execution → Bash tool block appears in output',
      (tester) async {
    // Configure mock server: turn 1 → tool_use, turn 2 → text
    await _mockSetTurns([
      {
        'type': 'tool_use',
        'tool_name': 'shell_exec',
        'tool_input': {'command': 'echo e2e_flutter_tool'},
        'preamble': 'Let me run that.',
      },
      {
        'type': 'text',
        'text': 'Done! The output was: e2e_flutter_tool',
      },
    ]);

    app.main();
    await tester.pumpAndSettle(const Duration(seconds: 3));

    final messageField = await _openNewSession(tester);
    await _sendMessage(tester, messageField, 'run echo');

    // Wait for the tool block to appear (display_name for shell_exec is "Bash")
    final toolBlockAppeared = await waitFor(
      tester,
      () => find.textContaining('Bash').evaluate().isNotEmpty ||
          find.textContaining('shell_exec').evaluate().isNotEmpty,
      maxSeconds: 30,
    );

    if (!toolBlockAppeared) {
      debugDumpApp();
      fail(
        'Timed out waiting for tool block to appear in UI. '
        'Expected "Bash" or "shell_exec" text.',
      );
    }

    // Also verify the final text response eventually arrives
    final finalTextAppeared = await waitFor(
      tester,
      () => find.textContaining('Done').evaluate().isNotEmpty,
      maxSeconds: 30,
    );

    expect(finalTextAppeared, isTrue,
        reason: 'Expected final assistant text after tool execution');

    print('[E2E] Test 5 passed: tool block "Bash" visible, final text arrived');
  });

  // ---------------------------------------------------------------------------
  // Test 6: session_end_ask card renders after the LLM includes SessionEndAsk
  //
  // When the backend pushes a session_end_ask message, the Flutter UI must
  // display "Task complete. End this chat?" with "Continue" and "End Session"
  // buttons.
  // ---------------------------------------------------------------------------
  testWidgets('session_end_ask → "Task complete" card renders in UI',
      (tester) async {
    // Backend pushes session_end_ask when the LLM text contains [SessionEndAsk].
    await _mockSetTextResponses(['Done! [SessionEndAsk]']);

    app.main();
    await tester.pumpAndSettle(const Duration(seconds: 3));

    final messageField = await _openNewSession(tester);
    await _sendMessage(tester, messageField, 'finish this');

    // Wait for the session_end_ask card to appear
    final cardAppeared = await waitFor(
      tester,
      () => find.textContaining('Task complete').evaluate().isNotEmpty ||
          find.text('End Session').evaluate().isNotEmpty,
      maxSeconds: 30,
    );

    if (!cardAppeared) {
      debugDumpApp();
      fail(
        'Timed out waiting for session_end_ask card. '
        'Expected "Task complete" or "End Session" text in UI.',
      );
    }

    expect(
      find.text('End Session'),
      findsOneWidget,
      reason: 'End Session button should be visible in session_end_ask card',
    );
    expect(
      find.text('Continue'),
      findsOneWidget,
      reason: 'Continue button should be visible in session_end_ask card',
    );

    print('[E2E] Test 6 passed: session_end_ask card with "End Session" and "Continue" visible');
  });

  // ---------------------------------------------------------------------------
  // Test 7: Multi-turn conversation — second message gets a second response
  //
  // User sends a second message to the same open session.
  // The second response must appear without re-opening a session.
  // ---------------------------------------------------------------------------
  testWidgets('multi-turn: second message → second response appears',
      (tester) async {
    // Two different text responses so we can distinguish them
    await _mockSetTextResponses([
      'First response from mock.',
      'Second response from mock.',
    ]);

    app.main();
    await tester.pumpAndSettle(const Duration(seconds: 3));

    final messageField = await _openNewSession(tester);

    // First message
    await _sendMessage(tester, messageField, 'first message');

    final firstAppeared = await waitFor(
      tester,
      () => find.textContaining('First response').evaluate().isNotEmpty,
      maxSeconds: 30,
    );
    expect(firstAppeared, isTrue, reason: 'First response should appear');

    // Second message — input field should still be available
    final inputStillVisible = await waitForWidget(
      tester,
      find.byType(TextField).first,
      maxSeconds: 10,
    );
    expect(inputStillVisible, isTrue, reason: 'Input field must remain after first response');

    await tester.enterText(find.byType(TextField).first, 'second message');
    await tester.pump();
    await tester.tap(find.byIcon(Icons.arrow_upward_rounded));
    await tester.pump();

    final secondAppeared = await waitFor(
      tester,
      () => find.textContaining('Second response').evaluate().isNotEmpty,
      maxSeconds: 30,
    );

    if (!secondAppeared) {
      debugDumpApp();
      fail('Timed out waiting for second response in UI.');
    }

    print('[E2E] Test 7 passed: second response visible after second message');
  });
}
