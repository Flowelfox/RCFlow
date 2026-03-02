import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../models/worker_config.dart';
import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';

bool get _isDesktop =>
    Platform.isWindows || Platform.isLinux || Platform.isMacOS;

class InputArea extends StatefulWidget {
  const InputArea({super.key});

  @override
  State<InputArea> createState() => _InputAreaState();
}

class _InputAreaState extends State<InputArea> {
  final TextEditingController _controller = TextEditingController();
  final FocusNode _focusNode = FocusNode();
  final LayerLink _layerLink = LayerLink();
  bool _hasText = false;

  // Mention overlay state
  OverlayEntry? _overlayEntry;
  List<String> _suggestions = [];
  int _selectedIndex = 0;
  int? _mentionStart;
  Timer? _debounceTimer;
  bool _showingNoResults = false;

  @override
  void initState() {
    super.initState();
    _controller.addListener(_onTextChanged);
  }

  @override
  void dispose() {
    _debounceTimer?.cancel();
    _removeOverlay();
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  void _onTextChanged() {
    final has = _controller.text.trim().isNotEmpty;
    if (has != _hasText) setState(() => _hasText = has);
    _checkForMention();
  }

  void _checkForMention() {
    final text = _controller.text;
    final selection = _controller.selection;
    if (!selection.isValid || !selection.isCollapsed) {
      _dismissOverlay();
      return;
    }
    final cursor = selection.baseOffset;

    // Walk backwards from cursor to find the nearest unescaped '@'
    int? atPos;
    for (var i = cursor - 1; i >= 0; i--) {
      final ch = text[i];
      if (ch == '@') {
        atPos = i;
        break;
      }
      if (ch == ' ' || ch == '\n') {
        break;
      }
    }

    if (atPos == null) {
      _dismissOverlay();
      return;
    }

    // '@' must be at start of text or preceded by whitespace
    if (atPos > 0 && text[atPos - 1] != ' ' && text[atPos - 1] != '\n') {
      _dismissOverlay();
      return;
    }

    final query = text.substring(atPos + 1, cursor);
    // If query contains a newline, dismiss
    if (query.contains('\n')) {
      _dismissOverlay();
      return;
    }

    _mentionStart = atPos;
    _fetchSuggestions(query);
  }

  void _fetchSuggestions(String query) {
    _debounceTimer?.cancel();
    _debounceTimer = Timer(const Duration(milliseconds: 300), () async {
      if (!mounted) return;
      try {
        final state = context.read<AppState>();
        final pane = context.read<PaneState>();
        final wid = pane.workerId ?? state.defaultWorkerId;
        if (wid == null) {
          _dismissOverlay();
          return;
        }
        final ws = state.wsForWorker(wid);
        final projects = await ws.fetchProjects(query: query);
        if (!mounted) return;
        if (projects.isEmpty) {
          _showNoResults();
          return;
        }
        _showingNoResults = false;
        _suggestions = projects.take(6).toList();
        _selectedIndex = 0;
        _updateOverlay();
      } catch (_) {
        _dismissOverlay();
      }
    });
  }

  void _showNoResults() {
    _showingNoResults = true;
    _suggestions = [];
    _selectedIndex = 0;
    _updateOverlay();
    // Auto-dismiss after a short delay
    Future.delayed(const Duration(seconds: 1), () {
      if (mounted && _showingNoResults) {
        _dismissOverlay();
      }
    });
  }

  void _updateOverlay() {
    if (_suggestions.isEmpty && !_showingNoResults) {
      _removeOverlay();
      return;
    }
    if (_overlayEntry != null) {
      _overlayEntry!.markNeedsBuild();
    } else {
      _overlayEntry = _buildOverlayEntry();
      Overlay.of(context).insert(_overlayEntry!);
    }
  }

  void _dismissOverlay() {
    _mentionStart = null;
    _suggestions = [];
    _selectedIndex = 0;
    _showingNoResults = false;
    _debounceTimer?.cancel();
    _removeOverlay();
  }

  void _removeOverlay() {
    _overlayEntry?.remove();
    _overlayEntry = null;
  }

  bool get _overlayVisible => _overlayEntry != null;

  void _selectSuggestion(String name) {
    if (_mentionStart == null) return;
    final text = _controller.text;
    final cursor = _controller.selection.baseOffset;
    final before = text.substring(0, _mentionStart!);
    final after = text.substring(cursor);
    final insertion = '@$name ';
    _controller.text = '$before$insertion$after';
    _controller.selection = TextSelection.collapsed(
      offset: before.length + insertion.length,
    );
    _dismissOverlay();
  }

  void _moveSelection(int delta) {
    if (_suggestions.isEmpty) return;
    setState(() {
      _selectedIndex = (_selectedIndex + delta) % _suggestions.length;
      if (_selectedIndex < 0) _selectedIndex = _suggestions.length - 1;
    });
    _overlayEntry?.markNeedsBuild();
  }

  void _send() {
    final text = _controller.text.trim();
    if (text.isEmpty) return;
    final pane = context.read<PaneState>();
    context.read<AppState>().setActivePane(pane.paneId);
    pane.sendPrompt(text);
    _controller.clear();
    _focusNode.requestFocus();
  }

  KeyEventResult _handleKeyEvent(FocusNode node, KeyEvent event) {
    if (event is! KeyDownEvent && event is! KeyRepeatEvent) {
      return KeyEventResult.ignored;
    }

    if (_overlayVisible) {
      if (event.logicalKey == LogicalKeyboardKey.escape) {
        _dismissOverlay();
        return KeyEventResult.handled;
      }
      if (event.logicalKey == LogicalKeyboardKey.arrowDown) {
        _moveSelection(1);
        return KeyEventResult.handled;
      }
      if (event.logicalKey == LogicalKeyboardKey.arrowUp) {
        _moveSelection(-1);
        return KeyEventResult.handled;
      }
      if (event.logicalKey == LogicalKeyboardKey.enter ||
          event.logicalKey == LogicalKeyboardKey.tab) {
        if (_suggestions.isNotEmpty) {
          _selectSuggestion(_suggestions[_selectedIndex]);
          return KeyEventResult.handled;
        }
      }
    }

    if (event.logicalKey == LogicalKeyboardKey.enter) {
      final shift = HardwareKeyboard.instance.isShiftPressed;
      if (!shift) {
        _send();
        return KeyEventResult.handled;
      }
    }
    return KeyEventResult.ignored;
  }

  OverlayEntry _buildOverlayEntry() {
    return OverlayEntry(
      builder: (context) {
        final mentionQuery = _mentionStart != null
            ? _controller.text.substring(
                _mentionStart! + 1,
                _controller.selection.isValid
                    ? _controller.selection.baseOffset
                    : _mentionStart! + 1,
              )
            : '';

        Widget content;
        if (_showingNoResults) {
          content = const Padding(
            padding: EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            child: Text(
              'No projects found',
              style: TextStyle(color: kTextMuted, fontSize: 13),
            ),
          );
        } else {
          content = ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 240),
            child: ListView.builder(
              padding: EdgeInsets.zero,
              shrinkWrap: true,
              itemCount: _suggestions.length,
              itemBuilder: (context, index) {
                final name = _suggestions[index];
                final selected = index == _selectedIndex;
                return _MentionItem(
                  name: name,
                  query: mentionQuery,
                  selected: selected,
                  onTap: () => _selectSuggestion(name),
                );
              },
            ),
          );
        }

        return CompositedTransformFollower(
          link: _layerLink,
          showWhenUnlinked: false,
          targetAnchor: Alignment.topLeft,
          followerAnchor: Alignment.bottomLeft,
          offset: const Offset(0, -4),
          child: Material(
            color: Colors.transparent,
            child: Align(
              alignment: Alignment.bottomLeft,
              child: Container(
                width: 280,
                decoration: BoxDecoration(
                  color: kBgElevated,
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: kDivider),
                  boxShadow: const [
                    BoxShadow(
                      color: Color(0x40000000),
                      blurRadius: 12,
                      offset: Offset(0, -4),
                    ),
                  ],
                ),
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(8),
                  child: content,
                ),
              ),
            ),
          ),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final canSend =
        context.select<PaneState, bool>((s) => s.canSendMessage);
    final sessionEnded =
        context.select<PaneState, bool>((s) => s.sessionEnded);
    final sessionPaused =
        context.select<PaneState, bool>((s) => s.sessionPaused);
    final sessionId =
        context.select<PaneState, String?>((s) => s.sessionId);
    final paneWorkerId =
        context.select<PaneState, String?>((s) => s.workerId);
    final bottom = MediaQuery.of(context).viewPadding.bottom;
    final showPauseResume =
        _isDesktop && sessionId != null && !sessionEnded;

    // Worker selector chip for new chats
    final state = context.watch<AppState>();
    final connectedWorkers = state.workerConfigs
        .where((c) => state.getWorker(c.id)?.isConnected == true)
        .toList();
    final showWorkerChip =
        sessionId == null && connectedWorkers.length > 1;
    final selectedWorkerName = _resolveWorkerName(
        paneWorkerId, state.defaultWorkerId, connectedWorkers);

    final String hintText;
    final IconData? prefixIcon;
    if (sessionEnded) {
      hintText = 'Session ended';
      prefixIcon = Icons.lock_rounded;
    } else if (sessionPaused) {
      hintText = 'Send a message to resume...';
      prefixIcon = Icons.pause_rounded;
    } else {
      hintText = 'Message...';
      prefixIcon = null;
    }

    Widget textField = TextField(
      controller: _controller,
      focusNode: _focusNode,
      enabled: canSend,
      style: const TextStyle(color: kTextPrimary, fontSize: 15),
      decoration: InputDecoration(
        hintText: hintText,
        prefixIcon: prefixIcon != null
            ? Icon(prefixIcon, size: 18, color: kTextMuted)
            : null,
        prefixIconConstraints:
            const BoxConstraints(minWidth: 40, minHeight: 0),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 20, vertical: 12),
      ),
      maxLines: _isDesktop ? 8 : 4,
      minLines: 1,
      textInputAction: _isDesktop ? TextInputAction.newline : TextInputAction.send,
      onSubmitted: _isDesktop ? null : (_) => _send(),
    );

    if (_isDesktop) {
      _focusNode.onKeyEvent = _handleKeyEvent;
    }

    return CompositedTransformTarget(
      link: _layerLink,
      child: Container(
        padding: EdgeInsets.fromLTRB(12, 10, 8, 10 + bottom),
        decoration: const BoxDecoration(
          color: kBgSurface,
          border: Border(top: BorderSide(color: kDivider)),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (showWorkerChip)
              Padding(
                padding: const EdgeInsets.only(left: 4, bottom: 6),
                child: _WorkerChip(
                  label: selectedWorkerName ?? 'Select worker',
                  workers: connectedWorkers,
                  onSelected: (id) {
                    context.read<PaneState>().setTargetWorker(id);
                  },
                ),
              ),
            Row(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              width: showPauseResume ? 46 : 0,
              height: 46,
              clipBehavior: Clip.hardEdge,
              decoration: const BoxDecoration(),
              child: Tooltip(
                message:
                    sessionPaused ? 'Resume session' : 'Pause session',
                child: Material(
                  color: kBgElevated,
                  shape: const CircleBorder(),
                  clipBehavior: Clip.antiAlias,
                  child: InkWell(
                    onTap: showPauseResume
                        ? () {
                            final pane = context.read<PaneState>();
                            if (sessionPaused) {
                              pane.resumeSession(sessionId!);
                            } else {
                              pane.pauseSession(sessionId!);
                            }
                          }
                        : null,
                    child: Center(
                      child: Icon(
                        sessionPaused
                            ? Icons.play_arrow_rounded
                            : Icons.pause_rounded,
                        color: sessionPaused
                            ? kAccentLight
                            : kTextSecondary,
                        size: 22,
                      ),
                    ),
                  ),
                ),
              ),
            ),
            AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              width: showPauseResume ? 8 : 0,
            ),
            Expanded(child: textField),
            const SizedBox(width: 8),
            AnimatedContainer(
              duration: const Duration(milliseconds: 200),
              width: 46,
              height: 46,
              child: Material(
                color: _hasText && canSend
                    ? kAccent
                    : kBgElevated,
                shape: const CircleBorder(),
                clipBehavior: Clip.antiAlias,
                child: InkWell(
                  onTap: _hasText && canSend ? _send : null,
                  child: Center(
                    child: Icon(
                      Icons.arrow_upward_rounded,
                      color: _hasText && canSend
                          ? Colors.white
                          : kTextMuted,
                      size: 22,
                    ),
                  ),
                ),
              ),
            ),
          ],
        ),
          ],
        ),
      ),
    );
  }

  String? _resolveWorkerName(String? paneWorkerId, String? defaultWorkerId,
      List<WorkerConfig> connectedWorkers) {
    final id = paneWorkerId ?? defaultWorkerId;
    if (id == null) return null;
    for (final c in connectedWorkers) {
      if (c.id == id) return c.name;
    }
    return connectedWorkers.isNotEmpty ? connectedWorkers.first.name : null;
  }
}

class _WorkerChip extends StatelessWidget {
  final String label;
  final List<WorkerConfig> workers;
  final void Function(String workerId) onSelected;

  const _WorkerChip({
    required this.label,
    required this.workers,
    required this.onSelected,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: () {
        final RenderBox box = context.findRenderObject() as RenderBox;
        final offset = box.localToGlobal(Offset.zero);
        showMenu<String>(
          context: context,
          position: RelativeRect.fromLTRB(
            offset.dx,
            offset.dy - (workers.length * 40 + 8),
            offset.dx + box.size.width,
            offset.dy,
          ),
          color: kBgSurface,
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
          items: workers
              .map((w) => PopupMenuItem<String>(
                    value: w.id,
                    height: 40,
                    child: Text(w.name,
                        style: const TextStyle(
                            color: kTextPrimary, fontSize: 13)),
                  ))
              .toList(),
        ).then((id) {
          if (id != null) onSelected(id);
        });
      },
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: kBgElevated,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: kDivider),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.dns_outlined, size: 14, color: kTextMuted),
            const SizedBox(width: 6),
            Text(label,
                style: const TextStyle(color: kTextSecondary, fontSize: 12)),
            const SizedBox(width: 4),
            const Icon(Icons.arrow_drop_down, size: 16, color: kTextMuted),
          ],
        ),
      ),
    );
  }
}

class _MentionItem extends StatelessWidget {
  final String name;
  final String query;
  final bool selected;
  final VoidCallback onTap;

  const _MentionItem({
    required this.name,
    required this.query,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        color: selected ? kBgOverlay : Colors.transparent,
        child: Row(
          children: [
            const Icon(Icons.folder_rounded, size: 16, color: kTextMuted),
            const SizedBox(width: 8),
            Expanded(child: _buildHighlightedName()),
          ],
        ),
      ),
    );
  }

  Widget _buildHighlightedName() {
    if (query.isEmpty) {
      return Text(
        name,
        style: const TextStyle(color: kTextPrimary, fontSize: 13),
        overflow: TextOverflow.ellipsis,
      );
    }

    final lowerName = name.toLowerCase();
    final lowerQuery = query.toLowerCase();
    final matchIndex = lowerName.indexOf(lowerQuery);

    if (matchIndex < 0) {
      return Text(
        name,
        style: const TextStyle(color: kTextPrimary, fontSize: 13),
        overflow: TextOverflow.ellipsis,
      );
    }

    return Text.rich(
      TextSpan(children: [
        if (matchIndex > 0)
          TextSpan(
            text: name.substring(0, matchIndex),
            style: const TextStyle(color: kTextPrimary, fontSize: 13),
          ),
        TextSpan(
          text: name.substring(matchIndex, matchIndex + query.length),
          style: const TextStyle(color: kAccentLight, fontSize: 13, fontWeight: FontWeight.w600),
        ),
        if (matchIndex + query.length < name.length)
          TextSpan(
            text: name.substring(matchIndex + query.length),
            style: const TextStyle(color: kTextPrimary, fontSize: 13),
          ),
      ]),
      overflow: TextOverflow.ellipsis,
    );
  }
}
