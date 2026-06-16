/// Account-level Claude subscription usage for one worker.
///
/// Mirrors the backend ``worker_usage`` broadcast / ``GET /api/worker/usage``
/// payload: the rolling 5-hour and 7-day quota windows (plus per-model 7-day
/// windows when the upstream endpoint reports them). Every window is optional —
/// the endpoint is undocumented and subscription-only, so [available] is false
/// for API-key workers or before the first successful poll.
class UsageWindow {
  /// Used percentage of the window, 0–100.
  final double utilization;

  /// When the window resets, or null if unknown.
  final DateTime? resetsAt;

  const UsageWindow({required this.utilization, this.resetsAt});

  static UsageWindow? fromJson(Object? value) {
    if (value is! Map) return null;
    final util = value['utilization'];
    if (util is! num) return null;
    final resetsRaw = value['resets_at'];
    return UsageWindow(
      utilization: util.toDouble(),
      resetsAt: resetsRaw is String ? DateTime.tryParse(resetsRaw) : null,
    );
  }
}

class WorkerUsage {
  /// Whether quota data is available (subscription worker, polled at least once).
  final bool available;
  final UsageWindow? fiveHour;
  final UsageWindow? sevenDay;
  final UsageWindow? sevenDayOpus;
  final UsageWindow? sevenDaySonnet;

  const WorkerUsage({
    required this.available,
    this.fiveHour,
    this.sevenDay,
    this.sevenDayOpus,
    this.sevenDaySonnet,
  });

  /// Unavailable sentinel — hides the quota chip.
  static const WorkerUsage unavailable = WorkerUsage(available: false);

  /// Whether the chip has at least one headline window to show.
  bool get hasData => available && (fiveHour != null || sevenDay != null);

  factory WorkerUsage.fromJson(Map<String, dynamic> msg) {
    if (msg['available'] != true) return WorkerUsage.unavailable;
    return WorkerUsage(
      available: true,
      fiveHour: UsageWindow.fromJson(msg['five_hour']),
      sevenDay: UsageWindow.fromJson(msg['seven_day']),
      sevenDayOpus: UsageWindow.fromJson(msg['seven_day_opus']),
      sevenDaySonnet: UsageWindow.fromJson(msg['seven_day_sonnet']),
    );
  }
}
