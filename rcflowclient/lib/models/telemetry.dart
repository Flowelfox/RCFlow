/// Telemetry data models for the Statistics pane.
library;

enum ZoomLevel { minute, hour, day }

extension ZoomLevelExt on ZoomLevel {
  String get apiValue => name;

  String get label {
    switch (this) {
      case ZoomLevel.minute:
        return 'Minute';
      case ZoomLevel.hour:
        return 'Hour';
      case ZoomLevel.day:
        return 'Day';
    }
  }

  Duration get defaultWindowDuration {
    switch (this) {
      case ZoomLevel.minute:
        return const Duration(hours: 1);
      case ZoomLevel.hour:
        return const Duration(hours: 24);
      case ZoomLevel.day:
        return const Duration(days: 30);
    }
  }
}

/// One pre-aggregated time bucket from the time-series endpoint.
class BucketPoint {
  final DateTime bucket;
  final int tokensSent;
  final int tokensReceived;
  final int cacheCreation;
  final int cacheRead;
  final double? avgLlmDurationMs;
  final double? avgToolDurationMs;
  final int turnCount;
  final int toolCallCount;
  final int errorCount;

  const BucketPoint({
    required this.bucket,
    required this.tokensSent,
    required this.tokensReceived,
    required this.cacheCreation,
    required this.cacheRead,
    this.avgLlmDurationMs,
    this.avgToolDurationMs,
    required this.turnCount,
    required this.toolCallCount,
    required this.errorCount,
  });

  factory BucketPoint.fromJson(Map<String, dynamic> json) {
    return BucketPoint(
      bucket: DateTime.parse(json['bucket'] as String),
      tokensSent: (json['tokens_sent'] as num?)?.toInt() ?? 0,
      tokensReceived: (json['tokens_received'] as num?)?.toInt() ?? 0,
      cacheCreation: (json['cache_creation'] as num?)?.toInt() ?? 0,
      cacheRead: (json['cache_read'] as num?)?.toInt() ?? 0,
      avgLlmDurationMs: (json['avg_llm_duration_ms'] as num?)?.toDouble(),
      avgToolDurationMs: (json['avg_tool_duration_ms'] as num?)?.toDouble(),
      turnCount: (json['turn_count'] as num?)?.toInt() ?? 0,
      toolCallCount: (json['tool_call_count'] as num?)?.toInt() ?? 0,
      errorCount: (json['error_count'] as num?)?.toInt() ?? 0,
    );
  }
}

/// Per-turn row in a session telemetry summary.
class TurnSummary {
  final int turnIndex;
  final DateTime? tsStart;
  final DateTime? tsEnd;
  final int? llmDurationMs;
  final int? ttftMs;
  final int inputTokens;
  final int outputTokens;
  final int cacheCreationTokens;
  final int cacheReadTokens;
  final int toolCalls;
  final String? model;
  final bool interrupted;

  const TurnSummary({
    required this.turnIndex,
    this.tsStart,
    this.tsEnd,
    this.llmDurationMs,
    this.ttftMs,
    required this.inputTokens,
    required this.outputTokens,
    required this.cacheCreationTokens,
    required this.cacheReadTokens,
    required this.toolCalls,
    this.model,
    required this.interrupted,
  });

  factory TurnSummary.fromJson(Map<String, dynamic> json) {
    return TurnSummary(
      turnIndex: (json['turn_index'] as num).toInt(),
      tsStart: json['ts_start'] != null
          ? DateTime.tryParse(json['ts_start'] as String)
          : null,
      tsEnd: json['ts_end'] != null
          ? DateTime.tryParse(json['ts_end'] as String)
          : null,
      llmDurationMs: (json['llm_duration_ms'] as num?)?.toInt(),
      ttftMs: (json['ttft_ms'] as num?)?.toInt(),
      inputTokens: (json['input_tokens'] as num?)?.toInt() ?? 0,
      outputTokens: (json['output_tokens'] as num?)?.toInt() ?? 0,
      cacheCreationTokens: (json['cache_creation_tokens'] as num?)?.toInt() ?? 0,
      cacheReadTokens: (json['cache_read_tokens'] as num?)?.toInt() ?? 0,
      toolCalls: (json['tool_calls'] as num?)?.toInt() ?? 0,
      model: json['model'] as String?,
      interrupted: json['interrupted'] as bool? ?? false,
    );
  }
}

/// Aggregate statistics for a worker across all its sessions.
class WorkerTelemetrySummary {
  final String workerId;
  final int sessionCount;
  final int turnCount;
  final int totalInputTokens;
  final int totalOutputTokens;
  final int totalCacheCreationTokens;
  final int totalCacheReadTokens;
  final int totalToolCalls;
  final double? avgLlmDurationMs;
  final double? p95LlmDurationMs;
  final double? avgToolDurationMs;
  final double? p95ToolDurationMs;
  final double errorRate;
  final List<Map<String, dynamic>> topTools;

  const WorkerTelemetrySummary({
    required this.workerId,
    required this.sessionCount,
    required this.turnCount,
    required this.totalInputTokens,
    required this.totalOutputTokens,
    required this.totalCacheCreationTokens,
    required this.totalCacheReadTokens,
    required this.totalToolCalls,
    this.avgLlmDurationMs,
    this.p95LlmDurationMs,
    this.avgToolDurationMs,
    this.p95ToolDurationMs,
    required this.errorRate,
    required this.topTools,
  });

  factory WorkerTelemetrySummary.fromJson(Map<String, dynamic> json) {
    final rawTools = (json['top_tools'] as List<dynamic>?) ?? [];
    return WorkerTelemetrySummary(
      workerId: json['worker_id'] as String,
      sessionCount: (json['session_count'] as num?)?.toInt() ?? 0,
      turnCount: (json['turn_count'] as num?)?.toInt() ?? 0,
      totalInputTokens: (json['total_input_tokens'] as num?)?.toInt() ?? 0,
      totalOutputTokens: (json['total_output_tokens'] as num?)?.toInt() ?? 0,
      totalCacheCreationTokens:
          (json['total_cache_creation_tokens'] as num?)?.toInt() ?? 0,
      totalCacheReadTokens:
          (json['total_cache_read_tokens'] as num?)?.toInt() ?? 0,
      totalToolCalls: (json['total_tool_calls'] as num?)?.toInt() ?? 0,
      avgLlmDurationMs: (json['avg_llm_duration_ms'] as num?)?.toDouble(),
      p95LlmDurationMs: (json['p95_llm_duration_ms'] as num?)?.toDouble(),
      avgToolDurationMs: (json['avg_tool_duration_ms'] as num?)?.toDouble(),
      p95ToolDurationMs: (json['p95_tool_duration_ms'] as num?)?.toDouble(),
      errorRate: (json['error_rate'] as num?)?.toDouble() ?? 0.0,
      topTools: rawTools.whereType<Map<String, dynamic>>().toList(),
    );
  }
}

/// Aggregate statistics for a single session.
class SessionTelemetrySummary {
  final String sessionId;
  final int turnCount;
  final int totalInputTokens;
  final int totalOutputTokens;
  final int totalToolCalls;
  final double? avgLlmDurationMs;
  final double? avgToolDurationMs;
  final double? p95LlmDurationMs;
  final double? p95ToolDurationMs;
  final double errorRate;
  final int? sessionDurationMs;
  final List<TurnSummary> turns;

  const SessionTelemetrySummary({
    required this.sessionId,
    required this.turnCount,
    required this.totalInputTokens,
    required this.totalOutputTokens,
    required this.totalToolCalls,
    this.avgLlmDurationMs,
    this.avgToolDurationMs,
    this.p95LlmDurationMs,
    this.p95ToolDurationMs,
    required this.errorRate,
    this.sessionDurationMs,
    required this.turns,
  });

  factory SessionTelemetrySummary.fromJson(Map<String, dynamic> json) {
    final rawTurns = (json['turns'] as List<dynamic>?) ?? [];
    return SessionTelemetrySummary(
      sessionId: json['session_id'] as String,
      turnCount: (json['turn_count'] as num?)?.toInt() ?? 0,
      totalInputTokens: (json['total_input_tokens'] as num?)?.toInt() ?? 0,
      totalOutputTokens: (json['total_output_tokens'] as num?)?.toInt() ?? 0,
      totalToolCalls: (json['total_tool_calls'] as num?)?.toInt() ?? 0,
      avgLlmDurationMs: (json['avg_llm_duration_ms'] as num?)?.toDouble(),
      avgToolDurationMs: (json['avg_tool_duration_ms'] as num?)?.toDouble(),
      p95LlmDurationMs: (json['p95_llm_duration_ms'] as num?)?.toDouble(),
      p95ToolDurationMs: (json['p95_tool_duration_ms'] as num?)?.toDouble(),
      errorRate: (json['error_rate'] as num?)?.toDouble() ?? 0.0,
      sessionDurationMs: (json['session_duration_ms'] as num?)?.toInt(),
      turns: rawTurns
          .whereType<Map<String, dynamic>>()
          .map(TurnSummary.fromJson)
          .toList(),
    );
  }
}
