/// Unit tests for [HttpUpdateFetcher] helpers.
///
/// The actual HTTP request is not exercised — this file focuses on the
/// version-normalisation logic which is fully synchronous and deterministic.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/services/update_fetcher.dart';

void main() {
  group('HttpUpdateFetcher.normalizeVersion', () {
    test('strips leading lowercase v', () {
      expect(HttpUpdateFetcher.normalizeVersion('v1.38.0'), '1.38.0');
    });

    test('strips leading uppercase V', () {
      expect(HttpUpdateFetcher.normalizeVersion('V1.38.0'), '1.38.0');
    });

    test('strips +build suffix', () {
      expect(HttpUpdateFetcher.normalizeVersion('1.38.0+75'), '1.38.0');
    });

    test('strips both leading v and +build suffix', () {
      expect(HttpUpdateFetcher.normalizeVersion('v1.38.0+75'), '1.38.0');
    });

    test('leaves plain version untouched', () {
      expect(HttpUpdateFetcher.normalizeVersion('1.38.0'), '1.38.0');
    });

    test('trims surrounding whitespace', () {
      expect(HttpUpdateFetcher.normalizeVersion('  v1.38.0  '), '1.38.0');
    });

    test('handles single-segment version', () {
      expect(HttpUpdateFetcher.normalizeVersion('v2'), '2');
    });
  });
}
