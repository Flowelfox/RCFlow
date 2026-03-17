/// Model for a cached Linear issue returned by the RCFlow backend.
class LinearIssueInfo {
  final String id; // local UUID
  final String linearId;
  final String identifier; // e.g. "ENG-123"
  String title;
  String? description;
  int priority; // 0=none 1=urgent 2=high 3=medium 4=low
  String stateName;
  String stateType; // triage|backlog|unstarted|started|completed|cancelled
  String? assigneeId;
  String? assigneeName;
  final String teamId;
  final String? teamName;
  final String url;
  List<String> labels;
  final DateTime createdAt;
  DateTime updatedAt;
  DateTime syncedAt;
  String? taskId; // linked local task UUID (nullable)
  final String workerId;
  final String workerName;

  LinearIssueInfo({
    required this.id,
    required this.linearId,
    required this.identifier,
    required this.title,
    this.description,
    required this.priority,
    required this.stateName,
    required this.stateType,
    this.assigneeId,
    this.assigneeName,
    required this.teamId,
    this.teamName,
    required this.url,
    required this.labels,
    required this.createdAt,
    required this.updatedAt,
    required this.syncedAt,
    this.taskId,
    required this.workerId,
    required this.workerName,
  });

  factory LinearIssueInfo.fromJson(
    Map<String, dynamic> json, {
    String workerId = '',
    String workerName = '',
  }) {
    final labelsRaw = json['labels'];
    final List<String> labels;
    if (labelsRaw is List) {
      labels = labelsRaw.map((e) => e.toString()).toList();
    } else {
      labels = [];
    }

    return LinearIssueInfo(
      id: json['id'] as String,
      linearId: json['linear_id'] as String,
      identifier: json['identifier'] as String? ?? '',
      title: json['title'] as String? ?? '',
      description: json['description'] as String?,
      priority: (json['priority'] as num?)?.toInt() ?? 0,
      stateName: json['state_name'] as String? ?? '',
      stateType: json['state_type'] as String? ?? '',
      assigneeId: json['assignee_id'] as String?,
      assigneeName: json['assignee_name'] as String?,
      teamId: json['team_id'] as String? ?? '',
      teamName: json['team_name'] as String?,
      url: json['url'] as String? ?? '',
      labels: labels,
      createdAt: DateTime.tryParse(json['created_at'] as String? ?? '') ?? DateTime.now(),
      updatedAt: DateTime.tryParse(json['updated_at'] as String? ?? '') ?? DateTime.now(),
      syncedAt: DateTime.tryParse(json['synced_at'] as String? ?? '') ?? DateTime.now(),
      taskId: json['task_id'] as String?,
      workerId: workerId,
      workerName: workerName,
    );
  }

  LinearIssueInfo copyWith({
    String? title,
    String? description,
    int? priority,
    String? stateName,
    String? stateType,
    String? assigneeId,
    String? assigneeName,
    List<String>? labels,
    DateTime? updatedAt,
    DateTime? syncedAt,
    String? taskId,
    bool clearTaskId = false,
  }) {
    return LinearIssueInfo(
      id: id,
      linearId: linearId,
      identifier: identifier,
      title: title ?? this.title,
      description: description ?? this.description,
      priority: priority ?? this.priority,
      stateName: stateName ?? this.stateName,
      stateType: stateType ?? this.stateType,
      assigneeId: assigneeId ?? this.assigneeId,
      assigneeName: assigneeName ?? this.assigneeName,
      teamId: teamId,
      teamName: teamName,
      url: url,
      labels: labels ?? this.labels,
      createdAt: createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
      syncedAt: syncedAt ?? this.syncedAt,
      taskId: clearTaskId ? null : (taskId ?? this.taskId),
      workerId: workerId,
      workerName: workerName,
    );
  }

  /// Human-readable priority label.
  String get priorityLabel => const {
        0: 'No priority',
        1: 'Urgent',
        2: 'High',
        3: 'Medium',
        4: 'Low',
      }[priority] ??
      'Unknown';

  /// Whether the issue is in a terminal state (completed or cancelled).
  bool get isTerminal =>
      stateType == 'completed' || stateType == 'cancelled';
}
