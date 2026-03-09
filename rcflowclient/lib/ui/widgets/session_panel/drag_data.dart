/// Data carried during a terminal drag from the sidebar.
class TerminalDragData {
  final String terminalId;
  final String workerId;
  final String label;

  const TerminalDragData({
    required this.terminalId,
    required this.workerId,
    required this.label,
  });
}
