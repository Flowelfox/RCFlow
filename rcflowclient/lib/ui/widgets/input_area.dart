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

enum _MentionType { project, tool }

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
  List<String> _projectSuggestions = [];
  List<Map<String, String>> _toolSuggestions = [];
  int _selectedIndex = 0;
  int? _mentionStart;
  _MentionType? _mentionType;
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

    // Walk backwards from cursor to find the nearest unescaped '@' or '#'
    int? triggerPos;
    String? triggerChar;
    for (var i = cursor - 1; i >= 0; i--) {
      final ch = text[i];
      if (ch == '@' || ch == '#') {
        triggerPos = i;
        triggerChar = ch;
        break;
      }
      if (ch == ' ' || ch == '\n') break;
    }

    if (triggerPos == null || triggerChar == null) {
      _dismissOverlay();
      return;
    }

    // Trigger char must be at start of text or preceded by whitespace
    if (triggerPos > 0 &&
        text[triggerPos - 1] != ' ' &&
        text[triggerPos - 1] != '\n') {
      _dismissOverlay();
      return;
    }

    final query = text.substring(triggerPos + 1, cursor);
    if (query.contains('\n')) {
      _dismissOverlay();
      return;
    }

    _mentionStart = triggerPos;
    _mentionType =
        triggerChar == '@' ? _MentionType.project : _MentionType.tool;
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

        if (_mentionType == _MentionType.project) {
          final projects = await ws.fetchProjects(query: query);
          if (!mounted) return;
          if (projects.isEmpty) {
            _showNoResults();
            return;
          }
          _showingNoResults = false;
          _projectSuggestions = projects.take(6).toList();
          _toolSuggestions = [];
        } else {
          final tools = await ws.fetchTools(query: query);
          if (!mounted) return;
          if (tools.isEmpty) {
            _showNoResults();
            return;
          }
          _showingNoResults = false;
          _toolSuggestions = tools.take(6).toList();
          _projectSuggestions = [];
        }

        _selectedIndex = 0;
        _updateOverlay();
      } catch (_) {
        _dismissOverlay();
      }
    });
  }

  void _showNoResults() {
    _showingNoResults = true;
    _projectSuggestions = [];
    _toolSuggestions = [];
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
    if (_projectSuggestions.isEmpty &&
        _toolSuggestions.isEmpty &&
        !_showingNoResults) {
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
    _mentionType = null;
    _projectSuggestions = [];
    _toolSuggestions = [];
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

  int get _suggestionCount {
    if (_mentionType == _MentionType.tool) return _toolSuggestions.length;
    return _projectSuggestions.length;
  }

  void _selectSuggestion(String name) {
    if (_mentionStart == null || _mentionType == null) return;
    final text = _controller.text;
    final cursor = _controller.selection.baseOffset;
    final before = text.substring(0, _mentionStart!);
    final after = text.substring(cursor);
    final prefix = _mentionType == _MentionType.project ? '@' : '#';
    final insertion = '$prefix$name ';
    _controller.text = '$before$insertion$after';
    _controller.selection = TextSelection.collapsed(
      offset: before.length + insertion.length,
    );
    _dismissOverlay();
  }

  void _moveSelection(int delta) {
    final count = _suggestionCount;
    if (count == 0) return;
    setState(() {
      _selectedIndex = (_selectedIndex + delta) % count;
      if (_selectedIndex < 0) _selectedIndex = count - 1;
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
        if (_suggestionCount > 0) {
          final name = _mentionType == _MentionType.tool
              ? _toolSuggestions[_selectedIndex]['name']!
              : _projectSuggestions[_selectedIndex];
          _selectSuggestion(name);
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
          final label = _mentionType == _MentionType.tool
              ? 'No tools found'
              : 'No projects found';
          content = Padding(
            padding: EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            child: Text(
              label,
              style: TextStyle(color: context.appColors.textMuted, fontSize: 13),
            ),
          );
        } else if (_mentionType == _MentionType.tool &&
            _toolSuggestions.isNotEmpty) {
          content = ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 240),
            child: ListView.builder(
              padding: EdgeInsets.zero,
              shrinkWrap: true,
              itemCount: _toolSuggestions.length,
              itemBuilder: (context, index) {
                final tool = _toolSuggestions[index];
                final selected = index == _selectedIndex;
                return _ToolMentionItem(
                  name: tool['name']!,
                  description: tool['description']!,
                  query: mentionQuery,
                  selected: selected,
                  onTap: () => _selectSuggestion(tool['name']!),
                );
              },
            ),
          );
        } else {
          content = ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 240),
            child: ListView.builder(
              padding: EdgeInsets.zero,
              shrinkWrap: true,
              itemCount: _projectSuggestions.length,
              itemBuilder: (context, index) {
                final name = _projectSuggestions[index];
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

        final overlayWidth =
            _mentionType == _MentionType.tool ? 320.0 : 280.0;

        return CompositedTransformFollower(
          link: _layerLink,
          showWhenUnlinked: false,
          targetAnchor: Alignment.topLeft,
          followerAnchor: Alignment.bottomLeft,
          offset: Offset(0, -4),
          child: Material(
            color: Colors.transparent,
            child: Align(
              alignment: Alignment.bottomLeft,
              child: Container(
                width: overlayWidth,
                decoration: BoxDecoration(
                  color: context.appColors.bgElevated,
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: context.appColors.divider),
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
      style: TextStyle(color: context.appColors.textPrimary, fontSize: 15),
      decoration: InputDecoration(
        hintText: hintText,
        prefixIcon: prefixIcon != null
            ? Icon(prefixIcon, size: 18, color: context.appColors.textMuted)
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
        decoration: BoxDecoration(
          color: context.appColors.bgSurface,
          border: Border(top: BorderSide(color: context.appColors.divider)),
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
              duration: Duration(milliseconds: 200),
              width: showPauseResume ? 46 : 0,
              height: 46,
              clipBehavior: Clip.hardEdge,
              decoration: BoxDecoration(),
              child: Tooltip(
                message:
                    sessionPaused ? 'Resume session' : 'Pause session',
                child: Material(
                  color: context.appColors.bgElevated,
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
                            ? context.appColors.accentLight
                            : context.appColors.textSecondary,
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
            SizedBox(width: 8),
            AnimatedContainer(
              duration: Duration(milliseconds: 200),
              width: 46,
              height: 46,
              child: Material(
                color: _hasText && canSend
                    ? context.appColors.accent
                    : context.appColors.bgElevated,
                shape: CircleBorder(),
                clipBehavior: Clip.antiAlias,
                child: InkWell(
                  onTap: _hasText && canSend ? _send : null,
                  child: Center(
                    child: Icon(
                      Icons.arrow_upward_rounded,
                      color: _hasText && canSend
                          ? Colors.white
                          : context.appColors.textMuted,
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
          color: context.appColors.bgSurface,
          shape:
              RoundedRectangleBorder(borderRadius: BorderRadius.circular(10)),
          items: workers
              .map((w) => PopupMenuItem<String>(
                    value: w.id,
                    height: 40,
                    child: Text(w.name,
                        style: TextStyle(
                            color: context.appColors.textPrimary, fontSize: 13)),
                  ))
              .toList(),
        ).then((id) {
          if (id != null) onSelected(id);
        });
      },
      child: Container(
        padding: EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: context.appColors.bgElevated,
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: context.appColors.divider),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.dns_outlined, size: 14, color: context.appColors.textMuted),
            SizedBox(width: 6),
            Text(label,
                style: TextStyle(color: context.appColors.textSecondary, fontSize: 12)),
            SizedBox(width: 4),
            Icon(Icons.arrow_drop_down, size: 16, color: context.appColors.textMuted),
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
        padding: EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        color: selected ? context.appColors.bgOverlay : Colors.transparent,
        child: Row(
          children: [
            Icon(Icons.folder_rounded, size: 16, color: context.appColors.textMuted),
            const SizedBox(width: 8),
            Expanded(child: _buildHighlightedName(context)),
          ],
        ),
      ),
    );
  }

  Widget _buildHighlightedName(BuildContext context) {
    if (query.isEmpty) {
      return Text(
        name,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
        overflow: TextOverflow.ellipsis,
      );
    }

    final lowerName = name.toLowerCase();
    final lowerQuery = query.toLowerCase();
    final matchIndex = lowerName.indexOf(lowerQuery);

    if (matchIndex < 0) {
      return Text(
        name,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
        overflow: TextOverflow.ellipsis,
      );
    }

    return Text.rich(
      TextSpan(children: [
        if (matchIndex > 0)
          TextSpan(
            text: name.substring(0, matchIndex),
            style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
          ),
        TextSpan(
          text: name.substring(matchIndex, matchIndex + query.length),
          style: TextStyle(color: context.appColors.accentLight, fontSize: 13, fontWeight: FontWeight.w600),
        ),
        if (matchIndex + query.length < name.length)
          TextSpan(
            text: name.substring(matchIndex + query.length),
            style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
          ),
      ]),
      overflow: TextOverflow.ellipsis,
    );
  }
}

class _ToolMentionItem extends StatelessWidget {
  final String name;
  final String description;
  final String query;
  final bool selected;
  final VoidCallback onTap;

  const _ToolMentionItem({
    required this.name,
    required this.description,
    required this.query,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        color: selected ? context.appColors.bgOverlay : Colors.transparent,
        child: Row(
          children: [
            Icon(Icons.build_rounded, size: 16, color: context.appColors.textMuted),
            SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _buildHighlightedName(context),
                  Text(
                    description,
                    style: TextStyle(color: context.appColors.textMuted, fontSize: 11),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildHighlightedName(BuildContext context) {
    if (query.isEmpty) {
      return Text(
        name,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
        overflow: TextOverflow.ellipsis,
      );
    }

    final lowerName = name.toLowerCase();
    final lowerQuery = query.toLowerCase();
    final matchIndex = lowerName.indexOf(lowerQuery);

    if (matchIndex < 0) {
      return Text(
        name,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
        overflow: TextOverflow.ellipsis,
      );
    }

    return Text.rich(
      TextSpan(children: [
        if (matchIndex > 0)
          TextSpan(
            text: name.substring(0, matchIndex),
            style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
          ),
        TextSpan(
          text: name.substring(matchIndex, matchIndex + query.length),
          style: TextStyle(
              color: context.appColors.accentLight, fontSize: 13, fontWeight: FontWeight.w600),
        ),
        if (matchIndex + query.length < name.length)
          TextSpan(
            text: name.substring(matchIndex + query.length),
            style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
          ),
      ]),
      overflow: TextOverflow.ellipsis,
    );
  }
}
