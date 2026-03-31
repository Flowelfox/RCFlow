/// Tests for NotificationSoundService, focusing on the Windows-specific
/// seek+resume optimisation that fixes silent notifications when the app
/// window is not focused.
library;

import 'dart:io';

import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/services/notification_sound_service.dart';
import 'package:rcflowclient/services/settings_service.dart';
import 'package:shared_preferences/shared_preferences.dart';

// ---------------------------------------------------------------------------
// Platform-channel mock helper
//
// AudioPlayer's constructor lazily creates the player and listens on event
// channels.  We need to:
//   1. Call TestWidgetsFlutterBinding.ensureInitialized() before any
//      AudioPlayer is created.
//   2. Mock the global method+event channels that audioplayers accesses
//      during GlobalAudioScope initialisation.
//   3. Mock the per-player method channel used for play/stop/seek/resume.
// ---------------------------------------------------------------------------

void _setupAudioPlayerMocks(List<MethodCall> recorded) {
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;

  // Global method channel
  messenger.setMockMethodCallHandler(
    const MethodChannel('xyz.luan/audioplayers.global'),
    (call) async => null,
  );

  // Per-player method channel — record calls for inspection
  messenger.setMockMethodCallHandler(
    const MethodChannel('xyz.luan/audioplayers'),
    (call) async {
      recorded.add(call);
      return null;
    },
  );

  // Global event channel — must return a valid stream handler response
  messenger.setMockStreamHandler(
    const EventChannel('xyz.luan/audioplayers.global/events'),
    MockStreamHandler.inline(onListen: (args, sink) {}),
  );
}

void _teardownAudioPlayerMocks() {
  final messenger =
      TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
  messenger.setMockMethodCallHandler(
    const MethodChannel('xyz.luan/audioplayers.global'),
    null,
  );
  messenger.setMockMethodCallHandler(
    const MethodChannel('xyz.luan/audioplayers'),
    null,
  );
  messenger.setMockStreamHandler(
    const EventChannel('xyz.luan/audioplayers.global/events'),
    null,
  );
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();

  // -------------------------------------------------------------------------
  // validateCustomSound — pure Dart logic, no plugin required
  // -------------------------------------------------------------------------

  group('validateCustomSound', () {
    late Directory tmp;
    late SettingsService settings;
    late NotificationSoundService svc;
    final calls = <MethodCall>[];

    setUpAll(() => _setupAudioPlayerMocks(calls));
    tearDownAll(_teardownAudioPlayerMocks);

    setUp(() async {
      calls.clear();
      tmp = await Directory.systemTemp.createTemp('rcflow_snd_test_');
      SharedPreferences.setMockInitialValues({});
      settings = SettingsService();
      await settings.init();
      svc = NotificationSoundService(settings: settings);
    });

    tearDown(() async {
      svc.dispose();
      await tmp.delete(recursive: true);
    });

    test('returns error when file does not exist', () async {
      final result = await svc.validateCustomSound('${tmp.path}/nope.wav');
      expect(result, 'File not found');
    });

    test('returns error for non-wav extension', () async {
      final f = File('${tmp.path}/sound.mp3')..writeAsBytesSync([0, 1, 2, 3]);
      final result = await svc.validateCustomSound(f.path);
      expect(result, 'Only .wav files are supported');
    });

    test('returns error when file is too short to be a valid WAV', () async {
      final f = File('${tmp.path}/short.wav')
        ..writeAsBytesSync(List.filled(20, 0));
      final result = await svc.validateCustomSound(f.path);
      expect(result, 'Invalid WAV file');
    });

    test('returns error for invalid WAV header (zero sample rate)', () async {
      // 44-byte header with sample rate = 0
      final header = List<int>.filled(44, 0);
      final f = File('${tmp.path}/bad_header.wav')..writeAsBytesSync(header);
      final result = await svc.validateCustomSound(f.path);
      expect(result, 'Invalid WAV header');
    });

    test('returns error when WAV is longer than 10 seconds', () async {
      // Build a minimal 44-byte WAV header:
      //   sampleRate  (bytes 24-27) = 44100
      //   bytesPerSec (bytes 28-31) = 88200  (stereo 16-bit)
      //   dataSize    (bytes 40-43) = 882001  → ~10.0001 s > 10 s
      final header = List<int>.filled(44, 0);
      _writeLE32(header, 24, 44100);
      _writeLE32(header, 28, 88200);
      _writeLE32(header, 40, 882001);
      final f = File('${tmp.path}/long.wav')..writeAsBytesSync(header);
      final result = await svc.validateCustomSound(f.path);
      expect(result, startsWith('Sound must be shorter than 10 seconds'));
    });

    test('returns null for a valid short WAV', () async {
      // dataSize = 88200 → exactly 1 second at 88200 bytes/sec
      final header = List<int>.filled(44, 0);
      _writeLE32(header, 24, 44100);
      _writeLE32(header, 28, 88200);
      _writeLE32(header, 40, 88200);
      final f = File('${tmp.path}/ok.wav')..writeAsBytesSync(header);
      final result = await svc.validateCustomSound(f.path);
      expect(result, isNull);
    });
  });

  // -------------------------------------------------------------------------
  // Windows: ReleaseMode.stop is set on construction
  //
  // With ReleaseMode.stop, stopping the player keeps the source loaded so
  // subsequent seek(0)+resume() calls work without a WMF async reload cycle.
  // -------------------------------------------------------------------------

  group('Windows init sets ReleaseMode.stop', () {
    final calls = <MethodCall>[];

    setUpAll(() => _setupAudioPlayerMocks(calls));
    tearDownAll(_teardownAudioPlayerMocks);

    test('setReleaseMode(stop) is sent on Windows', () async {
      if (!Platform.isWindows) {
        // On non-Windows platforms the release-mode call is skipped.
        return;
      }

      calls.clear();
      SharedPreferences.setMockInitialValues({});
      final settings = SettingsService();
      await settings.init();
      final svc = NotificationSoundService(settings: settings);
      svc.dispose();

      expect(calls.map((c) => c.method), contains('setReleaseMode'));
      final call = calls.firstWhere((c) => c.method == 'setReleaseMode');
      // audioplayers encodes ReleaseMode.stop as the string 'ReleaseMode.stop'
      expect(call.arguments['releaseMode'], contains('stop'));
    });

    test('setReleaseMode is NOT sent on non-Windows platforms', () async {
      if (Platform.isWindows) return;

      calls.clear();
      SharedPreferences.setMockInitialValues({});
      final settings = SettingsService();
      await settings.init();
      final svc = NotificationSoundService(settings: settings);
      svc.dispose();

      expect(calls.map((c) => c.method), isNot(contains('setReleaseMode')));
    });
  });
}

// Write a 32-bit little-endian value into [buf] at [offset].
void _writeLE32(List<int> buf, int offset, int value) {
  buf[offset] = value & 0xFF;
  buf[offset + 1] = (value >> 8) & 0xFF;
  buf[offset + 2] = (value >> 16) & 0xFF;
  buf[offset + 3] = (value >> 24) & 0xFF;
}
