import 'package:flutter/material.dart';

import '../../../models/ws_messages.dart';
import '../../../theme.dart';

class SummaryBubble extends StatelessWidget {
  final DisplayMessage message;
  const SummaryBubble({super.key, required this.message});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: kSummaryBg,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: kAccentDim),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Padding(
              padding: EdgeInsets.only(top: 2),
              child: Icon(Icons.auto_awesome_rounded,
                  color: kSummaryText, size: 16),
            ),
            const SizedBox(width: 10),
            Expanded(
              child: Text(
                message.content,
                style: const TextStyle(
                  color: kSummaryText,
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
