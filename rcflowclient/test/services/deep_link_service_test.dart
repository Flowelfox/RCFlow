import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/services/deep_link_service.dart';

void main() {
  group('AddWorkerLink.tryParse', () {
    test('parses a full rcflow://add-worker URL', () {
      final uri = Uri.parse(
        'rcflow://add-worker?host=127.0.0.1&port=53890&token=secret&ssl=1&name=Desk',
      );
      final link = AddWorkerLink.tryParse(uri);
      expect(link, isNotNull);
      expect(link!.host, '127.0.0.1');
      expect(link.port, 53890);
      expect(link.token, 'secret');
      expect(link.ssl, isTrue);
      expect(link.name, 'Desk');
    });

    test('treats ssl != 1 as false', () {
      final uri = Uri.parse(
        'rcflow://add-worker?host=h&port=1&token=t&ssl=0',
      );
      final link = AddWorkerLink.tryParse(uri);
      expect(link?.ssl, isFalse);
    });

    test('drops empty name', () {
      final uri = Uri.parse(
        'rcflow://add-worker?host=h&port=1&token=t&ssl=0&name=',
      );
      expect(AddWorkerLink.tryParse(uri)?.name, isNull);
    });

    test('rejects unknown scheme', () {
      final uri = Uri.parse(
        'other://add-worker?host=h&port=1&token=t',
      );
      expect(AddWorkerLink.tryParse(uri), isNull);
    });

    test('rejects unknown host/action', () {
      final uri = Uri.parse(
        'rcflow://something-else?host=h&port=1&token=t',
      );
      expect(AddWorkerLink.tryParse(uri), isNull);
    });

    test('rejects missing required fields', () {
      expect(
        AddWorkerLink.tryParse(
          Uri.parse('rcflow://add-worker?host=&port=1&token=t'),
        ),
        isNull,
      );
      expect(
        AddWorkerLink.tryParse(
          Uri.parse('rcflow://add-worker?host=h&port=abc&token=t'),
        ),
        isNull,
      );
      expect(
        AddWorkerLink.tryParse(
          Uri.parse('rcflow://add-worker?host=h&port=1&token='),
        ),
        isNull,
      );
    });

    test('rejects out-of-range ports', () {
      expect(
        AddWorkerLink.tryParse(
          Uri.parse('rcflow://add-worker?host=h&port=0&token=t'),
        ),
        isNull,
      );
      expect(
        AddWorkerLink.tryParse(
          Uri.parse('rcflow://add-worker?host=h&port=65536&token=t'),
        ),
        isNull,
      );
    });

    test('URL-decodes reserved token chars', () {
      final uri = Uri.parse(
        'rcflow://add-worker?host=h&port=1&token=a%2Bb%2Fc%3Dd%26e&ssl=0',
      );
      expect(AddWorkerLink.tryParse(uri)?.token, 'a+b/c=d&e');
    });
  });
}
