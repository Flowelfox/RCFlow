enum TodoStatus { pending, inProgress, completed }

class TodoItem {
  final String content;
  final TodoStatus status;
  final String? activeForm;

  TodoItem({required this.content, required this.status, this.activeForm});

  factory TodoItem.fromJson(Map<String, dynamic> json) {
    return TodoItem(
      content: json['content'] as String? ?? '',
      status: _parseStatus(json['status'] as String? ?? 'pending'),
      activeForm: json['activeForm'] as String?,
    );
  }

  static TodoStatus _parseStatus(String s) => switch (s) {
    'in_progress' => TodoStatus.inProgress,
    'completed' => TodoStatus.completed,
    _ => TodoStatus.pending,
  };

  /// Display text: use activeForm for in-progress items, content otherwise.
  String get displayText =>
      status == TodoStatus.inProgress && activeForm != null
      ? activeForm!
      : content;
}
