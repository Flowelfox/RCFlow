import 'dart:io';

import 'package:audioplayers/audioplayers.dart';

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

class NotificationSoundService {
  final SettingsService _settings;
  final AudioPlayer _player;

  // Tracks which source is currently loaded in the player.
  // Format: the soundId for built-in sounds, or 'custom:<path>' for custom files.
  String? _loadedKey;

  NotificationSoundService({
    required SettingsService settings,
    AudioPlayer? player,
  })  : _settings = settings,
        _player = player ?? AudioPlayer() {
    // On Windows, the audioplayers plugin uses Windows Media Foundation (WMF)
    // to load audio sources asynchronously.  When the app window is not
    // focused, Flutter's platform-event processing is in a reduced-activity
    // state, so the WMF "source loaded" callback never reaches Dart and
    // play() silently does nothing.
    //
    // Fix: use ReleaseMode.stop so that stopping the player keeps the source
    // loaded (instead of releasing it).  We pre-load the configured sound
    // during construction — while the window is guaranteed to be focused at
    // app startup — and then use seek(0)+resume() for playback.  Both of
    // those calls are synchronous platform-channel round-trips that complete
    // immediately without waiting for any WMF background callbacks.
    if (Platform.isWindows) {
      _player.setReleaseMode(ReleaseMode.stop);
      _preloadCurrentSound();
    }
  }

  /// Eagerly loads the currently configured sound into the player.
  /// Called once at construction while the window is focused.
  void _preloadCurrentSound() {
    final soundId = _settings.notificationSound;
    if (soundId == 'custom') return; // custom path may not exist yet
    _player
        .setSource(AssetSource('sounds/$soundId.wav'))
        .then((_) => _loadedKey = soundId)
        .catchError((_) {});
  }

  Future<void> playNotificationSound() async {
    final soundId = _settings.notificationSound;
    await _playSound(soundId);
  }

  Future<void> previewSound(String soundId) async {
    await _playSound(soundId);
  }

  Future<void> _playSound(String soundId) async {
    try {
      if (Platform.isWindows) {
        await _playSoundWindows(soundId);
      } else {
        await _playSoundDefault(soundId);
      }
    } catch (_) {
      // Silently ignore playback errors
    }
  }

  /// Windows-specific playback: avoid triggering an async WMF source reload
  /// when the app window may not be focused.
  Future<void> _playSoundWindows(String soundId) async {
    final String key;
    final Source source;

    if (soundId == 'custom') {
      final path = _settings.customSoundPath;
      if (path.isEmpty) return;
      key = 'custom:$path';
      source = DeviceFileSource(path);
    } else {
      key = soundId;
      source = AssetSource('sounds/$soundId.wav');
    }

    if (_loadedKey == key) {
      // Source already loaded — seek to beginning and resume.
      // These are synchronous platform-channel calls that return immediately
      // and do not wait for WMF callbacks, so they work even when the
      // app window is unfocused.
      await _player.seek(Duration.zero);
      await _player.resume();
    } else {
      // Different sound — must load it first.  This path requires the full
      // async cycle; it works reliably when triggered from the UI (focused).
      await _player.stop();
      await _player.play(source);
      _loadedKey = key;
    }
  }

  /// Default playback path used on non-Windows platforms.
  Future<void> _playSoundDefault(String soundId) async {
    await _player.stop();

    if (soundId == 'custom') {
      final path = _settings.customSoundPath;
      if (path.isNotEmpty) {
        await _player.play(DeviceFileSource(path));
      }
    } else {
      await _player.play(AssetSource('sounds/$soundId.wav'));
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
    final sampleRate = bytes[24] |
        (bytes[25] << 8) |
        (bytes[26] << 16) |
        (bytes[27] << 24);
    final dataSize = bytes[40] |
        (bytes[41] << 8) |
        (bytes[42] << 16) |
        (bytes[43] << 24);
    final bytesPerSec = bytes[28] |
        (bytes[29] << 8) |
        (bytes[30] << 16) |
        (bytes[31] << 24);

    if (sampleRate <= 0 || bytesPerSec <= 0) return 'Invalid WAV header';

    final durationSec = dataSize / bytesPerSec;
    if (durationSec > 10.0) {
      return 'Sound must be shorter than 10 seconds '
          '(${durationSec.toStringAsFixed(1)}s)';
    }

    return null;
  }

  void dispose() {
    _player.dispose();
  }
}
