import 'package:flutter/material.dart';

import '../../models/app_notification.dart';
import '../../services/notification_service.dart';
import 'notification_toast.dart';

class NotificationOverlay extends StatefulWidget {
  final NotificationService service;

  const NotificationOverlay({super.key, required this.service});

  @override
  State<NotificationOverlay> createState() => _NotificationOverlayState();
}

class _NotificationOverlayState extends State<NotificationOverlay> {
  final _listKey = GlobalKey<AnimatedListState>();
  List<AppNotification> _current = [];

  @override
  void initState() {
    super.initState();
    _current = List.of(widget.service.notifications);
    widget.service.addListener(_onChanged);
  }

  @override
  void dispose() {
    widget.service.removeListener(_onChanged);
    super.dispose();
  }

  void _onChanged() {
    final next = widget.service.notifications;

    // Find removed items
    for (var i = _current.length - 1; i >= 0; i--) {
      if (!next.any((n) => n.id == _current[i].id)) {
        final removed = _current.removeAt(i);
        _listKey.currentState?.removeItem(
          i,
          (context, animation) => _buildAnimated(removed, animation),
          duration: const Duration(milliseconds: 200),
        );
      }
    }

    // Find added items
    for (var i = 0; i < next.length; i++) {
      if (!_current.any((n) => n.id == next[i].id)) {
        _current.insert(i, next[i]);
        _listKey.currentState?.insertItem(
          i,
          duration: const Duration(milliseconds: 300),
        );
      }
    }
  }

  Widget _buildAnimated(AppNotification notification, Animation<double> animation) {
    final slide = Tween<Offset>(
      begin: const Offset(1.0, 0.0),
      end: Offset.zero,
    ).animate(CurvedAnimation(parent: animation, curve: Curves.easeOutCubic));

    return SlideTransition(
      position: slide,
      child: FadeTransition(
        opacity: animation,
        child: Padding(
          padding: const EdgeInsets.only(bottom: 8),
          child: NotificationToast(
            notification: notification,
            onDismiss: () => widget.service.dismiss(notification.id),
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final isNarrow = MediaQuery.of(context).size.width <= 700;

    return Positioned(
      bottom: 16,
      right: 16,
      left: isNarrow ? 16 : null,
      child: IgnorePointer(
        ignoring: false,
        child: AnimatedList(
          key: _listKey,
          initialItemCount: _current.length,
          shrinkWrap: true,
          reverse: true,
          physics: const NeverScrollableScrollPhysics(),
          itemBuilder: (context, index, animation) {
            if (index >= _current.length) return const SizedBox.shrink();
            return _buildAnimated(_current[index], animation);
          },
        ),
      ),
    );
  }
}
