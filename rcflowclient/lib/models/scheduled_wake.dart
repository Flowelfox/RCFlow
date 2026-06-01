/// One pending Claude Code `ScheduleWakeup` call.
///
/// Mirrors the server's `ScheduledWake` dataclass.  The full list of
/// pending wakes for a session is delivered via
/// `session_update.scheduled_wakes`; the wakeup badge label is driven
/// directly off this list.
class ScheduledWake {
  /// Server-assigned wake identifier — used as the path segment of the
  /// DELETE cancel endpoint.
  final String wakeId;

  /// The prompt that will be fired back into the agent when the timer
  /// expires.
  final String prompt;

  /// One-line context the assistant supplied so the user can see *why*
  /// the wake was scheduled (shown in the inline card).
  final String reason;

  /// UTC timestamp the wake should fire.  Drives the countdown shown
  /// in both the badge and the inline card.
  final DateTime fireAt;

  /// When the wake was originally scheduled.
  final DateTime createdAt;

  const ScheduledWake({
    required this.wakeId,
    required this.prompt,
    required this.reason,
    required this.fireAt,
    required this.createdAt,
  });

  factory ScheduledWake.fromJson(Map<String, dynamic> json) {
    return ScheduledWake(
      wakeId: json['wake_id'] as String,
      prompt: (json['prompt'] as String?) ?? '',
      reason: (json['reason'] as String?) ?? '',
      fireAt: DateTime.parse(json['fire_at'] as String).toLocal(),
      createdAt: DateTime.parse(json['created_at'] as String).toLocal(),
    );
  }

  Map<String, dynamic> toJson() => {
    'wake_id': wakeId,
    'prompt': prompt,
    'reason': reason,
    'fire_at': fireAt.toUtc().toIso8601String(),
    'created_at': createdAt.toUtc().toIso8601String(),
  };
}
