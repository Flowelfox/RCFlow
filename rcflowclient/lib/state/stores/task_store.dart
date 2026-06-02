import '../../models/task_info.dart';
import '../../models/worker_config.dart';

/// In-memory store of tasks keyed by task id, owned by [AppState].
///
/// Holds the data and the read-only query projections; AppState keeps the
/// notify, toast, and pane-management responsibilities.  Part of the Phase 5
/// step-3 carve of AppState into per-feature stores.
class TaskStore {
  final Map<String, TaskInfo> _tasks = {};

  /// All tasks sorted by ``updatedAt`` descending.
  List<TaskInfo> all() {
    final list = _tasks.values.toList();
    list.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    return list;
  }

  /// Tasks grouped by ``workerId`` (every configured worker gets a bucket).
  Map<String, List<TaskInfo>> byWorker(Iterable<WorkerConfig> configs) {
    final map = <String, List<TaskInfo>>{};
    for (final config in configs) {
      map[config.id] = [];
    }
    for (final t in _tasks.values) {
      map.putIfAbsent(t.workerId, () => []).add(t);
    }
    for (final list in map.values) {
      list.sort((a, b) => b.updatedAt.compareTo(a.updatedAt));
    }
    return map;
  }

  TaskInfo? get(String taskId) => _tasks[taskId];

  /// Whether [sessionId] is attached to any task.
  bool isAttachedToSession(String sessionId) =>
      _tasks.values.any((t) => t.sessions.any((s) => s.sessionId == sessionId));

  /// All tasks [sessionId] is attached to.
  List<TaskInfo> forSession(String sessionId) => _tasks.values
      .where((t) => t.sessions.any((s) => s.sessionId == sessionId))
      .toList();

  /// Replace every task belonging to [workerId] with [raw] (a fresh list).
  void replaceWorker(String workerId, String workerName, List<dynamic> raw) {
    _tasks.removeWhere((_, t) => t.workerId == workerId);
    for (final entry in raw) {
      final t = TaskInfo.fromJson(
        entry as Map<String, dynamic>,
        workerId: workerId,
        workerName: workerName,
      );
      _tasks[t.taskId] = t;
    }
  }

  void upsert(TaskInfo task) => _tasks[task.taskId] = task;

  bool remove(String taskId) => _tasks.remove(taskId) != null;
}
