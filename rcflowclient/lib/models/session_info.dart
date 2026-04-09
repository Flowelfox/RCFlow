/// Worktree context attached to a session after a worktree tool call succeeds.
class WorktreeInfo {
  final String repoPath;
  final String lastAction; // "new" | "merge" | "rm" | "list"
  final String? branch;
  final String? base;

  const WorktreeInfo({
    required this.repoPath,
    required this.lastAction,
    this.branch,
    this.base,
  });

  factory WorktreeInfo.fromJson(Map<String, dynamic> json) => WorktreeInfo(
    repoPath: json['repo_path'] as String? ?? '',
    lastAction: json['last_action'] as String? ?? '',
    branch: json['branch'] as String?,
    base: json['base'] as String?,
  );

  Map<String, dynamic> toJson() => {
    'repo_path': repoPath,
    'last_action': lastAction,
    if (branch != null) 'branch': branch,
    if (base != null) 'base': base,
  };
}

class SessionInfo {
  final String sessionId;
  final String sessionType;
  final String status;
  final String? activityState;
  final DateTime? createdAt;
  final String? title;
  final String workerId;

  /// Reason the session is paused, if applicable. "max_turns" means Claude Code
  /// hit its configured turn limit. Null for manual pauses or non-paused sessions.
  final String? pausedReason;
  // Token usage
  final int inputTokens;
  final int outputTokens;
  final int cacheCreationInputTokens;
  final int cacheReadInputTokens;
  final int toolInputTokens;
  final int toolOutputTokens;
  final double toolCostUsd;

  /// Non-null when this session has executed at least one worktree tool.
  final WorktreeInfo? worktreeInfo;

  /// Absolute path of the worktree selected as the agent working directory,
  /// or null if no worktree is explicitly selected for this session.
  final String? selectedWorktreePath;

  /// Absolute path of the project directory attached to this session via the
  /// latest @ProjectName mention.  Null means the session is "Global" (no
  /// project attached yet).
  final String? mainProjectPath;

  /// The managed coding agent driving this session.
  ///
  /// One of ``"claude_code"``, ``"codex"``, or ``null`` for pure-LLM sessions
  /// that are handled directly by the built-in model without a managed
  /// subprocess.  Only live (in-memory) sessions populate this field; archived
  /// sessions always return ``null``.
  final String? agentType;

  /// Custom ordering position. Lower values appear first in the session list.
  /// Null means "use default createdAt ordering".
  final int? sortOrder;

  SessionInfo({
    required this.sessionId,
    required this.sessionType,
    required this.status,
    this.activityState,
    this.createdAt,
    this.title,
    this.workerId = '',
    this.pausedReason,
    this.inputTokens = 0,
    this.outputTokens = 0,
    this.cacheCreationInputTokens = 0,
    this.cacheReadInputTokens = 0,
    this.toolInputTokens = 0,
    this.toolOutputTokens = 0,
    this.toolCostUsd = 0.0,
    this.worktreeInfo,
    this.selectedWorktreePath,
    this.mainProjectPath,
    this.agentType,
    this.sortOrder,
  });

  bool get isProcessing {
    final s = activityState;
    return s != null && s != 'idle';
  }

  int get totalInputTokens => inputTokens + toolInputTokens;
  int get totalOutputTokens => outputTokens + toolOutputTokens;

  factory SessionInfo.fromJson(
    Map<String, dynamic> json, {
    String workerId = '',
  }) {
    DateTime? createdAt;
    final raw = json['created_at'] as String?;
    if (raw != null) {
      createdAt = DateTime.tryParse(raw);
    }
    final wtJson = json['worktree'] as Map<String, dynamic>?;
    return SessionInfo(
      sessionId: json['session_id'] as String,
      sessionType: json['session_type'] as String? ?? 'unknown',
      status: json['status'] as String? ?? 'unknown',
      activityState: json['activity_state'] as String?,
      createdAt: createdAt,
      title: json['title'] as String?,
      workerId: json['worker_id'] as String? ?? workerId,
      pausedReason: json['paused_reason'] as String?,
      inputTokens: (json['input_tokens'] as num?)?.toInt() ?? 0,
      outputTokens: (json['output_tokens'] as num?)?.toInt() ?? 0,
      cacheCreationInputTokens:
          (json['cache_creation_input_tokens'] as num?)?.toInt() ?? 0,
      cacheReadInputTokens:
          (json['cache_read_input_tokens'] as num?)?.toInt() ?? 0,
      toolInputTokens: (json['tool_input_tokens'] as num?)?.toInt() ?? 0,
      toolOutputTokens: (json['tool_output_tokens'] as num?)?.toInt() ?? 0,
      toolCostUsd: (json['tool_cost_usd'] as num?)?.toDouble() ?? 0.0,
      worktreeInfo: wtJson != null ? WorktreeInfo.fromJson(wtJson) : null,
      selectedWorktreePath: json['selected_worktree_path'] as String?,
      mainProjectPath: json['main_project_path'] as String?,
      agentType: json['agent_type'] as String?,
      sortOrder: (json['sort_order'] as num?)?.toInt(),
    );
  }

  Map<String, dynamic> toJson() => {
    'session_id': sessionId,
    'session_type': sessionType,
    'status': status,
    if (activityState != null) 'activity_state': activityState,
    if (createdAt != null) 'created_at': createdAt!.toIso8601String(),
    if (title != null) 'title': title,
    'worker_id': workerId,
    if (pausedReason != null) 'paused_reason': pausedReason,
    'input_tokens': inputTokens,
    'output_tokens': outputTokens,
    'cache_creation_input_tokens': cacheCreationInputTokens,
    'cache_read_input_tokens': cacheReadInputTokens,
    'tool_input_tokens': toolInputTokens,
    'tool_output_tokens': toolOutputTokens,
    'tool_cost_usd': toolCostUsd,
    if (worktreeInfo != null) 'worktree': worktreeInfo!.toJson(),
    if (selectedWorktreePath != null)
      'selected_worktree_path': selectedWorktreePath,
    if (mainProjectPath != null) 'main_project_path': mainProjectPath,
    if (agentType != null) 'agent_type': agentType,
    if (sortOrder != null) 'sort_order': sortOrder,
  };

  String get shortId =>
      sessionId.length >= 8 ? '${sessionId.substring(0, 8)}...' : sessionId;

  SessionInfo copyWith({
    String? sessionId,
    String? sessionType,
    String? status,
    String? activityState,
    DateTime? createdAt,
    String? title,
    String? workerId,
    String? pausedReason,
    int? inputTokens,
    int? outputTokens,
    int? cacheCreationInputTokens,
    int? cacheReadInputTokens,
    int? toolInputTokens,
    int? toolOutputTokens,
    double? toolCostUsd,
    // Pass Object() sentinel to explicitly clear worktreeInfo
    Object? worktreeInfo = _keep,
    String? selectedWorktreePath,
    String? mainProjectPath,
    // Pass Object() sentinel to explicitly clear agentType
    Object? agentType = _keep,
    // Pass Object() sentinel to explicitly clear sortOrder
    Object? sortOrder = _keep,
  }) {
    return SessionInfo(
      sessionId: sessionId ?? this.sessionId,
      sessionType: sessionType ?? this.sessionType,
      status: status ?? this.status,
      activityState: activityState ?? this.activityState,
      createdAt: createdAt ?? this.createdAt,
      title: title ?? this.title,
      workerId: workerId ?? this.workerId,
      pausedReason: pausedReason ?? this.pausedReason,
      inputTokens: inputTokens ?? this.inputTokens,
      outputTokens: outputTokens ?? this.outputTokens,
      cacheCreationInputTokens:
          cacheCreationInputTokens ?? this.cacheCreationInputTokens,
      cacheReadInputTokens: cacheReadInputTokens ?? this.cacheReadInputTokens,
      toolInputTokens: toolInputTokens ?? this.toolInputTokens,
      toolOutputTokens: toolOutputTokens ?? this.toolOutputTokens,
      toolCostUsd: toolCostUsd ?? this.toolCostUsd,
      worktreeInfo: identical(worktreeInfo, _keep)
          ? this.worktreeInfo
          : worktreeInfo as WorktreeInfo?,
      selectedWorktreePath: selectedWorktreePath ?? this.selectedWorktreePath,
      mainProjectPath: mainProjectPath ?? this.mainProjectPath,
      agentType: identical(agentType, _keep)
          ? this.agentType
          : agentType as String?,
      sortOrder: identical(sortOrder, _keep)
          ? this.sortOrder
          : sortOrder as int?,
    );
  }
}

/// Sentinel used by [SessionInfo.copyWith] to distinguish "not provided" from
/// an explicit `null` for the [SessionInfo.worktreeInfo] parameter.
const Object _keep = Object();
