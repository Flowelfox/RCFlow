class SessionInfo {
  final String sessionId;
  final String sessionType;
  final String status;
  final String? activityState;
  final DateTime? createdAt;
  final String? title;
  final String workerId;
  // Token usage
  final int inputTokens;
  final int outputTokens;
  final int cacheCreationInputTokens;
  final int cacheReadInputTokens;
  final int toolInputTokens;
  final int toolOutputTokens;
  final double toolCostUsd;

  SessionInfo({
    required this.sessionId,
    required this.sessionType,
    required this.status,
    this.activityState,
    this.createdAt,
    this.title,
    this.workerId = '',
    this.inputTokens = 0,
    this.outputTokens = 0,
    this.cacheCreationInputTokens = 0,
    this.cacheReadInputTokens = 0,
    this.toolInputTokens = 0,
    this.toolOutputTokens = 0,
    this.toolCostUsd = 0.0,
  });

  bool get isProcessing {
    final s = activityState;
    return s != null && s != 'idle';
  }

  int get totalInputTokens => inputTokens + toolInputTokens;
  int get totalOutputTokens => outputTokens + toolOutputTokens;

  factory SessionInfo.fromJson(Map<String, dynamic> json,
      {String workerId = ''}) {
    DateTime? createdAt;
    final raw = json['created_at'] as String?;
    if (raw != null) {
      createdAt = DateTime.tryParse(raw);
    }
    return SessionInfo(
      sessionId: json['session_id'] as String,
      sessionType: json['session_type'] as String? ?? 'unknown',
      status: json['status'] as String? ?? 'unknown',
      activityState: json['activity_state'] as String?,
      createdAt: createdAt,
      title: json['title'] as String?,
      workerId: json['worker_id'] as String? ?? workerId,
      inputTokens: (json['input_tokens'] as num?)?.toInt() ?? 0,
      outputTokens: (json['output_tokens'] as num?)?.toInt() ?? 0,
      cacheCreationInputTokens:
          (json['cache_creation_input_tokens'] as num?)?.toInt() ?? 0,
      cacheReadInputTokens:
          (json['cache_read_input_tokens'] as num?)?.toInt() ?? 0,
      toolInputTokens: (json['tool_input_tokens'] as num?)?.toInt() ?? 0,
      toolOutputTokens: (json['tool_output_tokens'] as num?)?.toInt() ?? 0,
      toolCostUsd: (json['tool_cost_usd'] as num?)?.toDouble() ?? 0.0,
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
        'input_tokens': inputTokens,
        'output_tokens': outputTokens,
        'cache_creation_input_tokens': cacheCreationInputTokens,
        'cache_read_input_tokens': cacheReadInputTokens,
        'tool_input_tokens': toolInputTokens,
        'tool_output_tokens': toolOutputTokens,
        'tool_cost_usd': toolCostUsd,
      };

  String get shortId => sessionId.length >= 8
      ? '${sessionId.substring(0, 8)}...'
      : sessionId;

  SessionInfo copyWith({
    String? sessionId,
    String? sessionType,
    String? status,
    String? activityState,
    DateTime? createdAt,
    String? title,
    String? workerId,
    int? inputTokens,
    int? outputTokens,
    int? cacheCreationInputTokens,
    int? cacheReadInputTokens,
    int? toolInputTokens,
    int? toolOutputTokens,
    double? toolCostUsd,
  }) {
    return SessionInfo(
      sessionId: sessionId ?? this.sessionId,
      sessionType: sessionType ?? this.sessionType,
      status: status ?? this.status,
      activityState: activityState ?? this.activityState,
      createdAt: createdAt ?? this.createdAt,
      title: title ?? this.title,
      workerId: workerId ?? this.workerId,
      inputTokens: inputTokens ?? this.inputTokens,
      outputTokens: outputTokens ?? this.outputTokens,
      cacheCreationInputTokens:
          cacheCreationInputTokens ?? this.cacheCreationInputTokens,
      cacheReadInputTokens:
          cacheReadInputTokens ?? this.cacheReadInputTokens,
      toolInputTokens: toolInputTokens ?? this.toolInputTokens,
      toolOutputTokens: toolOutputTokens ?? this.toolOutputTokens,
      toolCostUsd: toolCostUsd ?? this.toolCostUsd,
    );
  }
}
