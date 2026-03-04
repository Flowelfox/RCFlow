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
  final AudioPlayer _player = AudioPlayer();

  NotificationSoundService({required SettingsService settings})
      : _settings = settings;

  Future<void> playNotificationSound() async {
    final soundId = _settings.notificationSound;
    await _playSound(soundId);
  }

  Future<void> previewSound(String soundId) async {
    await _playSound(soundId);
  }

  Future<void> _playSound(String soundId) async {
    try {
      await _player.stop();

      if (soundId == 'custom') {
        final path = _settings.customSoundPath;
        if (path.isNotEmpty) {
          await _player.play(DeviceFileSource(path));
        }
      } else {
        await _player.play(AssetSource('sounds/$soundId.wav'));
      }
    } catch (_) {
      // Silently ignore playback errors
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
