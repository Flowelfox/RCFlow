/// Transient info about a running subprocess (Claude Code, Codex, etc.).
///
/// This is ephemeral — it is not archived and will be null after session
/// restore. The backend broadcasts [SubprocessInfo] via the
/// `subprocess_status` WebSocket message type.
class SubprocessInfo {
  final String subprocessType;
  final String displayName;
  final String workingDirectory;
  final String? currentTool;
  final DateTime startedAt;

  const SubprocessInfo({
    required this.subprocessType,
    required this.displayName,
    required this.workingDirectory,
    this.currentTool,
    required this.startedAt,
  });

  factory SubprocessInfo.fromJson(Map<String, dynamic> json) {
    return SubprocessInfo(
      subprocessType: json['subprocess_type'] as String? ?? 'unknown',
      displayName: json['display_name'] as String? ?? 'Subprocess',
      workingDirectory: json['working_directory'] as String? ?? '',
      currentTool: json['current_tool'] as String?,
      startedAt: DateTime.tryParse(json['started_at'] as String? ?? '') ?? DateTime.now(),
    );
  }

  SubprocessInfo copyWith({String? currentTool}) {
    return SubprocessInfo(
      subprocessType: subprocessType,
      displayName: displayName,
      workingDirectory: workingDirectory,
      currentTool: currentTool ?? this.currentTool,
      startedAt: startedAt,
    );
  }
}
