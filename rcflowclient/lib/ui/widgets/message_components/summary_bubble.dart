import 'package:flutter/material.dart';

import '../../../models/ws_messages.dart';
import '../../../theme.dart';

class SummaryBubble extends StatelessWidget {
  final DisplayMessage message;
  const SummaryBubble({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.symmetric(vertical: 6),
      child: Container(
        width: double.infinity,
        padding: EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: context.appColors.summaryBg,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: context.appColors.accentDim),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Padding(
              padding: EdgeInsets.only(top: 2),
              child: Icon(
                Icons.auto_awesome_rounded,
                color: context.appColors.summaryText,
                size: 16,
              ),
            ),
            SizedBox(width: 10),
            Expanded(
              child: Text(
                message.content,
                style: TextStyle(
                  color: context.appColors.summaryText,
                  fontSize: 13,
                  height: 1.45,
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
