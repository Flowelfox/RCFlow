/// Typed enumeration of WebSocket output message types from the backend.
///
/// Using an enum instead of raw strings provides compile-time safety: registry
/// keys, sound-trigger sets, and notification sets are checked at build time
/// rather than discovered as null lookups at runtime.
///
/// Conversion from the raw JSON ``type`` field happens exactly once at the
/// protocol boundary (in AppState._handleOutputMessage and
/// PaneState._loadHistory) via [WsOutputType.tryParse].
enum WsOutputType {
  // --- Per-pane output (dispatched via outputHandlerRegistry) ---
  textChunk,
  toolStart,
  toolOutput,
  error,
  summary,
  turnComplete,
  sessionEnd,
  sessionPaused,
  sessionResumed,
  sessionRestored,
  todoUpdate,
  thinking,
  agentSessionStart,
  agentGroupStart,
  agentGroupEnd,
  planModeAsk,
  planReviewAsk,
  permissionRequest,
  subprocessStatus,
  messageQueued,
  messageDequeued,
  messageQueuedUpdated,
  cancelAck,
  editAck,

  // --- App-level (handled in AppState before pane dispatch) ---
  taskList,
  taskUpdate,
  taskDeleted,
  artifactList,
  artifactUpdate,
  artifactDeleted,
  linearIssueList,
  linearIssueUpdate,
  linearIssueDeleted,

  // --- Diagnostic / log (silently consumed, not rendered) ---
  agentLog,

  // --- Worker-level (handled in WorkerConnection, never reach AppState) ---
  sessionList,
  sessionUpdate,
  sessionReorder,
  draftUpdate,
  ;

  /// Parse a raw JSON type string. Returns null for unknown types so callers
  /// can handle the unknown-type path explicitly without try/catch.
  static WsOutputType? tryParse(String? s) {
    return switch (s) {
      'text_chunk' => WsOutputType.textChunk,
      'tool_start' => WsOutputType.toolStart,
      'tool_output' => WsOutputType.toolOutput,
      'error' => WsOutputType.error,
      'summary' => WsOutputType.summary,
      'turn_complete' => WsOutputType.turnComplete,
      'session_end' => WsOutputType.sessionEnd,
      'session_paused' => WsOutputType.sessionPaused,
      'session_resumed' => WsOutputType.sessionResumed,
      'session_restored' => WsOutputType.sessionRestored,
      'todo_update' => WsOutputType.todoUpdate,
      'thinking' => WsOutputType.thinking,
      'agent_session_start' => WsOutputType.agentSessionStart,
      'agent_group_start' => WsOutputType.agentGroupStart,
      'agent_group_end' => WsOutputType.agentGroupEnd,
      'plan_mode_ask' => WsOutputType.planModeAsk,
      'plan_review_ask' => WsOutputType.planReviewAsk,
      'permission_request' => WsOutputType.permissionRequest,
      'subprocess_status' => WsOutputType.subprocessStatus,
      'message_queued' => WsOutputType.messageQueued,
      'message_dequeued' => WsOutputType.messageDequeued,
      'message_queued_updated' => WsOutputType.messageQueuedUpdated,
      'cancel_ack' => WsOutputType.cancelAck,
      'edit_ack' => WsOutputType.editAck,
      'task_list' => WsOutputType.taskList,
      'task_update' => WsOutputType.taskUpdate,
      'task_deleted' => WsOutputType.taskDeleted,
      'artifact_list' => WsOutputType.artifactList,
      'artifact_update' => WsOutputType.artifactUpdate,
      'artifact_deleted' => WsOutputType.artifactDeleted,
      'linear_issue_list' => WsOutputType.linearIssueList,
      'linear_issue_update' => WsOutputType.linearIssueUpdate,
      'linear_issue_deleted' => WsOutputType.linearIssueDeleted,
      'agent_log' => WsOutputType.agentLog,
      'session_list' => WsOutputType.sessionList,
      'session_update' => WsOutputType.sessionUpdate,
      'session_reorder' => WsOutputType.sessionReorder,
      'draft_update' => WsOutputType.draftUpdate,
      _ => null,
    };
  }
}
