/// What non-chat content a pane is currently showing, plus the pending
/// selections applied to the first prompt of a new session.  Owned by
/// [PaneState]; PaneState keeps the notify responsibility and delegates the
/// storage here.  Part of the Phase 5 step-3 carve.
class PaneViewTarget {
  /// Worktree pre-selected before the first message (sent once, then cleared).
  String? pendingWorktreePath;

  /// Task to associate with the first prompt of a new session (sent once).
  String? pendingTaskId;

  /// Task detail view target (when the pane shows a task).
  String? taskId;

  /// Artifact detail view target (when the pane shows an artifact).
  String? artifactId;

  /// Linear issue detail view target (when the pane shows a Linear issue).
  String? linearIssueId;

  /// Managed tool whose settings are shown (claude_code / codex / opencode).
  String? workerSettingsTool;

  /// Settings sub-section currently shown (e.g. "plugins").
  String? workerSettingsSection;
}
