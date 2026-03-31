import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/subprocess_info.dart';

void main() {
  group('SubprocessInfo.fromJson', () {
    test('parses all fields correctly', () {
      final now = DateTime.utc(2026, 3, 20, 12, 0, 0);
      final info = SubprocessInfo.fromJson({
        'subprocess_type': 'claude_code',
        'display_name': 'Claude Code',
        'working_directory': '/home/user/project',
        'current_tool': 'Bash',
        'started_at': now.toIso8601String(),
      });

      expect(info.subprocessType, 'claude_code');
      expect(info.displayName, 'Claude Code');
      expect(info.workingDirectory, '/home/user/project');
      expect(info.currentTool, 'Bash');
      expect(info.startedAt.toUtc().year, 2026);
    });

    test('uses defaults for missing fields', () {
      final info = SubprocessInfo.fromJson({});

      expect(info.subprocessType, 'unknown');
      expect(info.displayName, 'Subprocess');
      expect(info.workingDirectory, '');
      expect(info.currentTool, isNull);
    });

    test('current_tool is null when not provided', () {
      final info = SubprocessInfo.fromJson({
        'subprocess_type': 'codex',
        'display_name': 'Codex',
        'working_directory': '/repo',
        'started_at': DateTime.now().toIso8601String(),
      });

      expect(info.currentTool, isNull);
    });

    test('falls back gracefully on invalid started_at', () {
      final before = DateTime.now();
      final info = SubprocessInfo.fromJson({
        'subprocess_type': 'claude_code',
        'started_at': 'not-a-date',
      });
      final after = DateTime.now();

      // Should use DateTime.now() as fallback — just verify it's recent
      expect(
        info.startedAt.isAfter(before.subtract(const Duration(seconds: 1))),
        isTrue,
      );
      expect(
        info.startedAt.isBefore(after.add(const Duration(seconds: 1))),
        isTrue,
      );
    });
  });

  group('SubprocessInfo.copyWith', () {
    test('updates currentTool', () {
      final info = SubprocessInfo(
        subprocessType: 'claude_code',
        displayName: 'Claude Code',
        workingDirectory: '/repo',
        startedAt: DateTime.utc(2026, 3, 20),
      );

      final updated = info.copyWith(currentTool: 'Write');

      expect(updated.currentTool, 'Write');
      expect(updated.subprocessType, 'claude_code');
      expect(updated.displayName, 'Claude Code');
    });

    test('retains existing currentTool when not overridden', () {
      final info = SubprocessInfo(
        subprocessType: 'codex',
        displayName: 'Codex',
        workingDirectory: '/repo',
        currentTool: 'Read',
        startedAt: DateTime.utc(2026, 3, 20),
      );

      final unchanged = info.copyWith();

      expect(unchanged.currentTool, 'Read');
    });
  });
}
