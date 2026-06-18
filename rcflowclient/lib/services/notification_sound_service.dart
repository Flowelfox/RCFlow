import 'dart:io';
import 'dart:isolate';

import 'package:audioplayers/audioplayers.dart';
import 'package:ffi/ffi.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart' show rootBundle;
import 'package:win32/win32.dart' show PlaySound;

import 'settings_service.dart';

class NotificationSoundInfo {
  final String id;
  final String label;

  const NotificationSoundInfo({required this.id, required this.label});
}

const defaultSounds = [
  NotificationSoundInfo(id: 'gentle_chime', label: 'Gentle Chime'),
  NotificationSoundInfo(id: 'soft_ping', label: 'Soft Ping'),
  NotificationSoundInfo(id: 'subtle_pop', label: 'Subtle Pop'),
  NotificationSoundInfo(id: 'bell', label: 'Bell'),
  NotificationSoundInfo(id: 'digital_tone', label: 'Digital Tone'),
  NotificationSoundInfo(id: 'notif', label: 'Nova Pulse'),
];

// PlaySound flags (winmm). Defined locally so we don't depend on win32
// re-exporting them.  SND_SYNC is 0 (the default); we play synchronously on a
// background isolate because SND_ASYNC's single playback slot wedges after the
// first sound on this embedding (returns TRUE but emits nothing).
const int _sndNodefault = 0x0002; // silence (not the system default) on error
const int _sndFilename = 0x00020000; // pszSound is a file name

/// Plays [path] to completion via the native winmm `PlaySound` (synchronous).
/// Top-level so it can run inside [Isolate.run] off the UI thread.
int _playSoundSyncBlocking(String path) {
  final pszSound = path.toNativeUtf16();
  try {
    return PlaySound(pszSound, 0, _sndFilename | _sndNodefault);
  } finally {
    malloc.free(pszSound);
  }
}

/// Plays short notification sounds.
///
/// Windows uses the native `PlaySound` (winmm) API directly instead of
/// `audioplayers`.  The `audioplayers` Windows backend (Windows Media
/// Foundation) proved unreliable here: the first play worked but every
/// subsequent play was silent, and it logs "channel sent a message from native
/// to Flutter on a non-platform thread" errors.  `PlaySound` is the canonical
/// Win32 WAV API — it replays reliably, works while the window is unfocused,
/// and uses no platform event channel.  All other platforms keep
/// `audioplayers`.
class NotificationSoundService {
  final SettingsService _settings;

  // audioplayers instance — only created/used on non-Windows platforms.
  AudioPlayer? _player;

  // Cache of built-in asset sounds extracted to temp files (Windows only):
  // soundId -> absolute temp-file path.  PlaySound needs a real file path,
  // but bundled assets live inside the app, so each is written out once.
  final Map<String, String> _extractedPaths = {};

  NotificationSoundService({
    required SettingsService settings,
    AudioPlayer? player,
  }) : _settings = settings,
       _player = Platform.isWindows ? null : (player ?? AudioPlayer());

  /// Plays the "Sound when done" (completion) sound.
  Future<void> playCompletionSound() async {
    await _playSound(
      _settings.completionSound,
      _settings.completionCustomSoundPath,
    );
  }

  /// Plays the "Sound on message" sound.
  Future<void> playMessageSound() async {
    await _playSound(_settings.messageSound, _settings.messageCustomSoundPath);
  }

  /// Plays [soundId] (a preset id, or 'custom' with [customPath]) for previews.
  Future<void> previewSound(String soundId, {String customPath = ''}) async {
    await _playSound(soundId, customPath);
  }

  Future<void> _playSound(String soundId, String customPath) async {
    try {
      if (Platform.isWindows) {
        await _playWindows(soundId, customPath);
      } else {
        await _playAudioplayers(soundId, customPath);
      }
    } catch (e) {
      // Non-fatal: a failed notification sound must not break the app, but
      // log it so playback problems are diagnosable.
      debugPrint('NotificationSoundService: playback failed for "$soundId": $e');
    }
  }

  /// Native Windows playback via winmm `PlaySound`, run synchronously on a
  /// background isolate so it plays to completion reliably on every call
  /// without blocking the UI thread.
  Future<void> _playWindows(String soundId, String customPath) async {
    final String path;
    if (soundId == 'custom') {
      if (customPath.isEmpty) return;
      path = customPath;
    } else {
      final extracted = await _windowsAssetPath(soundId);
      if (extracted == null) return;
      path = extracted;
    }

    await Isolate.run(() => _playSoundSyncBlocking(path));
  }

  /// Returns an on-disk path to the built-in WAV [soundId] for `PlaySound`.
  ///
  /// Prefers the asset Flutter already ships inside the install folder
  /// (`<exe dir>\data\flutter_assets\assets\sounds\<id>.wav`) so nothing is
  /// written out.  Falls back to extracting the asset to a temp file if that
  /// layout isn't found (e.g. an unusual packaging).
  Future<String?> _windowsAssetPath(String soundId) async {
    final cached = _extractedPaths[soundId];
    if (cached != null && File(cached).existsSync()) return cached;

    final exeDir = File(Platform.resolvedExecutable).parent.path;
    final bundled = File(
      '$exeDir\\data\\flutter_assets\\assets\\sounds\\$soundId.wav',
    );
    if (bundled.existsSync()) {
      _extractedPaths[soundId] = bundled.path;
      return bundled.path;
    }

    final data = await rootBundle.load('assets/sounds/$soundId.wav');
    final file = File('${Directory.systemTemp.path}/rcflow_sound_$soundId.wav');
    await file.writeAsBytes(
      data.buffer.asUint8List(data.offsetInBytes, data.lengthInBytes),
      flush: true,
    );
    _extractedPaths[soundId] = file.path;
    return file.path;
  }

  /// audioplayers playback path (non-Windows platforms).
  Future<void> _playAudioplayers(String soundId, String customPath) async {
    final player = _player ??= AudioPlayer();
    await player.stop();
    if (soundId == 'custom') {
      if (customPath.isNotEmpty) {
        await player.play(DeviceFileSource(customPath));
      }
    } else {
      await player.play(AssetSource('sounds/$soundId.wav'));
    }
  }

  /// Validate a custom WAV file: must exist, be .wav, and < 10 seconds.
  /// Returns null if valid, or an error message string.
  Future<String?> validateCustomSound(String path) async {
    final file = File(path);
    if (!await file.exists()) return 'File not found';

    final ext = path.split('.').last.toLowerCase();
    if (ext != 'wav') return 'Only .wav files are supported';

    final bytes = await file.readAsBytes();
    if (bytes.length < 44) return 'Invalid WAV file';

    // Parse WAV header to get duration
    final sampleRate =
        bytes[24] | (bytes[25] << 8) | (bytes[26] << 16) | (bytes[27] << 24);
    final dataSize =
        bytes[40] | (bytes[41] << 8) | (bytes[42] << 16) | (bytes[43] << 24);
    final bytesPerSec =
        bytes[28] | (bytes[29] << 8) | (bytes[30] << 16) | (bytes[31] << 24);

    if (sampleRate <= 0 || bytesPerSec <= 0) return 'Invalid WAV header';

    final durationSec = dataSize / bytesPerSec;
    if (durationSec > 10.0) {
      return 'Sound must be shorter than 10 seconds '
          '(${durationSec.toStringAsFixed(1)}s)';
    }

    return null;
  }

  void dispose() {
    _player?.dispose();
  }
}
