import 'dart:convert';

import 'package:shared_preferences/shared_preferences.dart';

import '../models/worker_config.dart';

class SettingsService {
  // Legacy keys (kept for migration)
  static const _hostKey = 'rcflow_host';
  static const _apiKeyKey = 'rcflow_api_key';
  static const _useSSLKey = 'rcflow_use_ssl';
  static const _lastSessionIdKey = 'rcflow_last_session_id';
  static const _cachedSessionsKey = 'rcflow_cached_sessions';

  // Worker keys
  static const _workersKey = 'rcflow_workers';
  static const _lastSessionPerWorkerKey = 'rcflow_last_session_per_worker';
  static const _cachedSessionsPerWorkerKey =
      'rcflow_cached_sessions_per_worker';

  static const _themeModeKey = 'rcflow_theme_mode';
  static const _fontSizeKey = 'rcflow_font_size';
  static const _compactModeKey = 'rcflow_compact_mode';
  static const _soundEnabledKey = 'rcflow_sound_enabled';
  static const _vibrateEnabledKey = 'rcflow_vibrate_enabled';
  static const _soundOnCompleteEnabledKey = 'rcflow_sound_on_complete';
  static const _notificationSoundKey = 'rcflow_notification_sound';
  static const _customSoundPathKey = 'rcflow_custom_sound_path';
  static const _defaultHost = '192.168.1.100:8765';

  late final SharedPreferences _prefs;

  Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
  }

  // --- Legacy single-server keys (for migration) ---

  String get host => _prefs.getString(_hostKey) ?? _defaultHost;
  set host(String value) => _prefs.setString(_hostKey, value);

  String get apiKey => _prefs.getString(_apiKeyKey) ?? '';
  set apiKey(String value) => _prefs.setString(_apiKeyKey, value);

  bool get useSSL => _prefs.getBool(_useSSLKey) ?? false;
  set useSSL(bool value) => _prefs.setBool(_useSSLKey, value);

  String? get lastSessionId => _prefs.getString(_lastSessionIdKey);
  set lastSessionId(String? value) {
    if (value == null) {
      _prefs.remove(_lastSessionIdKey);
    } else {
      _prefs.setString(_lastSessionIdKey, value);
    }
  }

  String? get cachedSessions => _prefs.getString(_cachedSessionsKey);
  set cachedSessions(String? value) {
    if (value == null) {
      _prefs.remove(_cachedSessionsKey);
    } else {
      _prefs.setString(_cachedSessionsKey, value);
    }
  }

  // --- Workers ---

  List<WorkerConfig> get workers {
    final raw = _prefs.getString(_workersKey);
    if (raw == null) return [];
    try {
      final list = jsonDecode(raw) as List<dynamic>;
      return list
          .map((e) => WorkerConfig.fromJson(e as Map<String, dynamic>))
          .toList();
    } catch (_) {
      return [];
    }
  }

  set workers(List<WorkerConfig> value) {
    _prefs.setString(
        _workersKey, jsonEncode(value.map((w) => w.toJson()).toList()));
  }

  // --- Per-worker last session ID ---

  Map<String, String> get _lastSessionPerWorker {
    final raw = _prefs.getString(_lastSessionPerWorkerKey);
    if (raw == null) return {};
    try {
      return (jsonDecode(raw) as Map<String, dynamic>)
          .map((k, v) => MapEntry(k, v as String));
    } catch (_) {
      return {};
    }
  }

  String? getLastSessionId(String workerId) => _lastSessionPerWorker[workerId];

  void setLastSessionId(String workerId, String? id) {
    final map = _lastSessionPerWorker;
    if (id == null) {
      map.remove(workerId);
    } else {
      map[workerId] = id;
    }
    _prefs.setString(_lastSessionPerWorkerKey, jsonEncode(map));
  }

  // --- Per-worker cached sessions ---

  Map<String, String> get _cachedSessionsPerWorker {
    final raw = _prefs.getString(_cachedSessionsPerWorkerKey);
    if (raw == null) return {};
    try {
      return (jsonDecode(raw) as Map<String, dynamic>)
          .map((k, v) => MapEntry(k, v as String));
    } catch (_) {
      return {};
    }
  }

  String? getCachedSessions(String workerId) =>
      _cachedSessionsPerWorker[workerId];

  void setCachedSessions(String workerId, String? json) {
    final map = _cachedSessionsPerWorker;
    if (json == null) {
      map.remove(workerId);
    } else {
      map[workerId] = json;
    }
    _prefs.setString(_cachedSessionsPerWorkerKey, jsonEncode(map));
  }

  String get themeMode => _prefs.getString(_themeModeKey) ?? 'dark';
  set themeMode(String value) => _prefs.setString(_themeModeKey, value);

  String get fontSize => _prefs.getString(_fontSizeKey) ?? 'medium';
  set fontSize(String value) => _prefs.setString(_fontSizeKey, value);

  bool get compactMode => _prefs.getBool(_compactModeKey) ?? false;
  set compactMode(bool value) => _prefs.setBool(_compactModeKey, value);

  bool get soundEnabled => _prefs.getBool(_soundEnabledKey) ?? false;
  set soundEnabled(bool value) => _prefs.setBool(_soundEnabledKey, value);

  bool get soundOnCompleteEnabled =>
      _prefs.getBool(_soundOnCompleteEnabledKey) ?? true;
  set soundOnCompleteEnabled(bool value) =>
      _prefs.setBool(_soundOnCompleteEnabledKey, value);

  bool get vibrateEnabled => _prefs.getBool(_vibrateEnabledKey) ?? true;
  set vibrateEnabled(bool value) => _prefs.setBool(_vibrateEnabledKey, value);

  String get notificationSound =>
      _prefs.getString(_notificationSoundKey) ?? 'gentle_chime';
  set notificationSound(String value) =>
      _prefs.setString(_notificationSoundKey, value);

  String get customSoundPath =>
      _prefs.getString(_customSoundPathKey) ?? '';
  set customSoundPath(String value) =>
      _prefs.setString(_customSoundPathKey, value);
}
