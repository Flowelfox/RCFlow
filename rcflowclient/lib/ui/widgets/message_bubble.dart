import 'package:flutter/material.dart';

import '../../models/ws_messages.dart';
import '../../theme.dart';
import 'message_components/agent_group_block.dart';
import 'message_components/assistant_bubble.dart';
import 'message_components/permission_request_card.dart';
import 'message_components/plan_mode_ask_card.dart';
import 'message_components/plan_review_ask_card.dart';
import 'message_components/question_block.dart';
import 'message_components/session_end_ask_card.dart';
import 'message_components/status_chip.dart';
import 'message_components/summary_bubble.dart';
import 'message_components/tool_block.dart';
import 'message_components/user_bubble.dart';

/// Renderer function: given a [DisplayMessage], return the widget for it.
typedef MessageRenderer = Widget Function(DisplayMessage message);

/// Registry mapping each [DisplayMessageType] to its renderer.
///
/// To add a new display type:
/// 1. Add the value to [DisplayMessageType]
/// 2. Create a widget in `message_components/`
/// 3. Add an entry here
final Map<DisplayMessageType, MessageRenderer> messageRenderers = {
  DisplayMessageType.user: (msg) => UserBubble(message: msg),
  DisplayMessageType.assistant: (msg) => AssistantBubble(message: msg),
  DisplayMessageType.toolBlock: (msg) => msg.isQuestion
      ? QuestionBlock(message: msg)
      : ToolBlock(message: msg),
  DisplayMessageType.error: (msg) => StatusChip(
        message: msg,
        icon: Icons.error_outline_rounded,
        bg: kErrorBg,
        fg: kErrorText,
      ),
  DisplayMessageType.system: (msg) => StatusChip(
        message: msg,
        icon: Icons.info_outline_rounded,
        bg: kBgElevated,
        fg: kSystemText,
      ),
  DisplayMessageType.summary: (msg) => SummaryBubble(message: msg),
  DisplayMessageType.sessionEndAsk: (msg) =>
      SessionEndAskCard(message: msg),
  DisplayMessageType.planModeAsk: (msg) => PlanModeAskCard(message: msg),
  DisplayMessageType.planReviewAsk: (msg) =>
      PlanReviewAskCard(message: msg),
  DisplayMessageType.permissionRequest: (msg) =>
      PermissionRequestCard(message: msg),
  DisplayMessageType.agentGroup: (msg) => AgentGroupBlock(message: msg),
};

/// Dispatches a [DisplayMessage] to its registered renderer widget.
class MessageBubble extends StatelessWidget {
  final DisplayMessage message;

  const MessageBubble({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    final renderer = messageRenderers[message.type];
    assert(renderer != null, 'No renderer for ${message.type}');
    return renderer!(message);
  }
}
