import '../../models/github_pr_info.dart';
import '../../models/worker_config.dart';

/// In-memory store of GitHub pull requests keyed by PR id, owned by [AppState].
///
/// Holds the data and the read-only query projections; AppState keeps the
/// notify + pane-management responsibilities and delegates data access here.
/// Mirrors [LinearIssueStore] from the Phase 5 step-3 carve of AppState into
/// per-feature stores.
class GithubPrStore {
  final Map<String, GithubPrInfo> _prs = {};

  /// All pull requests sorted by ``updatedAt`` descending.
  List<GithubPrInfo> all() {
    final list = _prs.values.toList();
    list.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    return list;
  }

  /// Pull requests grouped by ``workerId`` (every configured worker gets a bucket).
  Map<String, List<GithubPrInfo>> byWorker(Iterable<WorkerConfig> configs) {
    final map = <String, List<GithubPrInfo>>{};
    for (final config in configs) {
      map[config.id] = [];
    }
    for (final p in _prs.values) {
      map.putIfAbsent(p.workerId, () => []).add(p);
    }
    for (final list in map.values) {
      list.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    }
    return map;
  }

  GithubPrInfo? get(String prId) => _prs[prId];

  /// Pull requests with the given [role] ("for_me" or "created"), sorted by
  /// ``updatedAt`` descending.
  List<GithubPrInfo> forRole(String role) {
    final result = _prs.values.where((p) => p.role == role).toList();
    result.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    return result;
  }

  /// Replace every PR belonging to [workerId] with [raw] (a fresh list).
  void replaceWorker(String workerId, String workerName, List<dynamic> raw) {
    _prs.removeWhere((_, p) => p.workerId == workerId);
    for (final entry in raw) {
      final pr = GithubPrInfo.fromJson(
        entry as Map<String, dynamic>,
        workerId: workerId,
        workerName: workerName,
      );
      _prs[pr.id] = pr;
    }
  }

  void upsert(GithubPrInfo pr) => _prs[pr.id] = pr;

  /// Remove [prId]; returns true when an entry was actually removed.
  bool remove(String prId) => _prs.remove(prId) != null;
}
