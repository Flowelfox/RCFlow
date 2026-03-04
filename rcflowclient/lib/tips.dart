import 'dart:math';

const List<String> _tips = [
  'Use the @ symbol to reference projects by name in your messages',
  'Use the # symbol to suggest which tools to use (e.g. #claude_code, #codex)',
  'Try the RCFlow Android app for on-the-go access to your sessions',
  'You can split the view to work with multiple sessions side by side',
  'Drag a session from the sidebar to split panes and multitask',
  'Long messages are automatically summarized for a cleaner chat view',
  'Use the session history to pick up where you left off',
  'RCFlow supports multiple workers — connect to different machines at once',
  'Tap the connection bar to quickly switch between configured servers',
  'Your session history is preserved even after disconnecting',
  'You can configure multiple servers and switch between them easily',
];

final _random = Random();

String getRandomTip() => _tips[_random.nextInt(_tips.length)];
