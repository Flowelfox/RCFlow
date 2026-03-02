class SessionInfo {
  final String sessionId;
  final String sessionType;
  final String status;
  final String? activityState;
  final DateTime? createdAt;
  final String? title;
  final String workerId;

  SessionInfo({
    required this.sessionId,
    required this.sessionType,
    required this.status,
    this.activityState,
    this.createdAt,
    this.title,
    this.workerId = '',
  });

  bool get isProcessing {
    final s = activityState;
    return s != null && s != 'idle';
  }

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
  }) {
    return SessionInfo(
      sessionId: sessionId ?? this.sessionId,
      sessionType: sessionType ?? this.sessionType,
      status: status ?? this.status,
      activityState: activityState ?? this.activityState,
      createdAt: createdAt ?? this.createdAt,
      title: title ?? this.title,
      workerId: workerId ?? this.workerId,
    );
  }
}
