/// Data carried during a task drag from the sidebar.
class TaskDragData {
  final String taskId;
  final String workerId;
  final String label;

  const TaskDragData({
    required this.taskId,
    required this.workerId,
    required this.label,
  });
}
