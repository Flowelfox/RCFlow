import 'package:flutter/foundation.dart';

import 'settings_service.dart';
import 'update_fetcher.dart';

/// Manages the client's self-update lifecycle.
///
/// Call [restoreCachedState] synchronously (from the [AppState] constructor)
/// to populate state from [SettingsService] before the first frame. Call
/// [maybeCheck] asynchronously (from [AppState.initAsync]) to perform a
/// network fetch when the 24-hour cache TTL has expired.
///
/// Listeners are notified whenever [isChecking], [latestVersion], or
/// [errorMessage] change.
class UpdateService extends ChangeNotifier {
  static const Duration _cacheTtl = Duration(hours: 24);

  final SettingsService _settings;
  final UpdateFetcher _fetcher;

  String? _latestVersion;
  String? _latestReleaseUrl;
  String? _latestDownloadUrl;
  bool _isChecking = false;
  String? _errorMessage;

  UpdateService({required SettingsService settings, UpdateFetcher? fetcher})
    : _settings = settings,
      _fetcher = fetcher ?? HttpUpdateFetcher();

  // ---------------------------------------------------------------------------
  // Getters
  // ---------------------------------------------------------------------------

  /// The version string currently installed, as persisted by [main].
  String? get currentVersion => _settings.currentVersion;

  /// The latest known version from the update server, or null if not yet
  /// fetched.
  String? get latestVersion => _latestVersion;

  /// URL to the GitHub release page for [latestVersion].
  String? get latestReleaseUrl => _latestReleaseUrl;

  /// Platform-specific direct download URL for [latestVersion], or null.
  String? get latestDownloadUrl => _latestDownloadUrl;

  bool get isChecking => _isChecking;
  bool get hasError => _errorMessage != null;
  String? get errorMessage => _errorMessage;

  /// True when a newer version is available and the user has not dismissed it.
  bool get updateAvailable {
    final current = currentVersion;
    final latest = _latestVersion;
    if (current == null || latest == null) return false;
    return isNewer(latest, current);
  }

  /// True when the user has dismissed the banner for [latestVersion].
  bool get isDismissed =>
      _latestVersion != null &&
      _latestVersion == _settings.dismissedUpdateVersion;

  /// True when the banner should be visible (update available & not dismissed).
  bool get showBanner => updateAvailable && !isDismissed;

  // ---------------------------------------------------------------------------
  // Synchronous init (called from AppState constructor)
  // ---------------------------------------------------------------------------

  /// Restores cached update state synchronously from [SettingsService].
  ///
  /// If the currently running version is already >= the cached latest version
  /// the stale cache is cleared. This handles the post-update case where the
  /// user installed the update and launched the new version.
  ///
  /// Does **not** call [notifyListeners] — this is pure synchronous init.
  void restoreCachedState() {
    final cached = _settings.cachedLatestVersion;
    if (cached == null) return;

    final current = _settings.currentVersion;
    if (current != null && !isNewer(cached, current)) {
      // Running version is the same or newer — the cached update is stale.
      _settings.cachedLatestVersion = null;
      _settings.dismissedUpdateVersion = null;
      return;
    }

    _latestVersion = cached;
    // releaseUrl and downloadUrl are not cached; they will be filled on the
    // next successful network fetch.
  }

  // ---------------------------------------------------------------------------
  // Async update check
  // ---------------------------------------------------------------------------

  /// Checks for updates if the 24-hour TTL has expired; otherwise returns
  /// immediately using the already-restored cache.
  Future<void> maybeCheck() async {
    final lastCheck = _settings.lastUpdateCheck;
    if (lastCheck != null &&
        DateTime.now().difference(lastCheck) < _cacheTtl &&
        _settings.cachedLatestVersion != null) {
      return; // Cache is still fresh.
    }
    await checkForUpdates();
  }

  /// Performs a network fetch unconditionally and updates state.
  Future<void> checkForUpdates() async {
    if (_isChecking) return;
    _isChecking = true;
    _errorMessage = null;
    notifyListeners();

    try {
      final info = await _fetcher.fetchLatestUpdate();
      if (info != null) {
        _settings.lastUpdateCheck = DateTime.now();
        _settings.cachedLatestVersion = info.version;
        _latestVersion = info.version;
        _latestReleaseUrl = info.releaseUrl;
        _latestDownloadUrl = info.downloadUrl;

        // If there's a previously dismissed version and the newly fetched
        // version is strictly newer, clear the dismissal so the banner
        // reappears for the newer release.
        final dismissed = _settings.dismissedUpdateVersion;
        if (dismissed != null && isNewer(info.version, dismissed)) {
          _settings.dismissedUpdateVersion = null;
        }
      }
    } catch (e) {
      _errorMessage = e.toString();
    } finally {
      _isChecking = false;
      notifyListeners();
    }
  }

  /// Persists the current [latestVersion] as dismissed so the banner is hidden.
  void dismissCurrentUpdate() {
    if (_latestVersion == null) return;
    _settings.dismissedUpdateVersion = _latestVersion;
    notifyListeners();
  }

  // ---------------------------------------------------------------------------
  // Version comparison helpers
  // ---------------------------------------------------------------------------

  /// Returns true if [a] is strictly newer than [b].
  ///
  /// Versions are compared numerically by dot-separated segments, so "1.10.0"
  /// is correctly treated as newer than "1.9.0".
  static bool isNewer(String a, String b) {
    final aParts = _parseParts(a);
    final bParts = _parseParts(b);
    final len = aParts.length > bParts.length ? aParts.length : bParts.length;
    for (var i = 0; i < len; i++) {
      final av = i < aParts.length ? aParts[i] : 0;
      final bv = i < bParts.length ? bParts[i] : 0;
      if (av > bv) return true;
      if (av < bv) return false;
    }
    return false; // equal
  }

  static List<int> _parseParts(String version) =>
      version.split('.').map((p) => int.tryParse(p) ?? 0).toList();
}
