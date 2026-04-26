import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/session_info.dart';
import '../../services/worker_connection.dart';
import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';
import '../../tips.dart';
import '../dialogs/worker_edit_dialog.dart';
import '../utils/markdown_copy_menu.dart';
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
  int _lastRevision = 0;
  int _lastMessageCount = 0;
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
      padding: EdgeInsets.symmetric(vertical: 8),
      child: Center(
        child: loading
            ? SizedBox(
                width: 20,
                height: 20,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: context.appColors.toolAccent,
                ),
              )
            : Text(
                '$remaining older message${remaining == 1 ? '' : 's'}',
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 12,
                ),
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

            final rev = pane.revision;
            final contentChanged = rev != _lastRevision;
            if (contentChanged) {
              if (msgs.isEmpty && _lastMessageCount > 0) {
                _tip = getRandomTip();
              }
              _lastRevision = rev;
              _lastMessageCount = msgs.length;
              if (_isStuck) {
                WidgetsBinding.instance.addPostFrameCallback(
                  (_) => _scrollToBottom(),
                );
              } else {
                if (!_hasUnseenMessages) {
                  WidgetsBinding.instance.addPostFrameCallback((_) {
                    if (mounted) setState(() => _hasUnseenMessages = true);
                  });
                }
              }
            }

            // Landing page: no session selected and not ready for new chat
            final noSession = pane.sessionId == null && !pane.readyForNewChat;
            if (msgs.isEmpty && noSession) {
              final connected = context.select<AppState, bool>(
                (s) => s.connected,
              );
              return _withLlmBanner(
                context,
                pane,
                Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      Icons.chat_bubble_outline_rounded,
                      size: 48,
                      color: context.appColors.textMuted.withAlpha(80),
                    ),
                    SizedBox(height: 16),
                    Text(
                      'Welcome to RCFlow',
                      style: TextStyle(
                        color: context.appColors.textPrimary,
                        fontSize: 18,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    SizedBox(height: 6),
                    Text(
                      'Start a new chat or continue a previous one',
                      style: TextStyle(
                        color: context.appColors.textMuted,
                        fontSize: 13,
                      ),
                    ),
                    SizedBox(height: 24),
                    if (connected) ...[
                      FilledButton.icon(
                        onPressed: () => pane.startNewChat(),
                        icon: Icon(Icons.add_rounded, size: 18),
                        label: Text('New Chat'),
                        style: FilledButton.styleFrom(
                          backgroundColor: context.appColors.accent,
                          foregroundColor: Colors.white,
                          padding: const EdgeInsets.symmetric(
                            horizontal: 24,
                            vertical: 12,
                          ),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(12),
                          ),
                        ),
                      ),
                      SizedBox(height: 12),
                      OutlinedButton.icon(
                        onPressed: () => showSessionSheet(context),
                        icon: Icon(Icons.history_rounded, size: 18),
                        label: Text('View Sessions'),
                        style: OutlinedButton.styleFrom(
                          foregroundColor: context.appColors.textSecondary,
                          side: BorderSide(color: context.appColors.divider),
                          padding: EdgeInsets.symmetric(
                            horizontal: 24,
                            vertical: 12,
                          ),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(12),
                          ),
                        ),
                      ),
                    ] else
                      Text(
                        'Connect to get started',
                        style: TextStyle(
                          color: context.appColors.textMuted,
                          fontSize: 13,
                        ),
                      ),
                  ],
                ),
                ),
              );
            }

            if (msgs.isEmpty) {
              return _withLlmBanner(
                context,
                pane,
                Center(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      Icons.terminal_rounded,
                      size: 48,
                      color: context.appColors.textMuted.withAlpha(80),
                    ),
                    SizedBox(height: 12),
                    Text(
                      'Send a message to get started',
                      style: TextStyle(
                        color: context.appColors.textMuted,
                        fontSize: 15,
                      ),
                    ),
                    SizedBox(height: 20),
                    ConstrainedBox(
                      constraints: BoxConstraints(maxWidth: 380),
                      child: Row(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Icon(
                            Icons.lightbulb_outline_rounded,
                            size: 14,
                            color: context.appColors.textMuted,
                          ),
                          SizedBox(width: 6),
                          Flexible(
                            child: Text(
                              _tip,
                              style: TextStyle(
                                color: context.appColors.textMuted,
                                fontSize: 12.5,
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
                ),
              );
            }

            final hasMore = pane.hasMoreMessages;
            final loadingMore = pane.loadingMore;

            return _withLlmBanner(
              context,
              pane,
              SelectionScope(
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
                      return Padding(
                        padding: EdgeInsets.symmetric(vertical: 12),
                        child: Center(
                          child: Text(
                            'Beginning of session',
                            style: TextStyle(
                              color: context.appColors.textMuted,
                              fontSize: 12,
                            ),
                          ),
                        ),
                      );
                    }
                    final msg = msgs[index - 1];
                    // ObjectKey by message identity — DisplayMessage instances
                    // are stable across streaming (the same object grows in
                    // place), so this lets Flutter reuse the existing element
                    // and child state instead of treating each rebuild as a
                    // brand-new list item.
                    return MessageBubble(key: ObjectKey(msg), message: msg);
                  },
                ),
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
                  duration: Duration(milliseconds: 250),
                  child: Container(
                    width: double.infinity,
                    padding: EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                    decoration: BoxDecoration(
                      color: context.appColors.bgElevated,
                      border: Border(
                        top: BorderSide(color: context.appColors.divider),
                      ),
                    ),
                    child: Row(
                      children: [
                        SizedBox(
                          width: 16,
                          height: 16,
                          child: CircularProgressIndicator(
                            strokeWidth: 2,
                            color: context.appColors.toolAccent,
                          ),
                        ),
                        SizedBox(width: 12),
                        Text(
                          'Reconnecting...',
                          style: TextStyle(
                            color: context.appColors.textSecondary,
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
                backgroundColor: context.appColors.bgOverlay,
                elevation: 4,
                child: Stack(
                  clipBehavior: Clip.none,
                  children: [
                    Center(
                      child: Icon(
                        Icons.keyboard_arrow_down_rounded,
                        color: context.appColors.textSecondary,
                        size: 22,
                      ),
                    ),
                    if (_hasUnseenMessages)
                      Positioned(
                        top: -2,
                        right: -2,
                        child: Container(
                          width: 8,
                          height: 8,
                          decoration: BoxDecoration(
                            color: context.appColors.accent,
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

  /// Wrap an empty-state layout so the LLM-missing banner pins to the top of
  /// the pane while the existing content stays vertically centred in the
  /// remaining space. Returns [child] unchanged when no banner applies.
  Widget _withLlmBanner(BuildContext context, PaneState pane, Widget child) {
    final workerId = pane.workerId;
    if (workerId == null) return child;
    final worker = context.read<AppState>().getWorker(workerId);
    if (worker == null) return child;
    return ListenableBuilder(
      listenable: worker,
      builder: (context, _) {
        if (!worker.isConnected) return child;
        final banners = <Widget>[];
        if (!worker.hasLlmConfigured) {
          banners.add(_LlmNotConfiguredBanner(worker: worker));
        }
        final agentBanner = _buildAgentBanner(context, pane, worker);
        if (agentBanner != null) banners.add(agentBanner);
        if (banners.isEmpty) return child;
        return Column(
          children: [
            ...banners,
            Expanded(child: child),
          ],
        );
      },
    );
  }

  /// Build the agent-not-configured banner for [pane] when an agent badge is
  /// active and that agent has an unresolved auth issue. Active agent comes
  /// from the session's ``agent_type`` (live or archived sessions) or the
  /// pre-session ``selectedToolMention`` chip (new-chat panes).
  Widget? _buildAgentBanner(
    BuildContext context,
    PaneState pane,
    WorkerConnection worker,
  ) {
    String? internal;
    final sessionId = pane.sessionId;
    if (sessionId != null) {
      final session = context.read<AppState>().sessions
          .cast<SessionInfo?>()
          .firstWhere((s) => s!.sessionId == sessionId, orElse: () => null);
      final agentType = session?.agentType;
      if (agentType != null) {
        internal = AppState.agentInternalName(agentType) ?? agentType;
      }
    }
    if (internal == null) {
      final mention = pane.selectedToolMention;
      if (mention != null) {
        internal = AppState.agentInternalName(mention);
      }
    }
    if (internal == null) return null;
    final ready = worker.agentReady[internal];
    if (ready == null || ready) return null;
    final issue = worker.agentIssues[internal] ??
        '$internal has no API key or login configured.';
    return _AgentNotConfiguredBanner(
      worker: worker,
      agentInternal: internal,
      issue: issue,
    );
  }
}

class _LlmNotConfiguredBanner extends StatelessWidget {
  final WorkerConnection worker;

  const _LlmNotConfiguredBanner({required this.worker});

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF7CC),
        border: Border(
          bottom: BorderSide(color: colors.divider, width: 1),
        ),
      ),
      child: Row(
        children: [
          const Icon(
            Icons.warning_amber_rounded,
            size: 18,
            color: Color(0xFF8A6D1A),
          ),
          const SizedBox(width: 8),
          const Expanded(
            child: Text(
              'LLM key is not configured.',
              style: TextStyle(
                color: Color(0xFF5C4A0E),
                fontSize: 13,
                fontWeight: FontWeight.w500,
              ),
            ),
          ),
          const SizedBox(width: 8),
          TextButton(
            onPressed: () => showWorkerEditDialog(
              context,
              existing: worker.config,
              worker: worker,
              initialTabIndex: 1,
              initialServerSection: 'LLM',
            ),
            style: TextButton.styleFrom(
              foregroundColor: const Color(0xFF8A6D1A),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              minimumSize: Size.zero,
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: const Text(
              'Configure',
              style: TextStyle(fontWeight: FontWeight.w600),
            ),
          ),
        ],
      ),
    );
  }
}

/// Banner shown above a pane when the active coding-agent badge points at a
/// CLI that has no API key or login configured on this worker. Carries a
/// "Configure" button that opens the worker edit dialog at the matching tool
/// section so the user can fix it without leaving the chat.
class _AgentNotConfiguredBanner extends StatelessWidget {
  final WorkerConnection worker;
  final String agentInternal;
  final String issue;

  const _AgentNotConfiguredBanner({
    required this.worker,
    required this.agentInternal,
    required this.issue,
  });

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF7CC),
        border: Border(
          bottom: BorderSide(color: colors.divider, width: 1),
        ),
      ),
      child: Row(
        children: [
          const Icon(
            Icons.warning_amber_rounded,
            size: 18,
            color: Color(0xFF8A6D1A),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              issue,
              style: const TextStyle(
                color: Color(0xFF5C4A0E),
                fontSize: 13,
                fontWeight: FontWeight.w500,
              ),
            ),
          ),
          const SizedBox(width: 8),
          TextButton(
            onPressed: () => showWorkerEditDialog(
              context,
              existing: worker.config,
              worker: worker,
              initialTabIndex: 1,
              initialServerSection: agentInternal,
            ),
            style: TextButton.styleFrom(
              foregroundColor: const Color(0xFF8A6D1A),
              padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
              minimumSize: Size.zero,
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
            child: const Text(
              'Configure',
              style: TextStyle(fontWeight: FontWeight.w600),
            ),
          ),
        ],
      ),
    );
  }
}
