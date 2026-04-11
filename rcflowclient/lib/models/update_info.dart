/// Metadata about an available software update.
class UpdateInfo {
  /// Normalized version string (no leading 'v', no build suffix), e.g. "1.38.0".
  final String version;

  /// URL to the GitHub release page.
  final String releaseUrl;

  /// Platform-specific direct download URL, or null if unavailable.
  final String? downloadUrl;

  const UpdateInfo({
    required this.version,
    required this.releaseUrl,
    this.downloadUrl,
  });
}
