import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/worker_config.dart';

void main() {
  Map<String, dynamic> _base() => {
        'id': 'abc',
        'name': 'Test',
        'host': '127.0.0.1',
        'port': 53890,
        'api_key': 'key',
      };

  group('WorkerConfig.fromJson', () {
    test('reads default_agent when present and valid', () {
      final cfg =
          WorkerConfig.fromJson({..._base(), 'default_agent': 'claude_code'});
      expect(cfg.defaultAgent, 'claude_code');
    });

    test('reads codex default_agent', () {
      final cfg = WorkerConfig.fromJson({..._base(), 'default_agent': 'codex'});
      expect(cfg.defaultAgent, 'codex');
    });

    test('reads opencode default_agent', () {
      final cfg =
          WorkerConfig.fromJson({..._base(), 'default_agent': 'opencode'});
      expect(cfg.defaultAgent, 'opencode');
    });

    test('null when default_agent key is absent (backward compat)', () {
      final cfg = WorkerConfig.fromJson(_base());
      expect(cfg.defaultAgent, isNull);
    });

    test('null for unknown default_agent value (forward compat)', () {
      final cfg =
          WorkerConfig.fromJson({..._base(), 'default_agent': 'gpt_pilot'});
      expect(cfg.defaultAgent, isNull);
    });
  });

  group('WorkerConfig.toJson', () {
    test('omits default_agent when null', () {
      final cfg = WorkerConfig(id: '1', name: 'x', host: 'h', apiKey: 'k');
      expect(cfg.toJson().containsKey('default_agent'), isFalse);
    });

    test('includes default_agent when set', () {
      final cfg = WorkerConfig(
          id: '1', name: 'x', host: 'h', apiKey: 'k', defaultAgent: 'codex');
      expect(cfg.toJson()['default_agent'], 'codex');
    });

    test('roundtrip preserves defaultAgent', () {
      final original = WorkerConfig(
          id: '1',
          name: 'x',
          host: 'h',
          apiKey: 'k',
          defaultAgent: 'opencode');
      final restored = WorkerConfig.fromJson(original.toJson());
      expect(restored.defaultAgent, 'opencode');
    });
  });

  group('WorkerConfig.copyWith', () {
    test('can set defaultAgent', () {
      final cfg = WorkerConfig(id: '1', name: 'x', host: 'h', apiKey: 'k');
      final copy = cfg.copyWith(defaultAgent: 'claude_code');
      expect(copy.defaultAgent, 'claude_code');
    });

    test('can clear defaultAgent to null', () {
      final cfg = WorkerConfig(
          id: '1', name: 'x', host: 'h', apiKey: 'k', defaultAgent: 'codex');
      final copy = cfg.copyWith(defaultAgent: null);
      expect(copy.defaultAgent, isNull);
    });

    test('preserves defaultAgent when not specified', () {
      final cfg = WorkerConfig(
          id: '1', name: 'x', host: 'h', apiKey: 'k', defaultAgent: 'codex');
      final copy = cfg.copyWith(name: 'y');
      expect(copy.defaultAgent, 'codex');
    });
  });
}
