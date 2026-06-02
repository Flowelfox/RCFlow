import '../../models/artifact_info.dart';

/// In-memory store of artifacts keyed by artifact id, owned by [AppState].
///
/// Part of the Phase 5 step-3 carve of AppState into per-feature stores.
class ArtifactStore {
  final Map<String, ArtifactInfo> _artifacts = {};

  /// All artifacts sorted by ``discoveredAt`` descending (nulls last).
  List<ArtifactInfo> all() {
    final list = _artifacts.values.toList();
    list.sort((a, b) {
      final aTime = a.discoveredAt ?? DateTime(1970);
      final bTime = b.discoveredAt ?? DateTime(1970);
      return bTime.compareTo(aTime);
    });
    return list;
  }

  ArtifactInfo? get(String artifactId) => _artifacts[artifactId];

  /// Replace every artifact belonging to [workerId] with [raw] (a fresh list).
  void replaceWorker(String workerId, String workerName, List<dynamic> raw) {
    _artifacts.removeWhere((_, a) => a.workerId == workerId);
    for (final entry in raw) {
      final a = ArtifactInfo.fromJson(
        entry as Map<String, dynamic>,
        workerId: workerId,
        workerName: workerName,
      );
      _artifacts[a.artifactId] = a;
    }
  }

  void upsert(ArtifactInfo artifact) =>
      _artifacts[artifact.artifactId] = artifact;

  bool remove(String artifactId) => _artifacts.remove(artifactId) != null;
}
