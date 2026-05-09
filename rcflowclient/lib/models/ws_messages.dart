enum DisplayMessageType {
  user,
  assistant,
  toolBlock,
  error,
  system,
  summary,
  planModeAsk,
  planReviewAsk,
  permissionRequest,
  agentGroup,
  agentSessionStart,
  thinking,
  todoUpdate,

  /// Session was automatically paused because Claude Code reached its
  /// configured maximum number of turns (--max-turns limit).
  pausedMaxTurns,

  /// A live Claude Code ``Monitor`` watch — streams stdout-line notifications
  /// from a backgrounded script with a live elapsed-time counter.
  monitorBlock,
}

/// Single stdout-line batch from a live Monitor watch.
class MonitorEvent {
  final DateTime receivedAt;
  final String content;
  final bool isError;
  final int sequence;

  const MonitorEvent({
    required this.receivedAt,
    required this.content,
    required this.isError,
    required this.sequence,
  });

  factory MonitorEvent.fromJson(Map<String, dynamic> json) {
    return MonitorEvent(
      receivedAt:
          DateTime.tryParse(json['received_at'] as String? ?? '') ??
              DateTime.now(),
      content: json['content'] as String? ?? '',
      isError: json['is_error'] as bool? ?? false,
      sequence: (json['sequence'] as num?)?.toInt() ?? 0,
    );
  }
}

class DisplayMessage {
  final DisplayMessageType type;
  final String? sessionId;
  final String? toolName;
  final String? displayName;
  final Map<String, dynamic>? toolInput;
  String content;
  bool finished;
  bool expanded;
  bool isError;

  /// For planModeAsk/planReviewAsk: null = pending, true = accepted, false = declined.
  bool? accepted;

  /// Tracks user-selected answers for AskUserQuestion tool blocks.
  /// Keys are question texts, values are selected labels (or custom text).
  Map<String, String>? selectedAnswers;

  /// Child messages for agentGroup type (Claude Code sub-messages).
  List<DisplayMessage>? children;

  /// True for user messages added locally by [sendPrompt] that have not yet
  /// been confirmed by a server echo. Used for content-based deduplication.
  bool pendingLocalEcho;

  /// File attachments included with this user message.
  /// Each entry has at minimum: ``name`` (String) and ``mime_type`` (String).
  /// May also include ``size`` (int) and ``attachment_id`` (String).
  List<Map<String, dynamic>>? attachments;

  /// Unified diff string attached to a toolBlock message (e.g. Write/Edit tool).
  /// Null when no diff was included in the server message.
  String? fileDiff;

  // ---------------- Monitor block fields ----------------

  /// Stable id for a monitor block, matching the Claude Code ``tool_use_id``.
  /// Lookups in [PaneState] live-event routing key off this id.
  final String? monitorId;

  /// Wall-clock time the monitor was started — drives the live elapsed counter.
  final DateTime? monitorStartedAt;

  /// Configured timeout in milliseconds; non-persistent monitors only.
  final int? monitorTimeoutMs;

  /// True when this is a session-lifetime watch (no timeout deadline).
  final bool? monitorPersistent;

  /// Termination reason once finished: ``exit | timeout | cancelled | session_end | error``.
  String? monitorTerminationReason;

  /// Process exit code when ``monitorTerminationReason == 'exit'``.
  int? monitorExitCode;

  /// Streaming list of stdout-line batches received while the monitor was live.
  /// Capped at 200 entries to bound memory; oldest are dropped first.
  List<MonitorEvent>? monitorEvents;

  /// Total event count reported by the backend (may exceed ``monitorEvents.length``
  /// when older events have been dropped from the local cap).
  int monitorTotalEvents;

  DisplayMessage({
    required this.type,
    this.content = '',
    this.sessionId,
    this.toolName,
    this.displayName,
    this.toolInput,
    this.finished = false,
    this.expanded = false,
    this.isError = false,
    this.accepted,
    this.children,
    this.pendingLocalEcho = false,
    this.attachments,
    this.fileDiff,
    this.monitorId,
    this.monitorStartedAt,
    this.monitorTimeoutMs,
    this.monitorPersistent,
    this.monitorTerminationReason,
    this.monitorExitCode,
    this.monitorEvents,
    this.monitorTotalEvents = 0,
  });

  bool get isQuestion => toolName == 'AskUserQuestion';

  /// Number of toolBlock children in this agent group.
  int get toolCount =>
      children?.where((c) => c.type == DisplayMessageType.toolBlock).length ??
      0;

  /// Whether this agent group is still running (has unfinished toolBlock children).
  bool get isGroupRunning =>
      !finished ||
      (children?.any(
            (c) => c.type == DisplayMessageType.toolBlock && !c.finished,
          ) ??
          false);
}

/// A user message pinned at the bottom of the chat while the agent is busy.
///
/// Entries live in ``PaneState._queuedMessages`` and mirror the backend's
/// ``session_pending_messages`` rows.  Once the backend drains the message
/// (via a ``message_dequeued`` event), the corresponding entry is removed
/// here and the normal ``text_chunk`` echo inserts it into the chat history
/// at its delivered position.
class QueuedMessage {
  final String queuedId;
  int position;
  String content;
  String displayContent;
  DateTime submittedAt;
  DateTime updatedAt;

  /// True when the local optimistic add has not yet been confirmed by a
  /// server-side ``message_queued`` broadcast.  Used for de-duplication
  /// during reconcile — identical to ``DisplayMessage.pendingLocalEcho``.
  bool pendingLocalEcho;

  QueuedMessage({
    required this.queuedId,
    required this.position,
    required this.content,
    required this.displayContent,
    required this.submittedAt,
    required this.updatedAt,
    this.pendingLocalEcho = false,
  });

  factory QueuedMessage.fromSnapshot(Map<String, dynamic> json) {
    return QueuedMessage(
      queuedId: json['queued_id'] as String,
      position: (json['position'] as num?)?.toInt() ?? 0,
      content: json['content'] as String? ?? '',
      displayContent:
          json['display_content'] as String? ?? json['content'] as String? ?? '',
      submittedAt:
          DateTime.tryParse(json['submitted_at'] as String? ?? '') ??
              DateTime.now(),
      updatedAt:
          DateTime.tryParse(json['updated_at'] as String? ?? '') ??
              DateTime.now(),
    );
  }
}
