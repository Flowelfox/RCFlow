import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/ui/utils/link_utils.dart';
import 'package:url_launcher/url_launcher.dart';

void main() {
  group('openLinkOnCtrlClick', () {
    late List<Uri> launched;
    late Future<bool> Function(Uri, {LaunchMode mode}) fakeLauncher;

    setUp(() {
      launched = [];
      fakeLauncher = (uri, {mode = LaunchMode.platformDefault}) async {
        launched.add(uri);
        return true;
      };
    });

    test('opens URL when Ctrl is held and href is valid', () {
      openLinkOnCtrlClick(
        'Example',
        'https://example.com',
        '',
        isCtrlPressed: true,
        launcher: fakeLauncher,
      );

      expect(launched, [Uri.parse('https://example.com')]);
    });

    test('does nothing when Ctrl is NOT held', () {
      openLinkOnCtrlClick(
        'Example',
        'https://example.com',
        '',
        isCtrlPressed: false,
        launcher: fakeLauncher,
      );

      expect(launched, isEmpty);
    });

    test('does nothing when href is null', () {
      openLinkOnCtrlClick(
        'Example',
        null,
        '',
        isCtrlPressed: true,
        launcher: fakeLauncher,
      );

      expect(launched, isEmpty);
    });

    test('does nothing when href is unparseable', () {
      openLinkOnCtrlClick(
        'Example',
        ':::not a uri:::',
        '',
        isCtrlPressed: true,
        launcher: fakeLauncher,
      );

      expect(launched, isEmpty);
    });

    test('opens relative href when Ctrl is held', () {
      openLinkOnCtrlClick(
        'Docs',
        '/docs/page',
        '',
        isCtrlPressed: true,
        launcher: fakeLauncher,
      );

      expect(launched, [Uri.parse('/docs/page')]);
    });
  });
}
