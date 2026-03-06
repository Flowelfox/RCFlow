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

/// Dispatches a [DisplayMessage] to its registered renderer widget.
class MessageBubble extends StatelessWidget {
  final DisplayMessage message;

  const MessageBubble({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    switch (message.type) {
      case DisplayMessageType.user:
        return UserBubble(message: message);
      case DisplayMessageType.assistant:
        return AssistantBubble(message: message);
      case DisplayMessageType.toolBlock:
        return message.isQuestion
            ? QuestionBlock(message: message)
            : ToolBlock(message: message);
      case DisplayMessageType.error:
        return StatusChip(
          message: message,
          icon: Icons.error_outline_rounded,
          bg: context.appColors.errorBg,
          fg: context.appColors.errorText,
        );
      case DisplayMessageType.system:
        return StatusChip(
          message: message,
          icon: Icons.info_outline_rounded,
          bg: context.appColors.bgElevated,
          fg: context.appColors.systemText,
        );
      case DisplayMessageType.summary:
        return SummaryBubble(message: message);
      case DisplayMessageType.sessionEndAsk:
        return SessionEndAskCard(message: message);
      case DisplayMessageType.planModeAsk:
        return PlanModeAskCard(message: message);
      case DisplayMessageType.planReviewAsk:
        return PlanReviewAskCard(message: message);
      case DisplayMessageType.permissionRequest:
        return PermissionRequestCard(message: message);
      case DisplayMessageType.agentGroup:
        return AgentGroupBlock(message: message);
    }
  }
}
