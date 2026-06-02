import '../../models/linear_issue_info.dart';
import '../../models/worker_config.dart';

/// In-memory store of Linear issues keyed by issue id, owned by [AppState].
///
/// Holds the data and the read-only query projections; AppState keeps the
/// notify + pane-management responsibilities and delegates data access here.
/// Part of the Phase 5 step-3 carve of AppState into per-feature stores.
class LinearIssueStore {
  final Map<String, LinearIssueInfo> _issues = {};

  /// All issues sorted by ``updatedAt`` descending.
  List<LinearIssueInfo> all() {
    final list = _issues.values.toList();
    list.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    return list;
  }

  /// Issues grouped by ``workerId`` (every configured worker gets a bucket).
  Map<String, List<LinearIssueInfo>> byWorker(Iterable<WorkerConfig> configs) {
    final map = <String, List<LinearIssueInfo>>{};
    for (final config in configs) {
      map[config.id] = [];
    }
    for (final i in _issues.values) {
      map.putIfAbsent(i.workerId, () => []).add(i);
    }
    for (final list in map.values) {
      list.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    }
    return map;
  }

  LinearIssueInfo? get(String issueId) => _issues[issueId];

  /// Issues linked to [taskId], sorted by ``updatedAt`` descending.
  List<LinearIssueInfo> forTask(String taskId) {
    final result = _issues.values.where((i) => i.taskId == taskId).toList();
    result.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    return result;
  }

  /// Issues not yet linked to any task, sorted by ``updatedAt`` descending.
  List<LinearIssueInfo> unlinked() {
    final result = _issues.values.where((i) => i.taskId == null).toList();
    result.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    return result;
  }

  /// Replace every issue belonging to [workerId] with [raw] (a fresh list).
  void replaceWorker(String workerId, String workerName, List<dynamic> raw) {
    _issues.removeWhere((_, i) => i.workerId == workerId);
    for (final entry in raw) {
      final issue = LinearIssueInfo.fromJson(
        entry as Map<String, dynamic>,
        workerId: workerId,
        workerName: workerName,
      );
      _issues[issue.id] = issue;
    }
  }

  void upsert(LinearIssueInfo issue) => _issues[issue.id] = issue;

  /// Remove [issueId]; returns true when an entry was actually removed.
  bool remove(String issueId) => _issues.remove(issueId) != null;
}
