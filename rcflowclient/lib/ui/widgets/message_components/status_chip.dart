import 'package:flutter/material.dart';

import '../../../models/ws_messages.dart';

class StatusChip extends StatelessWidget {
  final DisplayMessage message;
  final IconData icon;
  final Color bg;
  final Color fg;
  const StatusChip({
    super.key,
    required this.message,
    required this.icon,
    required this.bg,
    required this.fg,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(20),
        ),
        child: Row(
          children: [
            Icon(icon, color: fg, size: 14),
            const SizedBox(width: 6),
            Expanded(
              child: Text(
                message.content,
                style: TextStyle(color: fg, fontSize: 12),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
