/// Cached worktree/artifact lists per `'workerId:projectPath'` key, owned by
/// [AppState].  Lets a reopened project panel show last-known data immediately
/// while a fresh fetch runs.  Part of the Phase 5 step-3 carve of AppState.
typedef ProjectData = ({
  List<Map<String, dynamic>>? worktrees,
  List<Map<String, dynamic>>? artifacts,
  bool noGitRepo,
});

class ProjectDataCache {
  final Map<String, ProjectData> _cache = {};

  ProjectData? get(String key) => _cache[key];

  /// Merge the provided fields into the cached entry for [key] (unspecified
  /// fields keep their prior value).
  void set(
    String key, {
    List<Map<String, dynamic>>? worktrees,
    List<Map<String, dynamic>>? artifacts,
    bool? noGitRepo,
  }) {
    final existing = _cache[key];
    _cache[key] = (
      worktrees: worktrees ?? existing?.worktrees,
      artifacts: artifacts ?? existing?.artifacts,
      noGitRepo: noGitRepo ?? existing?.noGitRepo ?? false,
    );
  }
}
