enum DisplayMessageType {
  user,
  assistant,
  toolBlock,
  error,
  system,
  summary,
  sessionEndAsk,
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

  /// For sessionEndAsk: null = pending, true = user ended, false = user continued.
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
