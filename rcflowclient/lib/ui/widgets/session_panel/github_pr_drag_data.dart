/// Data carried during a pull-request drag from the sidebar.
class GithubPrDragData {
  final String prId;
  final String workerId;
  final String label;

  const GithubPrDragData({
    required this.prId,
    required this.workerId,
    required this.label,
  });
}
