class TaskSessionRef {
  final String sessionId;
  final String? title;
  final String status;
  final DateTime? attachedAt;

  TaskSessionRef({
    required this.sessionId,
    this.title,
    required this.status,
    this.attachedAt,
  });

  factory TaskSessionRef.fromJson(Map<String, dynamic> json) {
    DateTime? attachedAt;
    final raw = json['attached_at'] as String?;
    if (raw != null && raw.isNotEmpty) {
      attachedAt = DateTime.tryParse(raw);
    }
    return TaskSessionRef(
      sessionId: json['session_id'] as String,
      title: json['title'] as String?,
      status: json['status'] as String? ?? 'unknown',
      attachedAt: attachedAt,
    );
  }
}

class TaskInfo {
  final String taskId;
  String title;
  String? description;
  String status;
  final String source;
  final String workerId;
  final String workerName;
  final DateTime createdAt;
  DateTime updatedAt;
  List<TaskSessionRef> sessions;

  TaskInfo({
    required this.taskId,
    required this.title,
    this.description,
    required this.status,
    required this.source,
    required this.workerId,
    required this.workerName,
    required this.createdAt,
    required this.updatedAt,
    this.sessions = const [],
  });

  factory TaskInfo.fromJson(Map<String, dynamic> json, {
    String workerId = '',
    String workerName = '',
  }) {
    final sessionsRaw = json['sessions'] as List<dynamic>? ?? [];
    return TaskInfo(
      taskId: json['task_id'] as String,
      title: json['title'] as String? ?? '',
      description: json['description'] as String?,
      status: json['status'] as String? ?? 'todo',
      source: json['source'] as String? ?? 'user',
      workerId: workerId,
      workerName: workerName,
      createdAt: DateTime.tryParse(json['created_at'] as String? ?? '') ?? DateTime.now(),
      updatedAt: DateTime.tryParse(json['updated_at'] as String? ?? '') ?? DateTime.now(),
      sessions: sessionsRaw
          .map((s) => TaskSessionRef.fromJson(s as Map<String, dynamic>))
          .toList(),
    );
  }

  TaskInfo copyWith({
    String? title,
    String? description,
    String? status,
    DateTime? updatedAt,
    List<TaskSessionRef>? sessions,
  }) {
    return TaskInfo(
      taskId: taskId,
      title: title ?? this.title,
      description: description ?? this.description,
      status: status ?? this.status,
      source: source,
      workerId: workerId,
      workerName: workerName,
      createdAt: createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
      sessions: sessions ?? this.sessions,
    );
  }
}
