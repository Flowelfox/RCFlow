import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';
import '../../tips.dart';
import 'message_bubble.dart';
import 'session_panel.dart';

class OutputDisplay extends StatefulWidget {
  const OutputDisplay({super.key});

  @override
  State<OutputDisplay> createState() => _OutputDisplayState();
}

class _OutputDisplayState extends State<OutputDisplay> {
  final ScrollController _scrollController = ScrollController();
  String _tip = getRandomTip();
  int _lastMessageCount = 0;
  String _lastContent = '';
  Timer? _loadMoreDebounce;

  /// True when auto-scroll is active (user is at/near the bottom).
  bool _isStuck = true;

  /// True when new content arrived while the user was unstuck.
  bool _hasUnseenMessages = false;

  /// Guards against re-sticking during load-more scroll restoration.
  bool _restoringScroll = false;

  @override
  void initState() {
    super.initState();
    _scrollController.addListener(_onScroll);
  }

  @override
  void dispose() {
    _loadMoreDebounce?.cancel();
    _scrollController.dispose();
    super.dispose();
  }

  void _onScroll() {
    if (!_scrollController.hasClients) return;
    if (_restoringScroll) return;
    final pos = _scrollController.position;

    final atBottom = pos.pixels >= pos.maxScrollExtent - 80;

    if (atBottom && !_isStuck) {
      setState(() {
        _isStuck = true;
        _hasUnseenMessages = false;
      });
    } else if (!atBottom && _isStuck) {
      setState(() => _isStuck = false);
    }

    // Scroll-to-top: trigger loading older messages
    if (pos.pixels <= 50) {
      _loadMoreDebounce?.cancel();
      _loadMoreDebounce = Timer(const Duration(milliseconds: 200), () {
        final pane = context.read<PaneState>();
        if (pane.hasMoreMessages && !pane.loadingMore) {
          _loadMoreMessages(pane);
        }
      });
    }
  }

  Future<void> _loadMoreMessages(PaneState pane) async {
    if (!_scrollController.hasClients) return;
    final oldMaxExtent = _scrollController.position.maxScrollExtent;
    final oldPixels = _scrollController.position.pixels;

    await pane.loadOlderMessages();

    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!_scrollController.hasClients) return;
      final newMaxExtent = _scrollController.position.maxScrollExtent;
      _restoringScroll = true;
      _scrollController.jumpTo(oldPixels + (newMaxExtent - oldMaxExtent));
      _restoringScroll = false;
    });
  }

  void _scrollToBottom({bool animate = false}) {
    if (!_scrollController.hasClients) return;
    if (!_isStuck || _hasUnseenMessages) {
      setState(() {
        _isStuck = true;
        _hasUnseenMessages = false;
      });
    }
    if (animate) {
      _scrollController.animateTo(
        _scrollController.position.maxScrollExtent,
        duration: const Duration(milliseconds: 250),
        curve: Curves.easeOut,
      );
    } else {
      _scrollController.jumpTo(_scrollController.position.maxScrollExtent);
    }
  }

  Widget _buildLoadMoreIndicator({
    required bool loading,
    required int remaining,
  }) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Center(
        child: loading
            ? const SizedBox(
                width: 20,
                height: 20,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: kToolAccent,
                ),
              )
            : Text(
                '$remaining older message${remaining == 1 ? '' : 's'}',
                style: const TextStyle(color: kTextMuted, fontSize: 12),
              ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Stack(
      children: [
        Consumer<PaneState>(
          builder: (context, pane, _) {
            final msgs = pane.messages;

            final currentContent = msgs.isNotEmpty ? msgs.last.content : '';
            final contentChanged = msgs.length != _lastMessageCount ||
                currentContent != _lastContent;
            if (contentChanged) {
              if (msgs.isEmpty && _lastMessageCount > 0) {
                _tip = getRandomTip();
              }
              _lastMessageCount = msgs.length;
              _lastContent = currentContent;
              if (_isStuck) {
                WidgetsBinding.instance
                    .addPostFrameCallback((_) => _scrollToBottom());
              } else {
                if (!_hasUnseenMessages) {
                  WidgetsBinding.instance.addPostFrameCallback((_) {
                    if (mounted) setState(() => _hasUnseenMessages = true);
                  });
                }
              }
            }

            // Landing page: no session selected and not ready for new chat
            final noSession =
                pane.sessionId == null && !pane.readyForNewChat;
            if (msgs.isEmpty && noSession) {
              final connected = context.select<AppState, bool>((s) => s.connected);
              return Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.chat_bubble_outline_rounded,
                        size: 48, color: kTextMuted.withAlpha(80)),
                    const SizedBox(height: 16),
                    const Text('Welcome to RCFlow',
                        style: TextStyle(
                          color: kTextPrimary,
                          fontSize: 18,
                          fontWeight: FontWeight.w600,
                        )),
                    const SizedBox(height: 6),
                    const Text('Start a new chat or continue a previous one',
                        style: TextStyle(color: kTextMuted, fontSize: 13)),
                    const SizedBox(height: 24),
                    if (connected) ...[
                      FilledButton.icon(
                        onPressed: () => pane.startNewChat(),
                        icon: const Icon(Icons.add_rounded, size: 18),
                        label: const Text('New Chat'),
                        style: FilledButton.styleFrom(
                          backgroundColor: kAccent,
                          foregroundColor: Colors.white,
                          padding: const EdgeInsets.symmetric(
                              horizontal: 24, vertical: 12),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(12),
                          ),
                        ),
                      ),
                      const SizedBox(height: 12),
                      OutlinedButton.icon(
                        onPressed: () => showSessionSheet(context),
                        icon: const Icon(Icons.history_rounded, size: 18),
                        label: const Text('View Sessions'),
                        style: OutlinedButton.styleFrom(
                          foregroundColor: kTextSecondary,
                          side: const BorderSide(color: kDivider),
                          padding: const EdgeInsets.symmetric(
                              horizontal: 24, vertical: 12),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(12),
                          ),
                        ),
                      ),
                    ] else
                      const Text('Connect to get started',
                          style: TextStyle(color: kTextMuted, fontSize: 13)),
                  ],
                ),
              );
            }

            if (msgs.isEmpty) {
              return Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.terminal_rounded,
                        size: 48, color: kTextMuted.withAlpha(80)),
                    const SizedBox(height: 12),
                    const Text('Send a message to get started',
                        style: TextStyle(color: kTextMuted, fontSize: 15)),
                    const SizedBox(height: 20),
                    ConstrainedBox(
                      constraints: const BoxConstraints(maxWidth: 380),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          const Icon(Icons.lightbulb_outline_rounded,
                              size: 14, color: kTextMuted),
                          const SizedBox(width: 6),
                          Flexible(
                            child: Text(
                              _tip,
                              style: const TextStyle(
                                  color: kTextMuted, fontSize: 12.5),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
              );
            }

            final hasMore = pane.hasMoreMessages;
            final loadingMore = pane.loadingMore;

            return SelectionArea(
              child: ListView.builder(
                controller: _scrollController,
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
                itemCount: msgs.length + 1,
                itemBuilder: (context, index) {
                  if (index == 0) {
                    if (hasMore) {
                      return _buildLoadMoreIndicator(
                        loading: loadingMore,
                        remaining: pane.totalMessageCount - msgs.length,
                      );
                    }
                    return const Padding(
                      padding: EdgeInsets.symmetric(vertical: 12),
                      child: Center(
                        child: Text(
                          'Beginning of session',
                          style: TextStyle(color: kTextMuted, fontSize: 12),
                        ),
                      ),
                    );
                  }
                  return MessageBubble(message: msgs[index - 1]);
                },
              ),
            );
          },
        ),
        // Reconnecting banner (any worker reconnecting)
        Positioned(
          left: 0,
          right: 0,
          bottom: 0,
          child: Selector<AppState, bool>(
            selector: (_, s) => s.connecting && !s.connected,
            builder: (context, isReconnecting, _) {
              return AnimatedSlide(
                offset: isReconnecting ? Offset.zero : const Offset(0, 1),
                duration: const Duration(milliseconds: 250),
                curve: Curves.easeOut,
                child: AnimatedOpacity(
                  opacity: isReconnecting ? 1.0 : 0.0,
                  duration: const Duration(milliseconds: 250),
                  child: Container(
                    width: double.infinity,
                    padding: const EdgeInsets.symmetric(
                        horizontal: 16, vertical: 10),
                    decoration: const BoxDecoration(
                      color: kBgElevated,
                      border: Border(top: BorderSide(color: kDivider)),
                    ),
                    child: const Row(
                      children: [
                        SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: kToolAccent,
                          ),
                        ),
                        SizedBox(width: 12),
                        Text(
                          'Reconnecting...',
                          style: TextStyle(
                            color: kTextSecondary,
                            fontSize: 13,
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              );
            },
          ),
        ),
        // Scroll-to-bottom FAB with unseen indicator
        Positioned(
          right: 16,
          bottom: 8,
          child: AnimatedScale(
            scale: _isStuck ? 0.0 : 1.0,
            duration: const Duration(milliseconds: 200),
            curve: Curves.easeOut,
            child: SizedBox(
              width: 36,
              height: 36,
              child: FloatingActionButton.small(
                onPressed: () {
                  _scrollToBottom(animate: true);
                  setState(() {
                    _isStuck = true;
                    _hasUnseenMessages = false;
                  });
                },
                backgroundColor: kBgOverlay,
                elevation: 4,
                child: Stack(
                  clipBehavior: Clip.none,
                  children: [
                    const Center(
                      child: Icon(Icons.keyboard_arrow_down_rounded,
                          color: kTextSecondary, size: 22),
                    ),
                    if (_hasUnseenMessages)
                      Positioned(
                        top: -2,
                        right: -2,
                        child: Container(
                          width: 8,
                          height: 8,
                          decoration: const BoxDecoration(
                            color: kAccent,
                            shape: BoxShape.circle,
                          ),
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }
}
