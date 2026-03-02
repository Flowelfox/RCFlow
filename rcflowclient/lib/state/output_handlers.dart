/// Output message handler registry.
///
/// Each backend WebSocket message type (e.g. `text_chunk`, `tool_start`)
/// maps to a handler function that processes the raw message and updates
/// [PaneState]. To add a new backend message type:
///
/// 1. Write a handler function below
/// 2. Add it to [outputHandlerRegistry]
/// 3. If the handler introduces a new [DisplayMessageType], also create a
///    renderer widget in `ui/widgets/message_components/` and register it
///    in [messageRenderers] (see `message_bubble.dart`).
library;

import '../models/ws_messages.dart';
import 'pane_state.dart';

/// Signature for per-pane output message handlers.
typedef OutputHandler = void Function(
    Map<String, dynamic> msg, PaneState pane);

// ---------------------------------------------------------------------------
// Handler implementations
// ---------------------------------------------------------------------------

void handleTextChunk(Map<String, dynamic> msg, PaneState pane) {
  if (msg['role'] == 'user') {
    final content = msg['content'] as String? ?? '';
    // Skip server echo if the message was already added locally by sendPrompt.
    // Uses content-based matching to be robust against counter desync.
    if (pane.consumeLocalUserMessage(content)) return;
    pane.addDisplayMessage(DisplayMessage(
      type: DisplayMessageType.user,
      content: content,
      sessionId: msg['session_id'] as String?,
      finished: true,
    ));
    return;
  }

  var content =
      (msg['content'] as String? ?? '').replaceAll('[SessionEndAsk]', '');
  if (content.isNotEmpty) {
    pane.appendAssistantChunk(content);
  }
}

void handleToolStart(Map<String, dynamic> msg, PaneState pane) {
  pane.startToolBlock(
    msg['tool_name'] as String? ?? 'unknown',
    msg['tool_input'] as Map<String, dynamic>?,
  );
}

void handleToolOutput(Map<String, dynamic> msg, PaneState pane) {
  pane.appendToolOutput(msg['content'] as String? ?? '');
}

void handleError(Map<String, dynamic> msg, PaneState pane) {
  pane.finalizeStream();
  final code = msg['code'] as String?;
  final sessionId = msg['session_id'] as String?;

  if (code == 'SESSION_NOT_FOUND' && sessionId != null) {
    pane.handleSessionNotFound(sessionId);
    return;
  }

  pane.addSystemMessage(msg['content'] as String? ?? 'Unknown error',
      isError: true);
}

void handleSummary(Map<String, dynamic> msg, PaneState pane) {
  pane.finalizeStream();
  pane.addDisplayMessage(DisplayMessage(
    type: DisplayMessageType.summary,
    content: msg['content'] as String? ?? '',
    sessionId: msg['session_id'] as String?,
    finished: true,
  ));
}

void handleSessionEndAsk(Map<String, dynamic> msg, PaneState pane) {
  pane.finalizeStream();
  pane.stripSessionEndAskTag();
  pane.addDisplayMessage(DisplayMessage(
    type: DisplayMessageType.sessionEndAsk,
    sessionId: msg['session_id'] as String?,
    accepted: msg['accepted'] as bool?,
  ));
}

void handlePlanModeAsk(Map<String, dynamic> msg, PaneState pane) {
  pane.finalizeStream();
  pane.addDisplayMessage(DisplayMessage(
    type: DisplayMessageType.planModeAsk,
    sessionId: msg['session_id'] as String?,
    accepted: msg['accepted'] as bool?,
  ));
}

void handlePlanReviewAsk(Map<String, dynamic> msg, PaneState pane) {
  pane.finalizeStream();
  pane.addDisplayMessage(DisplayMessage(
    type: DisplayMessageType.planReviewAsk,
    sessionId: msg['session_id'] as String?,
    accepted: msg['accepted'] as bool?,
  ));
}

void handleAgentGroupStart(Map<String, dynamic> msg, PaneState pane) {
  pane.startAgentGroup(
    msg['tool_name'] as String? ?? 'claude_code',
    msg['tool_input'] as Map<String, dynamic>?,
  );
}

void handleAgentGroupEnd(Map<String, dynamic> msg, PaneState pane) {
  pane.endAgentGroup();
}

void handleSessionPaused(Map<String, dynamic> msg, PaneState pane) {
  pane.finalizeStream();
  final pausedId = msg['session_id'] as String?;
  pane.handleSessionPaused(pausedId);
}

void handleSessionResumed(Map<String, dynamic> msg, PaneState pane) {
  final resumedId = msg['session_id'] as String?;
  pane.handleSessionResumed(resumedId);
}

void handleSessionRestored(Map<String, dynamic> msg, PaneState pane) {
  final restoredId = msg['session_id'] as String?;
  pane.handleSessionRestored(restoredId);
}

void handleSessionEnd(Map<String, dynamic> msg, PaneState pane) {
  pane.finalizeStream();
  for (final m in pane.messages) {
    if (m.type == DisplayMessageType.sessionEndAsk && m.accepted == null) {
      m.accepted = true;
    }
  }
  final endedId = msg['session_id'] as String?;
  pane.handleSessionEnded(endedId);
}

// ---------------------------------------------------------------------------
// Registry — per-pane handlers (routed by session_id in AppState)
// ---------------------------------------------------------------------------

/// Maps backend message type strings to per-pane handler functions.
/// Note: `session_list` is handled at the AppState level, not here.
final Map<String, OutputHandler> outputHandlerRegistry = {
  'text_chunk': handleTextChunk,
  'tool_start': handleToolStart,
  'tool_output': handleToolOutput,
  'error': handleError,
  'summary': handleSummary,
  'session_end_ask': handleSessionEndAsk,
  'session_end': handleSessionEnd,
  'session_paused': handleSessionPaused,
  'session_resumed': handleSessionResumed,
  'session_restored': handleSessionRestored,
  'agent_group_start': handleAgentGroupStart,
  'agent_group_end': handleAgentGroupEnd,
  'plan_mode_ask': handlePlanModeAsk,
  'plan_review_ask': handlePlanReviewAsk,
};

// ---------------------------------------------------------------------------
// History builders — reconstruct DisplayMessages from archived session data
// ---------------------------------------------------------------------------

/// Signature for history message builders.
typedef HistoryBuilder = void Function(
  Map<String, dynamic> msg,
  String sessionId,
  List<DisplayMessage> messages,
);

void buildTextChunkHistory(Map<String, dynamic> msg, String sessionId,
    List<DisplayMessage> messages) {
  final content =
      (msg['content'] as String? ?? '').replaceAll('[SessionEndAsk]', '');
  final metadata = msg['metadata'] as Map<String, dynamic>? ?? {};

  if (metadata['role'] == 'user') {
    messages.add(DisplayMessage(
      type: DisplayMessageType.user,
      content: content,
      sessionId: sessionId,
      finished: true,
    ));
  } else {
    if (messages.isNotEmpty &&
        messages.last.type == DisplayMessageType.assistant &&
        messages.last.sessionId == sessionId) {
      messages.last.content += content;
    } else {
      messages.add(DisplayMessage(
        type: DisplayMessageType.assistant,
        content: content,
        sessionId: sessionId,
        finished: true,
      ));
    }
  }
}

void buildToolStartHistory(Map<String, dynamic> msg, String sessionId,
    List<DisplayMessage> messages) {
  final metadata = msg['metadata'] as Map<String, dynamic>? ?? {};
  messages.add(DisplayMessage(
    type: DisplayMessageType.toolBlock,
    sessionId: sessionId,
    toolName: metadata['tool_name'] as String? ?? 'unknown',
    toolInput: metadata['tool_input'] as Map<String, dynamic>?,
    finished: false,
  ));
}

void buildToolOutputHistory(Map<String, dynamic> msg, String sessionId,
    List<DisplayMessage> messages) {
  final content = msg['content'] as String? ?? '';
  if (messages.isNotEmpty &&
      messages.last.type == DisplayMessageType.toolBlock &&
      messages.last.sessionId == sessionId) {
    messages.last.content += content;
  } else {
    messages.add(DisplayMessage(
      type: DisplayMessageType.toolBlock,
      sessionId: sessionId,
      toolName: 'output',
      content: content,
      finished: true,
    ));
  }
}

void buildErrorHistory(Map<String, dynamic> msg, String sessionId,
    List<DisplayMessage> messages) {
  messages.add(DisplayMessage(
    type: DisplayMessageType.error,
    content: msg['content'] as String? ?? '',
    sessionId: sessionId,
    finished: true,
  ));
}

void buildSummaryHistory(Map<String, dynamic> msg, String sessionId,
    List<DisplayMessage> messages) {
  messages.add(DisplayMessage(
    type: DisplayMessageType.summary,
    content: msg['content'] as String? ?? '',
    sessionId: sessionId,
    finished: true,
  ));
}

void buildSessionEndAskHistory(Map<String, dynamic> msg, String sessionId,
    List<DisplayMessage> messages) {
  for (int i = messages.length - 1; i >= 0; i--) {
    if (messages[i].type == DisplayMessageType.assistant) {
      messages[i].content =
          messages[i].content.replaceAll('[SessionEndAsk]', '').trimRight();
      break;
    }
  }

  final metadata = msg['metadata'] as Map<String, dynamic>? ?? {};
  bool? accepted = metadata['accepted'] as bool?;
  accepted ??= false;
  messages.add(DisplayMessage(
    type: DisplayMessageType.sessionEndAsk,
    sessionId: sessionId,
    accepted: accepted,
  ));
}

void buildSessionEndHistory(Map<String, dynamic> msg, String sessionId,
    List<DisplayMessage> messages) {
  for (final m in messages) {
    if (m.type == DisplayMessageType.toolBlock && !m.finished) {
      m.finished = true;
    }
    if (m.type == DisplayMessageType.agentGroup && !m.finished) {
      m.finished = true;
      for (final child in m.children ?? <DisplayMessage>[]) {
        if (child.type == DisplayMessageType.toolBlock && !child.finished) {
          child.finished = true;
        }
      }
    }
    if (m.type == DisplayMessageType.sessionEndAsk && m.accepted == null) {
      m.accepted = true;
    }
  }
}

void buildSessionPausedHistory(Map<String, dynamic> msg, String sessionId,
    List<DisplayMessage> messages) {
  messages.add(DisplayMessage(
    type: DisplayMessageType.system,
    content: 'Session paused',
    sessionId: sessionId,
    finished: true,
  ));
}

void buildSessionResumedHistory(Map<String, dynamic> msg, String sessionId,
    List<DisplayMessage> messages) {
  messages.add(DisplayMessage(
    type: DisplayMessageType.system,
    content: 'Session resumed',
    sessionId: sessionId,
    finished: true,
  ));
}

void buildPlanModeAskHistory(Map<String, dynamic> msg, String sessionId,
    List<DisplayMessage> messages) {
  final metadata = msg['metadata'] as Map<String, dynamic>? ?? {};
  messages.add(DisplayMessage(
    type: DisplayMessageType.planModeAsk,
    sessionId: sessionId,
    accepted: metadata['accepted'] as bool? ?? true,
  ));
}

void buildPlanReviewAskHistory(Map<String, dynamic> msg, String sessionId,
    List<DisplayMessage> messages) {
  final metadata = msg['metadata'] as Map<String, dynamic>? ?? {};
  messages.add(DisplayMessage(
    type: DisplayMessageType.planReviewAsk,
    sessionId: sessionId,
    accepted: metadata['accepted'] as bool? ?? true,
  ));
}

/// Maps archived message type strings to history builder functions.
final Map<String, HistoryBuilder> historyBuilderRegistry = {
  'text_chunk': buildTextChunkHistory,
  'tool_start': buildToolStartHistory,
  'tool_output': buildToolOutputHistory,
  'error': buildErrorHistory,
  'summary': buildSummaryHistory,
  'session_end_ask': buildSessionEndAskHistory,
  'session_end': buildSessionEndHistory,
  'session_paused': buildSessionPausedHistory,
  'session_resumed': buildSessionResumedHistory,
  'plan_mode_ask': buildPlanModeAskHistory,
  'plan_review_ask': buildPlanReviewAskHistory,
};
