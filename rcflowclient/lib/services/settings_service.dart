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
  static const _lastProjectPerWorkerKey = 'rcflow_last_project_per_worker';
  static const _lastAgentPerWorkerKey = 'rcflow_last_agent_per_worker';

  static const _themeModeKey = 'rcflow_theme_mode';
  static const _fontSizeKey = 'rcflow_font_size';
  static const _compactModeKey = 'rcflow_compact_mode';
  static const _soundEnabledKey = 'rcflow_sound_enabled';
  static const _vibrateEnabledKey = 'rcflow_vibrate_enabled';
  static const _soundOnCompleteEnabledKey = 'rcflow_sound_on_complete';
  static const _notificationSoundKey = 'rcflow_notification_sound';
  static const _customSoundPathKey = 'rcflow_custom_sound_path';

  static const _terminalScrollbackKey = 'rcflow_terminal_scrollback';
  static const _terminalColorSchemeKey = 'rcflow_terminal_color_scheme';
  static const _terminalCursorStyleKey = 'rcflow_terminal_cursor_style';
  static const _terminalFontSizeKey = 'rcflow_terminal_font_size';
  static const _terminalFontFamilyKey = 'rcflow_terminal_font_family';
  static const _hotkeyBindingsKey = 'rcflow_hotkey_bindings';
  static const _toastEnabledKey = 'rcflow_toast_enabled';
  static const _toastBackgroundSessionsKey = 'rcflow_toast_background_sessions';
  static const _toastTasksKey = 'rcflow_toast_tasks';
  static const _toastConnectionsKey = 'rcflow_toast_connections';
  static const _showCompletedTasksKey = 'rcflow_show_completed_tasks';

  // Filter persistence keys
  static const _workersFilterSearchKey = 'rcflow_workers_filter_search';
  static const _workersFilterStatusKey = 'rcflow_workers_filter_status';
  static const _tasksFilterSearchKey = 'rcflow_tasks_filter_search';
  static const _tasksFilterStatusKey = 'rcflow_tasks_filter_status';
  static const _tasksFilterSourceKey = 'rcflow_tasks_filter_source';
  static const _artifactsFilterSearchKey = 'rcflow_artifacts_filter_search';

  // Expanded/collapsed state persistence keys
  static const _workersExpandedKey = 'rcflow_workers_expanded';
  static const _tasksCollapsedGroupsKey = 'rcflow_tasks_collapsed_groups';
  static const _tasksGroupByWorkerKey = 'rcflow_tasks_group_by_worker';
  static const _workersGroupByProjectKey = 'rcflow_workers_group_by_project';
  static const _artifactsGroupByProjectKey =
      'rcflow_artifacts_group_by_project';
  static const _artifactsExpandedWorkersKey =
      'rcflow_artifacts_expanded_workers';
  static const _artifactsExpandedProjectsKey =
      'rcflow_artifacts_expanded_projects';

  // Auto-update keys
  static const _currentVersionKey = 'rcflow_current_version';
  static const _lastUpdateCheckKey = 'rcflow_last_update_check';
  static const _cachedLatestVersionKey = 'rcflow_cached_latest_version';
  static const _dismissedUpdateVersionKey = 'rcflow_dismissed_update_version';

  // Setup / onboarding keys
  static const _setupCompleteKey = 'rcflow_setup_complete';
  static const _onboardingCompleteKey = 'rcflow_onboarding_complete';

  static const _defaultHost = '192.168.1.100:8765';

  late final SharedPreferences _prefs;

  Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
  }

  // --- Auto-update ---

  /// The version string currently installed (e.g. "1.38.0"), persisted at
  /// startup before [AppState] is created. Null until first launch after this
  /// feature is deployed.
  String? get currentVersion => _prefs.getString(_currentVersionKey);
  set currentVersion(String? value) {
    if (value == null) {
      _prefs.remove(_currentVersionKey);
    } else {
      _prefs.setString(_currentVersionKey, value);
    }
  }

  /// UTC timestamp of the last successful update check, stored as ISO-8601.
  DateTime? get lastUpdateCheck {
    final raw = _prefs.getString(_lastUpdateCheckKey);
    if (raw == null) return null;
    return DateTime.tryParse(raw);
  }

  set lastUpdateCheck(DateTime? value) {
    if (value == null) {
      _prefs.remove(_lastUpdateCheckKey);
    } else {
      _prefs.setString(_lastUpdateCheckKey, value.toUtc().toIso8601String());
    }
  }

  /// Normalized latest version seen from the update server (e.g. "1.39.0").
  String? get cachedLatestVersion => _prefs.getString(_cachedLatestVersionKey);
  set cachedLatestVersion(String? value) {
    if (value == null) {
      _prefs.remove(_cachedLatestVersionKey);
    } else {
      _prefs.setString(_cachedLatestVersionKey, value);
    }
  }

  /// Version the user has explicitly dismissed from the update banner.
  String? get dismissedUpdateVersion =>
      _prefs.getString(_dismissedUpdateVersionKey);
  set dismissedUpdateVersion(String? value) {
    if (value == null) {
      _prefs.remove(_dismissedUpdateVersionKey);
    } else {
      _prefs.setString(_dismissedUpdateVersionKey, value);
    }
  }

  // --- Setup / onboarding ---

  bool get setupComplete => _prefs.getBool(_setupCompleteKey) ?? false;
  set setupComplete(bool value) => _prefs.setBool(_setupCompleteKey, value);

  bool get onboardingComplete =>
      _prefs.getBool(_onboardingCompleteKey) ?? false;
  set onboardingComplete(bool value) =>
      _prefs.setBool(_onboardingCompleteKey, value);

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
      _workersKey,
      jsonEncode(value.map((w) => w.toJson()).toList()),
    );
  }

  // --- Per-worker last session ID ---

  Map<String, String> get _lastSessionPerWorker {
    final raw = _prefs.getString(_lastSessionPerWorkerKey);
    if (raw == null) return {};
    try {
      return (jsonDecode(raw) as Map<String, dynamic>).map(
        (k, v) => MapEntry(k, v as String),
      );
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
      return (jsonDecode(raw) as Map<String, dynamic>).map(
        (k, v) => MapEntry(k, v as String),
      );
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

  // --- Per-worker last used project ---

  Map<String, String> get _lastProjectPerWorker {
    final raw = _prefs.getString(_lastProjectPerWorkerKey);
    if (raw == null) return {};
    try {
      return (jsonDecode(raw) as Map<String, dynamic>).map(
        (k, v) => MapEntry(k, v as String),
      );
    } catch (_) {
      return {};
    }
  }

  String? getLastProjectForWorker(String workerId) =>
      _lastProjectPerWorker[workerId];

  void setLastProjectForWorker(String workerId, String? projectName) {
    final map = _lastProjectPerWorker;
    if (projectName == null) {
      map.remove(workerId);
    } else {
      map[workerId] = projectName;
    }
    _prefs.setString(_lastProjectPerWorkerKey, jsonEncode(map));
  }

  // --- Per-worker last used agent ---

  Map<String, String> get _lastAgentPerWorker {
    final raw = _prefs.getString(_lastAgentPerWorkerKey);
    if (raw == null) return {};
    try {
      return (jsonDecode(raw) as Map<String, dynamic>).map(
        (k, v) => MapEntry(k, v as String),
      );
    } catch (_) {
      return {};
    }
  }

  String? getLastAgentForWorker(String workerId) =>
      _lastAgentPerWorker[workerId];

  void setLastAgentForWorker(String workerId, String? agentName) {
    final map = _lastAgentPerWorker;
    if (agentName == null) {
      map.remove(workerId);
    } else {
      map[workerId] = agentName;
    }
    _prefs.setString(_lastAgentPerWorkerKey, jsonEncode(map));
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

  String get customSoundPath => _prefs.getString(_customSoundPathKey) ?? '';
  set customSoundPath(String value) =>
      _prefs.setString(_customSoundPathKey, value);

  int get terminalScrollback => _prefs.getInt(_terminalScrollbackKey) ?? 1000;
  set terminalScrollback(int value) =>
      _prefs.setInt(_terminalScrollbackKey, value);

  String get terminalColorScheme =>
      _prefs.getString(_terminalColorSchemeKey) ?? 'default';
  set terminalColorScheme(String value) =>
      _prefs.setString(_terminalColorSchemeKey, value);

  String get terminalCursorStyle =>
      _prefs.getString(_terminalCursorStyleKey) ?? 'block';
  set terminalCursorStyle(String value) =>
      _prefs.setString(_terminalCursorStyleKey, value);

  double get terminalFontSize => _prefs.getDouble(_terminalFontSizeKey) ?? 14.0;
  set terminalFontSize(double value) =>
      _prefs.setDouble(_terminalFontSizeKey, value);

  String get terminalFontFamily =>
      _prefs.getString(_terminalFontFamilyKey) ?? 'monospace';
  set terminalFontFamily(String value) =>
      _prefs.setString(_terminalFontFamilyKey, value);

  String? get hotkeyBindings => _prefs.getString(_hotkeyBindingsKey);
  set hotkeyBindings(String? value) {
    if (value == null) {
      _prefs.remove(_hotkeyBindingsKey);
    } else {
      _prefs.setString(_hotkeyBindingsKey, value);
    }
  }

  // --- Toast notifications ---

  bool get toastEnabled => _prefs.getBool(_toastEnabledKey) ?? true;
  set toastEnabled(bool value) => _prefs.setBool(_toastEnabledKey, value);

  bool get toastBackgroundSessions =>
      _prefs.getBool(_toastBackgroundSessionsKey) ?? true;
  set toastBackgroundSessions(bool value) =>
      _prefs.setBool(_toastBackgroundSessionsKey, value);

  bool get toastTasks => _prefs.getBool(_toastTasksKey) ?? true;
  set toastTasks(bool value) => _prefs.setBool(_toastTasksKey, value);

  bool get toastConnections => _prefs.getBool(_toastConnectionsKey) ?? true;
  set toastConnections(bool value) =>
      _prefs.setBool(_toastConnectionsKey, value);

  bool get showCompletedTasks =>
      _prefs.getBool(_showCompletedTasksKey) ?? false;
  set showCompletedTasks(bool value) =>
      _prefs.setBool(_showCompletedTasksKey, value);

  // --- Per-session draft cache ---
  //
  // Key scheme:
  //   • Real session: "rcflow_draft_session_{sessionId}"
  //   • New-session pane (no ID yet): "rcflow_draft_new_{workerId}"
  //
  // Each draft has a companion "_ts" key storing the write timestamp as
  // milliseconds-since-epoch so the client can compare it to the backend's
  // updated_at when deciding which copy to trust.

  static const _draftPrefix = 'rcflow_draft_session_';

  /// Read the cached draft for [key].
  ///
  /// Returns `(content: '', cachedAt: null)` when no draft exists.
  ({String content, DateTime? cachedAt}) getDraft(String key) {
    final content = _prefs.getString('$_draftPrefix$key') ?? '';
    final tsMs = _prefs.getInt('$_draftPrefix${key}_ts');
    final cachedAt = tsMs != null
        ? DateTime.fromMillisecondsSinceEpoch(tsMs, isUtc: true)
        : null;
    return (content: content, cachedAt: cachedAt);
  }

  /// Persist [content] as the draft for [key] and record the current time.
  void saveDraft(String key, String content) {
    _prefs.setString('$_draftPrefix$key', content);
    _prefs.setInt(
      '$_draftPrefix${key}_ts',
      DateTime.now().millisecondsSinceEpoch,
    );
  }

  /// Remove the draft and its timestamp companion for [key].
  void clearDraft(String key) {
    _prefs.remove('$_draftPrefix$key');
    _prefs.remove('$_draftPrefix${key}_ts');
  }

  // --- Helpers for list persistence (avoids setStringList/getStringList
  //     which can lose type info on Windows after JSON round-trip) ---

  List<String> _getJsonStringList(String key) {
    final raw = _prefs.getString(key);
    if (raw == null || raw.isEmpty) return [];
    try {
      return (jsonDecode(raw) as List<dynamic>).cast<String>();
    } catch (_) {
      return [];
    }
  }

  void _setJsonStringList(String key, List<String> value) {
    _prefs.setString(key, jsonEncode(value));
  }

  // --- Filter persistence ---

  String get workersFilterSearch =>
      _prefs.getString(_workersFilterSearchKey) ?? '';
  set workersFilterSearch(String value) =>
      _prefs.setString(_workersFilterSearchKey, value);

  List<String> get workersFilterStatus =>
      _getJsonStringList(_workersFilterStatusKey);
  set workersFilterStatus(List<String> value) =>
      _setJsonStringList(_workersFilterStatusKey, value);

  String get tasksFilterSearch => _prefs.getString(_tasksFilterSearchKey) ?? '';
  set tasksFilterSearch(String value) =>
      _prefs.setString(_tasksFilterSearchKey, value);

  List<String> get tasksFilterStatus =>
      _getJsonStringList(_tasksFilterStatusKey);
  set tasksFilterStatus(List<String> value) =>
      _setJsonStringList(_tasksFilterStatusKey, value);

  List<String> get tasksFilterSource =>
      _getJsonStringList(_tasksFilterSourceKey);
  set tasksFilterSource(List<String> value) =>
      _setJsonStringList(_tasksFilterSourceKey, value);

  String get artifactsFilterSearch =>
      _prefs.getString(_artifactsFilterSearchKey) ?? '';
  set artifactsFilterSearch(String value) =>
      _prefs.setString(_artifactsFilterSearchKey, value);

  // --- Expanded/collapsed state persistence ---

  /// Workers tab: which worker IDs are expanded. Null means "not yet set"
  /// (first-time use should auto-expand all).
  List<String>? get workersExpanded {
    final raw = _prefs.getString(_workersExpandedKey);
    if (raw == null) return null;
    return _getJsonStringList(_workersExpandedKey);
  }

  set workersExpanded(List<String>? value) {
    if (value == null) {
      _prefs.remove(_workersExpandedKey);
    } else {
      _setJsonStringList(_workersExpandedKey, value);
    }
  }

  /// Tasks tab: which status groups are collapsed. Null means "not yet set"
  /// (first-time use should default to {'done'}).
  List<String>? get tasksCollapsedGroups {
    final raw = _prefs.getString(_tasksCollapsedGroupsKey);
    if (raw == null) return null;
    return _getJsonStringList(_tasksCollapsedGroupsKey);
  }

  set tasksCollapsedGroups(List<String>? value) {
    if (value == null) {
      _prefs.remove(_tasksCollapsedGroupsKey);
    } else {
      _setJsonStringList(_tasksCollapsedGroupsKey, value);
    }
  }

  /// Tasks tab: whether to group tasks by worker instead of by status.
  bool get tasksGroupByWorker =>
      _prefs.getBool(_tasksGroupByWorkerKey) ?? false;
  set tasksGroupByWorker(bool value) =>
      _prefs.setBool(_tasksGroupByWorkerKey, value);

  /// Workers tab: whether to group sessions by project within each worker.
  bool get workersGroupByProject =>
      _prefs.getBool(_workersGroupByProjectKey) ?? false;
  set workersGroupByProject(bool value) =>
      _prefs.setBool(_workersGroupByProjectKey, value);

  /// Artifacts tab: whether to group artifacts by project (defaults to true).
  bool get artifactsGroupByProject =>
      _prefs.getBool(_artifactsGroupByProjectKey) ?? true;
  set artifactsGroupByProject(bool value) =>
      _prefs.setBool(_artifactsGroupByProjectKey, value);

  /// Artifacts tab: which worker IDs are expanded. Null means "not yet set".
  List<String>? get artifactsExpandedWorkers {
    final raw = _prefs.getString(_artifactsExpandedWorkersKey);
    if (raw == null) return null;
    return _getJsonStringList(_artifactsExpandedWorkersKey);
  }

  set artifactsExpandedWorkers(List<String>? value) {
    if (value == null) {
      _prefs.remove(_artifactsExpandedWorkersKey);
    } else {
      _setJsonStringList(_artifactsExpandedWorkersKey, value);
    }
  }

  /// Artifacts tab: which project keys are expanded. Null means "not yet set".
  List<String>? get artifactsExpandedProjects {
    final raw = _prefs.getString(_artifactsExpandedProjectsKey);
    if (raw == null) return null;
    return _getJsonStringList(_artifactsExpandedProjectsKey);
  }

  set artifactsExpandedProjects(List<String>? value) {
    if (value == null) {
      _prefs.remove(_artifactsExpandedProjectsKey);
    } else {
      _setJsonStringList(_artifactsExpandedProjectsKey, value);
    }
  }
}
