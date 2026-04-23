import 'dart:async';
import 'dart:io' show Platform;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../models/subprocess_info.dart';
import '../../models/worker_config.dart';
import '../../services/keyboard_state_reconciler.dart';
import '../../state/app_state.dart';
import '../../state/input_area_view_model.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';
import '../../tips.dart';
import '../badges/badge_chip.dart';
import 'create_worktree_dialog.dart';
import 'session_identity_bar.dart' show CavemanPreviewBadge, WorkerBadge;

bool get _isDesktop =>
    Platform.isWindows || Platform.isLinux || Platform.isMacOS;


enum _MentionType { project, tool, file, slash }

class InputArea extends StatefulWidget {
  const InputArea({super.key});

  @override
  State<InputArea> createState() => _InputAreaState();
}

class _InputAreaState extends State<InputArea> {
  late InputAreaViewModel _vm;
  final TextEditingController _controller = TextEditingController();
  final FocusNode _focusNode = FocusNode();
  final LayerLink _layerLink = LayerLink();
  bool _hasText = false;

  // Tracks the active pane so the worktree cache can be invalidated when the
  // user switches to a different session pane (the widget may be reused).
  String? _lastPaneId;

  Future<void> _fetchWorktrees(
    String projectPath,
    String workerId, {
    bool force = false,
  }) => _vm.fetchWorktrees(projectPath, workerId, force: force);

  /// Set or clear the worktree for an active session via the server API.
  Future<void> _setSessionWorktree(String sessionId, String? path) async {
    if (!mounted) return;
    final pane = context.read<PaneState>();
    final workerId = pane.workerId ?? context.read<AppState>().defaultWorkerId;
    if (workerId == null) return;
    final ws = context.read<AppState>().wsForWorker(workerId);
    try {
      await ws.setSessionWorktree(sessionId, path);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text('Failed to set worktree: $e')));
      }
    }
  }

  /// Create a new worktree via the REST API and auto-select it.
  ///
  /// For new sessions the path is stored in [PaneState.pendingWorktreePath].
  /// For active sessions it is pushed to the server via PATCH.
  Future<void> _createAndSelectWorktree({
    required String projectPath,
    required String workerId,
    String? sessionId,
  }) async {
    final params = await showCreateWorktreeDialog(context);
    if (params == null || !mounted) return;
    try {
      final ws = context.read<AppState>().wsForWorker(workerId);
      final result = await ws.createWorktree(
        branch: params.branch,
        repoPath: projectPath,
        base: params.base,
      );
      if (!mounted) return;
      final newPath = (result['worktree'] as Map<String, dynamic>?)?['path'] as String?;
      if (newPath != null) {
        if (sessionId != null) {
          await ws.setSessionWorktree(sessionId, newPath);
        } else {
          context.read<PaneState>().setPendingWorktreePath(newPath);
        }
      }
      // Refresh the cached worktree list so the dropdown shows the new entry.
      await _fetchWorktrees(projectPath, workerId, force: true);
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Create worktree failed: $e')),
        );
      }
    }
  }

  // Mention overlay state
  OverlayEntry? _overlayEntry;
  // Each entry has 'name' and 'path' keys from the server's project list.
  List<Map<String, String>> _projectSuggestions = [];
  List<Map<String, String>> _toolSuggestions = [];
  List<Map<String, String>> _fileSuggestions = [];
  List<Map<String, String>> _slashSuggestions = [];
  int _selectedIndex = 0;
  int? _mentionStart;
  _MentionType? _mentionType;
  Timer? _debounceTimer;
  // Separate timer for draft persistence — longer interval than the mention
  // suggestion debounce to avoid excessive network calls on every keypress.
  Timer? _draftTimer;
  // Pre-captured PaneState reference — safe to use inside timer callbacks
  // because it is captured once in initState while the element is active,
  // avoiding the context.read-in-timer anti-pattern.
  late final PaneState _pane;
  bool _showingNoResults = false;

  /// Saved reference to the AppState's focus request notifier so we can safely
  /// remove the listener in [dispose] without accessing [context].
  late final Listenable _focusRequestNotifier;
  late final ValueNotifier<int> _pasteRequestNotifier;
  late final ValueNotifier<int> _externalPasteNotifier;

  @override
  void initState() {
    super.initState();
    _vm = InputAreaViewModel(context.read<AppState>());
    _vm.addListener(_onVmChanged);
    // Capture PaneState once while the element is active. All timer callbacks
    // reference _pane directly instead of calling context.read.
    _pane = context.read<PaneState>();
    _pane.registerDraftProvider(() => _controller.text);
    _controller.addListener(_onTextChanged);
    final appState = context.read<AppState>();
    _focusRequestNotifier = appState.inputFocusRequest;
    _focusRequestNotifier.addListener(_onFocusRequest);
    _pasteRequestNotifier = appState.pasteToInputRequest;
    _pasteRequestNotifier.addListener(_onPasteRequest);
    _externalPasteNotifier = appState.externalPasteRequest;
    _externalPasteNotifier.addListener(_onExternalPaste);
    _focusNode.addListener(_onFocusChanged);
    // Check for pending input text on next frame (e.g. from "Start Session
    // from Task" which pre-fills the input area, or from draft restoration).
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _consumePendingInput();
    });
  }

  /// Reconciles modifier state when the input field gains focus. Tools
  /// like Wispr Flow grab modifier keys via global hooks and never
  /// propagate the release to RCFlow, leaving HardwareKeyboard convinced
  /// Ctrl/Alt/Shift are still held. Reconciler only clears on actual
  /// drift vs OS — won't break a user genuinely holding Ctrl.
  void _onFocusChanged() {
    if (!_focusNode.hasFocus) return;
    KeyboardStateReconciler.reconcile();
  }

  void _onVmChanged() => setState(() {});

  void _onFocusRequest() {
    _focusNode.requestFocus();
  }

  /// Inserts text at the cursor (or replaces selection). Shared by both the
  /// Ctrl+V async clipboard read and the external clipboard sniffer.
  void _insertAtCursor(String text) {
    final current = _controller.text;
    final sel = _controller.selection;
    final start = sel.isValid ? sel.start : current.length;
    final end = sel.isValid ? sel.end : current.length;
    _controller.value = TextEditingValue(
      text: current.substring(0, start) + text + current.substring(end),
      selection: TextSelection.collapsed(offset: start + text.length),
    );
  }

  /// Inserts text captured by the Windows clipboard sniffer (Wispr Flow path).
  /// Text payload is delivered already-snapshotted in AppState so we don't
  /// race the dictation tool restoring the prior clipboard. Reconciles
  /// modifier state after insertion because Wispr's activation hotkey can
  /// leave Ctrl "stuck" in HardwareKeyboard — the OS has already released
  /// it by the time the text arrives here.
  ///
  /// Gated on the input field actually having focus: AppState already
  /// requires foreground + non-own clipboard, but a background clipboard
  /// manager could still match those conditions. Requiring real focus on
  /// the input ensures the paste only goes where the user is typing.
  void _onExternalPaste() {
    if (!mounted) return;
    if (!_focusNode.hasFocus) return;
    final appState = context.read<AppState>();
    if (appState.activePaneId != _pane.paneId) return;
    final text = appState.externalPasteText;
    if (text == null || text.isEmpty) return;
    _insertAtCursor(text);
    KeyboardStateReconciler.reconcile();
  }

  /// Programmatic paste triggered by the global Ctrl+V hardware interceptor.
  /// Reads the system clipboard and inserts at the current cursor position,
  /// replacing any active selection. Only fires for the active pane so that
  /// multi-pane layouts don't all paste simultaneously.
  Future<void> _onPasteRequest() async {
    if (!mounted) return;
    final appState = context.read<AppState>();
    if (appState.activePaneId != _pane.paneId) return;
    _focusNode.requestFocus();
    final data = await Clipboard.getData(Clipboard.kTextPlain);
    final pasted = data?.text;
    if (pasted == null || !mounted) return;
    _insertAtCursor(pasted);
  }

  @override
  void dispose() {
    _vm.removeListener(_onVmChanged);
    _vm.dispose();
    _pane.unregisterDraftProvider();
    _focusRequestNotifier.removeListener(_onFocusRequest);
    _pasteRequestNotifier.removeListener(_onPasteRequest);
    _externalPasteNotifier.removeListener(_onExternalPaste);
    _debounceTimer?.cancel();
    _draftTimer?.cancel();
    _removeOverlay();
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  Future<void> _pickAttachments() async {
    final supportsImages = context
        .read<AppState>()
        .workerSupportsImageAttachments(context.read<PaneState>().workerId);
    await _vm.pickAttachments(supportsImages: supportsImages);
  }

  void _removeAttachment(int index) => _vm.removeAttachment(index);

  void _onTextChanged() {
    final has = _controller.text.trim().isNotEmpty;
    if (has != _hasText) setState(() => _hasText = has);

    // Debounced draft autosave — 800ms so it fires after the user pauses but
    // not on every keystroke. Uses pre-captured _pane (no context.read here).
    _draftTimer?.cancel();
    _draftTimer = Timer(const Duration(milliseconds: 800), () {
      _pane.triggerDraftSave();
    });

    // Snapshot mention state before _checkForMention potentially dismisses it
    final prevMentionStart = _mentionStart;
    final prevMentionType = _mentionType;
    final prevProjectSuggestions = List<Map<String, String>>.from(
      _projectSuggestions,
    );
    final prevToolSuggestions = List<Map<String, String>>.from(
      _toolSuggestions,
    );

    _checkForMention();

    // Space-trigger: if a mention was active and just got dismissed by typing a
    // space or newline, confirm it as a chip if the name matches.
    if (_mentionStart == null && prevMentionStart != null) {
      final text = _controller.text;
      final cursor = _controller.selection.baseOffset;
      if (cursor > 0 && cursor <= text.length) {
        final triggerChar = text[cursor - 1];
        if (triggerChar == ' ' || triggerChar == '\n') {
          final typedName = text.substring(prevMentionStart + 1, cursor - 1);
          if (typedName.isNotEmpty) {
            if (prevMentionType == _MentionType.project) {
              final match = prevProjectSuggestions.firstWhere(
                (s) => s['name']!.toLowerCase() == typedName.toLowerCase(),
                orElse: () => {},
              );
              if (match.isNotEmpty) {
                _confirmProjectMention(
                  match['name']!,
                  prevMentionStart,
                  cursor,
                  path: match['path'],
                );
              }
            } else if (prevMentionType == _MentionType.tool) {
              final match = prevToolSuggestions.firstWhere(
                (s) =>
                    s['mention_name']!.toLowerCase() == typedName.toLowerCase(),
                orElse: () => {},
              );
              if (match.isNotEmpty) {
                _confirmToolMention(
                  match['mention_name']!,
                  prevMentionStart,
                  cursor,
                );
              }
            }
          }
        }
      }
    }
  }

  /// Confirms a project mention: removes `@name[trigger]` from the text field
  /// and populates the project chip in PaneState.
  ///
  /// [path] is the full absolute path resolved from the server's project list.
  /// When provided, the Project panel can show worktrees/artifacts immediately
  /// without waiting for the first prompt to be sent.
  void _confirmProjectMention(String name, int start, int end, {String? path}) {
    final text = _controller.text;
    _controller.text = text.substring(0, start) + text.substring(end);
    _controller.selection = TextSelection.collapsed(offset: start);
    _dismissOverlay();
    context.read<PaneState>().setSelectedProject(name, path: path);
  }

  /// Confirms a tool mention: removes `#name[trigger]` from the text field
  /// and populates the tool chip in PaneState.
  void _confirmToolMention(String name, int start, int end) {
    final text = _controller.text;
    _controller.text = text.substring(0, start) + text.substring(end);
    _controller.selection = TextSelection.collapsed(offset: start);
    _dismissOverlay();
    context.read<PaneState>().setSelectedTool(name);
  }

  void _checkForMention() {
    final text = _controller.text;
    final selection = _controller.selection;
    if (!selection.isValid || !selection.isCollapsed) {
      _dismissOverlay();
      return;
    }
    final cursor = selection.baseOffset;

    // Walk backwards from cursor to find the nearest '@', '#', '$', or '/'
    int? triggerPos;
    String? triggerChar;
    for (var i = cursor - 1; i >= 0; i--) {
      final ch = text[i];
      if (ch == '@' || ch == '#' || ch == '\$' || ch == '/') {
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

    if (triggerChar == '/') {
      // Slash only activates when it is the very first character of the input.
      if (triggerPos != 0) {
        _dismissOverlay();
        return;
      }
    } else {
      // '@', '#', '$': must be at position 0 or preceded by whitespace
      if (triggerPos > 0 &&
          text[triggerPos - 1] != ' ' &&
          text[triggerPos - 1] != '\n') {
        _dismissOverlay();
        return;
      }
    }

    final query = text.substring(triggerPos + 1, cursor);
    if (query.contains('\n')) {
      _dismissOverlay();
      return;
    }

    _mentionStart = triggerPos;
    _mentionType = switch (triggerChar) {
      '@' => _MentionType.project,
      '#' => _MentionType.tool,
      '\$' => _MentionType.file,
      '/' => _MentionType.slash,
      _ => null,
    };
    if (_mentionType != null) _fetchSuggestions(query);
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
          _fileSuggestions = [];
          _slashSuggestions = [];
        } else if (_mentionType == _MentionType.tool) {
          final tools = await ws.fetchTools(query: query);
          if (!mounted) return;
          if (tools.isEmpty) {
            _showNoResults();
            return;
          }
          _showingNoResults = false;
          _toolSuggestions = tools.take(6).toList();
          _projectSuggestions = [];
          _fileSuggestions = [];
          _slashSuggestions = [];
        } else if (_mentionType == _MentionType.file) {
          final artifacts = await ws.fetchArtifactSuggestions(query: query);
          if (!mounted) return;
          if (artifacts.isEmpty) {
            _showNoResults();
            return;
          }
          _showingNoResults = false;
          _fileSuggestions = artifacts.take(8).toList();
          _projectSuggestions = [];
          _toolSuggestions = [];
          _slashSuggestions = [];
        } else if (_mentionType == _MentionType.slash) {
          final isCC = context.read<PaneState>().isClaudeCodeSession;
          final all = await ws.fetchSlashCommands(query: query);
          if (!mounted) return;
          final filtered = isCC
              ? all
              : all.where((c) => c['source'] == 'rcflow').toList();
          if (filtered.isEmpty) {
            _showNoResults();
            return;
          }
          _showingNoResults = false;
          _slashSuggestions = filtered.take(12).toList();
          _projectSuggestions = [];
          _toolSuggestions = [];
          _fileSuggestions = [];
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
    _fileSuggestions = [];
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
        _fileSuggestions.isEmpty &&
        _slashSuggestions.isEmpty &&
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
    _projectSuggestions = <Map<String, String>>[];
    _toolSuggestions = [];
    _fileSuggestions = [];
    _slashSuggestions = [];
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
    if (_mentionType == _MentionType.file) return _fileSuggestions.length;
    if (_mentionType == _MentionType.slash) return _slashSuggestions.length;
    return _projectSuggestions.length;
  }

  void _selectSuggestion(String name, {String? path}) {
    if (_mentionStart == null || _mentionType == null) return;
    // Project mentions go to the chip instead of inserting into the text field
    if (_mentionType == _MentionType.project) {
      _confirmProjectMention(
        name,
        _mentionStart!,
        _controller.selection.baseOffset,
        path: path,
      );
      return;
    }
    // Tool mentions go to the chip instead of inserting into the text field
    if (_mentionType == _MentionType.tool) {
      _confirmToolMention(
        name,
        _mentionStart!,
        _controller.selection.baseOffset,
      );
      return;
    }
    final text = _controller.text;
    final cursor = _controller.selection.baseOffset;
    final before = text.substring(0, _mentionStart!);
    final after = text.substring(cursor);
    final prefix = switch (_mentionType!) {
      _MentionType.tool => '#',
      _MentionType.file => '\$',
      _MentionType.slash => '/',
      _MentionType.project => '@', // unreachable but satisfies exhaustiveness
    };
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

  Future<void> _send() async {
    final text = _controller.text.trim();
    if (text.isEmpty && _vm.pendingAttachments.isEmpty) return;
    if (text.isEmpty) return; // text is still required
    if (_vm.uploadingAttachments) return;
    // Intercept RCFlow built-in slash commands before sending to server.
    if (text.startsWith('/') && _tryHandleRCFlowCommand(text)) return;
    final pane = context.read<PaneState>();
    final state = context.read<AppState>();
    context.read<AppState>().setActivePane(pane.paneId);

    // Snapshot and clear pending attachments immediately so the UI unblocks.
    final toUpload = _vm.takeAttachments();
    _controller.clear();
    _focusNode.requestFocus();

    if (toUpload.isEmpty) {
      pane.sendPrompt(text);
      return;
    }

    // Upload all files concurrently then send the prompt with the attachment IDs.
    List<Map<String, dynamic>>? uploaded;
    try {
      final wid = pane.workerId ?? state.defaultWorkerId;
      final ws = wid != null ? state.wsForWorker(wid) : null;
      if (ws == null) {
        pane.sendPrompt(text); // fall back to text-only on no connection
        return;
      }
      uploaded = await _vm.uploadAttachments(toUpload, ws);
    } catch (e) {
      // Upload failed — send text-only and surface an error in the pane
      pane.addSystemMessage('Attachment upload failed: $e', isError: true);
      uploaded = null;
    }

    pane.sendPrompt(text, attachments: uploaded);
  }

  /// Handles selection of a slash command from the suggestion overlay.
  /// RCFlow commands execute locally; Claude Code commands are sent as prompts.
  void _handleSlashSelected(String name, String source) {
    _controller.text = '/$name';
    _controller.selection = TextSelection.collapsed(offset: name.length + 1);
    _dismissOverlay();
    _send();
  }

  /// Attempts to handle a RCFlow built-in slash command. Returns true if handled.
  bool _tryHandleRCFlowCommand(String text) {
    final cmd = text.split(' ').first.toLowerCase();
    switch (cmd) {
      case '/clear':
        context.read<PaneState>().clearMessages();
        _controller.clear();
        _focusNode.requestFocus();
        return true;
      case '/new':
        context.read<PaneState>().startNewChat();
        _controller.clear();
        _focusNode.requestFocus();
        return true;
      case '/help':
        _showHelp();
        _controller.clear();
        _focusNode.requestFocus();
        return true;
      case '/pause':
        final sid = context.read<PaneState>().sessionId;
        if (sid != null) context.read<PaneState>().pauseSession(sid);
        _controller.clear();
        _focusNode.requestFocus();
        return true;
      case '/resume':
        final sid = context.read<PaneState>().sessionId;
        if (sid != null) context.read<PaneState>().resumeSession(sid);
        _controller.clear();
        _focusNode.requestFocus();
        return true;
      case '/plugins':
        _dispatchPluginsCommand();
        _controller.clear();
        _focusNode.requestFocus();
        return true;
      default:
        return false;
    }
  }

  void _showHelp() {
    final appState = context.read<AppState>();
    final paneId = context.read<PaneState>().paneId;
    appState.addSystemMessageToPane(
      paneId,
      'RCFlow slash commands: /clear, /new, /help, /pause, /resume, /plugins\n'
      '/plugins — open plugin settings for the active coding agent\n'
      'Type / to browse all available commands.\n'
      'Tip: ${getRandomTip()}',
    );
  }

  /// Dispatches the /plugins command by navigating to the worker settings pane
  /// for the active session's coding agent (or ``claude_code`` as default).
  void _dispatchPluginsCommand() {
    final appState = context.read<AppState>();
    final paneState = context.read<PaneState>();

    // Determine the agent type from the currently viewed session, if any.
    final sessionId = paneState.sessionId;
    String toolName = 'claude_code'; // default
    if (sessionId != null) {
      final session = appState.sessions.cast<dynamic>().firstWhere(
        (s) => s?.sessionId == sessionId,
        orElse: () => null,
      );
      final agentType = session?.agentType as String?;
      if (agentType != null) toolName = agentType;
    }

    appState.openWorkerSettingsInPane(toolName);
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
          if (_mentionType == _MentionType.slash) {
            final cmd = _slashSuggestions[_selectedIndex];
            _handleSlashSelected(cmd['name']!, cmd['source']!);
          } else if (_mentionType == _MentionType.project) {
            final item = _projectSuggestions[_selectedIndex];
            _selectSuggestion(item['name']!, path: item['path']);
          } else {
            final name = switch (_mentionType) {
              _MentionType.tool =>
                _toolSuggestions[_selectedIndex]['mention_name']!,
              _MentionType.file =>
                _fileSuggestions[_selectedIndex]['file_name']!,
              _MentionType.project ||
              _MentionType.slash ||
              null => _projectSuggestions[_selectedIndex]['name']!,
            };
            _selectSuggestion(name);
          }
          return KeyEventResult.handled;
        }
      }
    }

    if (event.logicalKey == LogicalKeyboardKey.enter) {
      final shift = HardwareKeyboard.instance.isShiftPressed;
      if (!shift) {
        // Enter-trigger: confirm a project @mention that has no overlay open
        if (_mentionType == _MentionType.project && _mentionStart != null) {
          final text = _controller.text;
          final cursor = _controller.selection.baseOffset;
          final typedName = text.substring(_mentionStart! + 1, cursor);
          if (typedName.isNotEmpty) {
            final match = _projectSuggestions.firstWhere(
              (s) => s['name']!.toLowerCase() == typedName.toLowerCase(),
              orElse: () => {},
            );
            _confirmProjectMention(
              typedName,
              _mentionStart!,
              cursor,
              path: match['path'],
            );
            return KeyEventResult.handled;
          }
        }
        // Enter-trigger: confirm a tool #mention that has no overlay open
        if (_mentionType == _MentionType.tool && _mentionStart != null) {
          final text = _controller.text;
          final cursor = _controller.selection.baseOffset;
          final typedName = text.substring(_mentionStart! + 1, cursor);
          if (typedName.isNotEmpty) {
            final match = _toolSuggestions.firstWhere(
              (s) =>
                  s['mention_name']!.toLowerCase() == typedName.toLowerCase(),
              orElse: () => {},
            );
            if (match.isNotEmpty) {
              _confirmToolMention(
                match['mention_name']!,
                _mentionStart!,
                cursor,
              );
              return KeyEventResult.handled;
            }
          }
        }
        _send(); // unawaited — intentional
        return KeyEventResult.handled;
      }
    }
    return KeyEventResult.ignored;
  }

  Widget _buildSlashOverlayContent(String query) {
    final children = <Widget>[];
    String? currentGroup;
    for (var i = 0; i < _slashSuggestions.length; i++) {
      final cmd = _slashSuggestions[i];
      final group = cmd['source'] == 'rcflow'
          ? 'RCFlow'
          : cmd['source'] == 'claude_code_plugin'
          ? 'Plugins'
          : 'Claude Code';
      if (group != currentGroup) {
        if (currentGroup != null) {
          children.add(Divider(height: 1, thickness: 1));
        }
        currentGroup = group;
        children.add(_SlashGroupHeader(label: group));
      }
      children.add(
        _SlashCommandItem(
          name: cmd['name']!,
          description: cmd['description']!,
          source: cmd['source']!,
          query: query,
          selected: i == _selectedIndex,
          onTap: () => _handleSlashSelected(cmd['name']!, cmd['source']!),
        ),
      );
    }
    return ConstrainedBox(
      constraints: const BoxConstraints(maxHeight: 380),
      child: SingleChildScrollView(
        child: Column(mainAxisSize: MainAxisSize.min, children: children),
      ),
    );
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
          final label = switch (_mentionType) {
            _MentionType.tool => 'No tools found',
            _MentionType.file => 'No artifacts found',
            _MentionType.slash => 'No commands found',
            _MentionType.project || null => 'No projects found',
          };
          content = Padding(
            padding: EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            child: Text(
              label,
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 13,
              ),
            ),
          );
        } else if (_mentionType == _MentionType.slash &&
            _slashSuggestions.isNotEmpty) {
          content = _buildSlashOverlayContent(mentionQuery);
        } else if (_mentionType == _MentionType.file &&
            _fileSuggestions.isNotEmpty) {
          content = ConstrainedBox(
            constraints: const BoxConstraints(maxHeight: 320),
            child: ListView.builder(
              padding: EdgeInsets.zero,
              shrinkWrap: true,
              itemCount: _fileSuggestions.length,
              itemBuilder: (context, index) {
                final file = _fileSuggestions[index];
                final selected = index == _selectedIndex;
                return _FileMentionItem(
                  fileName: file['file_name']!,
                  filePath: file['file_path']!,
                  fileExtension: file['file_extension']!,
                  isText: file['is_text'] == 'true',
                  query: mentionQuery,
                  selected: selected,
                  onTap: () => _selectSuggestion(file['file_name']!),
                );
              },
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
                  name: tool['display_name']!,
                  description: tool['description']!,
                  query: mentionQuery,
                  selected: selected,
                  onTap: () => _selectSuggestion(tool['mention_name']!),
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
                final item = _projectSuggestions[index];
                final name = item['name']!;
                final path = item['path'];
                final selected = index == _selectedIndex;
                return _MentionItem(
                  name: name,
                  query: mentionQuery,
                  selected: selected,
                  onTap: () => _selectSuggestion(name, path: path),
                );
              },
            ),
          );
        }

        final overlayWidth = switch (_mentionType) {
          _MentionType.file => 400.0,
          _MentionType.tool => 320.0,
          _MentionType.slash => 360.0,
          _MentionType.project || null => 280.0,
        };

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

  void _consumePendingInput() {
    final pane = context.read<PaneState>();
    final pending = pane.pendingInputText;
    if (pending != null) {
      _controller.text = pending;
      _controller.selection = TextSelection.collapsed(offset: pending.length);
      pane.consumePendingInputText();
      _focusNode.requestFocus();
    }
  }

  @override
  Widget build(BuildContext context) {
    final pendingInput = context.select<PaneState, String?>(
      (s) => s.pendingInputText,
    );
    if (pendingInput != null) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _consumePendingInput();
      });
    }

    // Detect pane switches so we invalidate the worktree cache — the widget
    // instance may be reused across different panes by Flutter.
    final currentPaneId = context.select<PaneState, String>((s) => s.paneId);
    if (_lastPaneId != null && _lastPaneId != currentPaneId) {
      _vm.resetWorktreeCache();
    }
    _lastPaneId = currentPaneId;

    final canSend = context.select<PaneState, bool>((s) => s.canSendMessage);
    final sessionEnded = context.select<PaneState, bool>((s) => s.sessionEnded);
    final sessionPaused = context.select<PaneState, bool>(
      (s) => s.sessionPaused,
    );
    final pausedReason = context.select<PaneState, String?>(
      (s) => s.pausedReason,
    );
    final sessionId = context.select<PaneState, String?>((s) => s.sessionId);
    final paneWorkerId = context.select<PaneState, String?>((s) => s.workerId);
    final bottom = MediaQuery.of(context).viewPadding.bottom;
    final showPauseResume = _isDesktop && sessionId != null && !sessionEnded;
    final modelSupportsAttachments = context.select<AppState, bool>(
      (s) => s.workerSupportsAttachments(paneWorkerId),
    );
    final canAttach =
        canSend && !_vm.uploadingAttachments && modelSupportsAttachments;

    // Worker selector chip for new chats
    final state = context.watch<AppState>();
    final connectedWorkers = state.workerConfigs
        .where((c) => state.getWorker(c.id)?.isConnected == true)
        .toList();
    final showWorkerChip = sessionId == null && connectedWorkers.length > 1;
    final selectedWorkerName = _resolveWorkerName(
      paneWorkerId,
      state.defaultWorkerId,
      connectedWorkers,
    );
    // Read-only worker badge: shown whenever the interactive picker isn't —
    // i.e. an existing session, or a new chat with only one connected worker —
    // so the user can always see which worker will receive the message.
    final showReadOnlyWorkerBadge =
        !showWorkerChip && selectedWorkerName != null;
    // Caveman mode indicator — surfaced alongside the other chips so the user
    // is always reminded before sending a message.
    final cavemanActive = context.select<PaneState, bool>(
      (s) => s.isCavemanActive,
    );

    // Project chip
    final selectedProject = context.select<PaneState, String?>(
      (s) => s.selectedProjectName,
    );
    final projectNameError = context.select<PaneState, String?>(
      (s) => s.projectNameError,
    );

    // Tool chip
    final selectedTool = context.select<PaneState, String?>(
      (s) => s.selectedToolMention,
    );

    // Pre-session worktree chip — shown when a project is selected and no
    // session exists yet, so the user can pre-select a worktree before sending.
    final pendingWorktreePath = context.select<PaneState, String?>(
      (s) => s.pendingWorktreePath,
    );
    final selectedProjectPath = context.select<PaneState, String?>(
      (s) => s.effectiveProjectPath,
    );

    // Active-session worktree chip — shown when the current session has a
    // project attached, so the user can see / change the active worktree.
    final activeWorktreePath = context.select<PaneState, String?>(
      (s) => s.currentSelectedWorktreePath,
    );
    final activeSessionProjectPath = context.select<PaneState, String?>(
      (s) => s.currentMainProjectPath,
    );

    // When the project path changes (user picks a different project), reset
    // the per-project flags so they are re-evaluated for the new project.
    final worktreeProjectPath = selectedProjectPath ?? activeSessionProjectPath;
    final worktreeWorkerId = paneWorkerId ?? state.defaultWorkerId ?? '';
    final currentCacheKey = worktreeProjectPath != null
        ? '$worktreeWorkerId:$worktreeProjectPath'
        : null;
    if (currentCacheKey != null &&
        currentCacheKey != _vm.worktreeCacheKey) {
      final cached = state.getProjectDataCache(currentCacheKey);
      // Sync the VM from the shared project-data cache when the project
      // changes. We need the flags synchronously here for chip visibility;
      // the VM will re-evaluate on the next fetchWorktrees call.
      _vm.noGitRepo = cached?.noGitRepo ?? false;
      _vm.worktreeFetchFailed = false;
    }

    // Hide the worktree chip entirely when the project is known to have no
    // git repository — there is nothing to pick.
    final showWorktreeChip =
        sessionId == null &&
        selectedProject != null &&
        selectedProjectPath != null &&
        !_vm.noGitRepo;
    final showActiveWorktreeChip =
        sessionId != null &&
        activeSessionProjectPath != null &&
        !_vm.noGitRepo;

    // Eagerly pre-fetch worktrees as soon as a project path is known so the
    // chip dropdown is ready on the first click.  _fetchWorktrees is
    // idempotent (it returns immediately if already loaded, loading, or if
    // the project is known to have no git repository).
    if (worktreeProjectPath != null &&
        !_vm.noGitRepo &&
        !_vm.worktreeFetchFailed) {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) _fetchWorktrees(worktreeProjectPath, worktreeWorkerId);
      });
    }

    // Subprocess status bar
    final runningSubprocess = context.select<PaneState, SubprocessInfo?>(
      (s) => s.runningSubprocess,
    );

    final String hintText;
    final IconData? prefixIcon;
    if (sessionEnded) {
      hintText = 'Session ended';
      prefixIcon = Icons.lock_rounded;
    } else if (sessionPaused && pausedReason == 'max_turns') {
      hintText = 'Turn limit reached — send a message to continue...';
      prefixIcon = Icons.hourglass_bottom_rounded;
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
        prefixIconConstraints: const BoxConstraints(minWidth: 40, minHeight: 0),
        contentPadding: const EdgeInsets.symmetric(
          horizontal: 20,
          vertical: 12,
        ),
      ),
      maxLines: _isDesktop ? 8 : 4,
      minLines: 1,
      textInputAction: _isDesktop
          ? TextInputAction.newline
          : TextInputAction.send,
      onSubmitted: _isDesktop
          ? null
          : (_) => _send(), // unawaited — intentional
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
            // All chips in a single horizontal Wrap row
            if (showWorkerChip ||
                showReadOnlyWorkerBadge ||
                cavemanActive ||
                selectedProject != null ||
                selectedTool != null ||
                showWorktreeChip ||
                showActiveWorktreeChip ||
                _vm.pendingAttachments.isNotEmpty ||
                _vm.uploadingAttachments)
              Padding(
                padding: const EdgeInsets.only(left: 4, bottom: 6),
                child: Wrap(
                  spacing: 6,
                  runSpacing: 4,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    if (showWorkerChip)
                      _WorkerChip(
                        label: selectedWorkerName ?? 'Select worker',
                        workers: connectedWorkers,
                        onSelected: (id) {
                          context.read<PaneState>().setTargetWorker(id);
                        },
                      ),
                    if (showReadOnlyWorkerBadge)
                      WorkerBadge(
                        name: selectedWorkerName,
                        interactive: false,
                      ),
                    if (cavemanActive)
                      CavemanPreviewBadge(
                        onDismiss: () =>
                            context.read<PaneState>().setCavemanDisabled(true),
                      ),
                    if (selectedProject != null)
                      _ProjectChip(
                        name: selectedProject,
                        error: projectNameError,
                        onClear: () {
                          context.read<PaneState>().setSelectedProject(null);
                          context.read<PaneState>().setPendingWorktreePath(
                            null,
                          );
                        },
                      ),
                    if (selectedTool != null)
                      _ToolChip(
                        name: selectedTool,
                        onClear: () {
                          context.read<PaneState>().setSelectedTool(null);
                        },
                      ),
                    if (showWorktreeChip)
                      _WorktreeChip(
                        selectedPath: pendingWorktreePath,
                        getWorktrees: () => _vm.preSessionWorktrees,
                        loading: _vm.loadingWorktrees,
                        onOpen: () => _fetchWorktrees(
                          selectedProjectPath,
                          paneWorkerId ?? state.defaultWorkerId ?? '',
                          force: true,
                        ),
                        onSelect: (path) => context
                            .read<PaneState>()
                            .setPendingWorktreePath(path),
                        onClear: () => context
                            .read<PaneState>()
                            .setPendingWorktreePath(null),
                        onCreateWorktree: () => _createAndSelectWorktree(
                          projectPath: selectedProjectPath,
                          workerId: paneWorkerId ?? state.defaultWorkerId ?? '',
                        ),
                      ),
                    if (showActiveWorktreeChip)
                      _WorktreeChip(
                        selectedPath: activeWorktreePath,
                        getWorktrees: () => _vm.preSessionWorktrees,
                        loading: _vm.loadingWorktrees,
                        onOpen: () => _fetchWorktrees(
                          activeSessionProjectPath,
                          paneWorkerId ?? state.defaultWorkerId ?? '',
                          force: true,
                        ),
                        onSelect: (path) =>
                            _setSessionWorktree(sessionId, path),
                        onClear: () => _setSessionWorktree(sessionId, null),
                        onCreateWorktree: () => _createAndSelectWorktree(
                          projectPath: activeSessionProjectPath,
                          workerId: paneWorkerId ?? state.defaultWorkerId ?? '',
                          sessionId: sessionId,
                        ),
                      ),
                    for (int i = 0; i < _vm.pendingAttachments.length; i++)
                      _AttachmentChip(
                        name: _vm.pendingAttachments[i].name,
                        mimeType: _vm.pendingAttachments[i].mimeType,
                        onRemove: canAttach ? () => _removeAttachment(i) : null,
                      ),
                    if (_vm.uploadingAttachments)
                      SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: context.appColors.textMuted,
                        ),
                      ),
                  ],
                ),
              ),
            if (runningSubprocess != null)
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: _SubprocessStatusBar(
                  subprocess: runningSubprocess,
                  onKill: () => context.read<PaneState>().interruptSubprocess(),
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
                    message: sessionPaused ? 'Resume session' : 'Pause session',
                    child: Material(
                      color: context.appColors.bgElevated,
                      shape: const CircleBorder(),
                      clipBehavior: Clip.antiAlias,
                      child: InkWell(
                        onTap: showPauseResume
                            ? () {
                                final pane = context.read<PaneState>();
                                if (sessionPaused) {
                                  pane.resumeSession(sessionId);
                                } else {
                                  pane.pauseSession(sessionId);
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
                // Attach file button
                Tooltip(
                  message: modelSupportsAttachments
                      ? 'Attach file'
                      : 'Attachments are not supported by the current model',
                  child: Material(
                    color: Colors.transparent,
                    shape: const CircleBorder(),
                    clipBehavior: Clip.antiAlias,
                    child: InkWell(
                      onTap: canAttach ? _pickAttachments : null,
                      child: Padding(
                        padding: const EdgeInsets.all(10),
                        child: Icon(
                          Icons.attach_file_rounded,
                          size: 20,
                          color: canAttach
                              ? context.appColors.textSecondary
                              : context.appColors.textMuted,
                        ),
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 2),
                Expanded(child: textField),
                SizedBox(width: 8),
                if (sessionEnded && sessionId != null)
                  Tooltip(
                    message: 'Restore session',
                    child: SizedBox(
                      width: 46,
                      height: 46,
                      child: Material(
                        color: context.appColors.accent,
                        shape: CircleBorder(),
                        clipBehavior: Clip.antiAlias,
                        child: InkWell(
                          onTap: () => context.read<PaneState>().restoreSession(
                            sessionId,
                          ),
                          child: const Center(
                            child: Icon(
                              Icons.restore_rounded,
                              color: Colors.white,
                              size: 22,
                            ),
                          ),
                        ),
                      ),
                    ),
                  )
                else
                  AnimatedContainer(
                    duration: Duration(milliseconds: 200),
                    width: 46,
                    height: 46,
                    child: Material(
                      color:
                          (_hasText || _vm.pendingAttachments.isNotEmpty) &&
                              canSend
                          ? context.appColors.accent
                          : context.appColors.bgElevated,
                      shape: CircleBorder(),
                      clipBehavior: Clip.antiAlias,
                      child: InkWell(
                        onTap:
                            (_hasText || _vm.pendingAttachments.isNotEmpty) &&
                                canSend
                            ? () =>
                                  _send() // unawaited — intentional
                            : null,
                        child: Center(
                          child: _vm.uploadingAttachments
                              ? SizedBox(
                                  width: 18,
                                  height: 18,
                                  child: CircularProgressIndicator(
                                    strokeWidth: 2,
                                    color: Colors.white,
                                  ),
                                )
                              : Icon(
                                  Icons.arrow_upward_rounded,
                                  color:
                                      (_hasText ||
                                              _vm.pendingAttachments.isNotEmpty) &&
                                          canSend
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

  String? _resolveWorkerName(
    String? paneWorkerId,
    String? defaultWorkerId,
    List<WorkerConfig> connectedWorkers,
  ) {
    final id = paneWorkerId ?? defaultWorkerId;
    if (id == null) return null;
    for (final c in connectedWorkers) {
      if (c.id == id) return c.name;
    }
    return connectedWorkers.isNotEmpty ? connectedWorkers.first.name : null;
  }
}

/// A chip displayed above the input field for each pending attachment.
class _AttachmentChip extends StatelessWidget {
  final String name;
  final String mimeType;
  final VoidCallback? onRemove;

  const _AttachmentChip({
    required this.name,
    required this.mimeType,
    this.onRemove,
  });

  static bool _isImage(String mime) => mime.startsWith('image/');

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      decoration: BoxDecoration(
        color: context.appColors.bgElevated,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: context.appColors.divider),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            _isImage(mimeType)
                ? Icons.image_rounded
                : Icons.insert_drive_file_rounded,
            size: 13,
            color: context.appColors.textMuted,
          ),
          const SizedBox(width: 5),
          ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 160),
            child: Text(
              name,
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 12,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (onRemove != null) ...[
            const SizedBox(width: 4),
            GestureDetector(
              onTap: onRemove,
              child: Icon(
                Icons.close_rounded,
                size: 13,
                color: context.appColors.textMuted,
              ),
            ),
          ],
        ],
      ),
    );
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
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(10),
          ),
          items: workers
              .map(
                (w) => PopupMenuItem<String>(
                  value: w.id,
                  height: 40,
                  child: Text(
                    w.name,
                    style: TextStyle(
                      color: context.appColors.textPrimary,
                      fontSize: 13,
                    ),
                  ),
                ),
              )
              .toList(),
        ).then((id) {
          if (id != null) onSelected(id);
        });
      },
      child: BadgeChip(
        label: label,
        icon: Icons.dns_outlined,
        trailing: BadgeChip.neutralDropdownCaret(context),
      ),
    );
  }
}

class _ProjectChip extends StatelessWidget {
  final String name;
  final String? error;
  final VoidCallback onClear;

  const _ProjectChip({required this.name, this.error, required this.onClear});

  // Neutral by default; error state uses project badge's red accent.
  static const _errorColor = Color(0xFFEF4444); // red-500

  @override
  Widget build(BuildContext context) {
    final hasError = error != null;
    final trailingColor =
        hasError ? _errorColor.withAlpha(180) : context.appColors.textMuted;
    return Tooltip(
      message: error ?? '',
      child: BadgeChip(
        color: hasError ? _errorColor : null,
        label: name,
        icon: hasError ? Icons.error_outline_rounded : Icons.folder_outlined,
        trailing: GestureDetector(
          onTap: onClear,
          child: Icon(Icons.close, size: 14, color: trailingColor),
        ),
      ),
    );
  }
}

/// Chip displayed above the input field representing a selected tool mention.
/// Mirrors _ProjectChip but uses a build icon and does not have an error state.
class _ToolChip extends StatelessWidget {
  final String name;
  final VoidCallback onClear;

  const _ToolChip({required this.name, required this.onClear});

  @override
  Widget build(BuildContext context) {
    return BadgeChip(
      label: name,
      icon: Icons.build_outlined,
      trailing: GestureDetector(
        onTap: onClear,
        child: Icon(Icons.close, size: 14, color: context.appColors.textMuted),
      ),
    );
  }
}

/// Chip displayed above the input field before session creation allowing the
/// user to pre-select a git worktree.  Shows the selected worktree name when
/// one is chosen, or a button to open the worktree picker dropdown.
class _WorktreeChip extends StatelessWidget {
  final String? selectedPath;
  // ValueGetter so the popup menu reads the current list at open-time, not
  // the stale value captured when the widget was last constructed.
  final List<Map<String, dynamic>>? Function() getWorktrees;
  final bool loading;
  final VoidCallback onOpen;
  final void Function(String path) onSelect;
  final VoidCallback onClear;
  final Future<void> Function()? onCreateWorktree;

  const _WorktreeChip({
    required this.selectedPath,
    required this.getWorktrees,
    required this.loading,
    required this.onOpen,
    required this.onSelect,
    required this.onClear,
    this.onCreateWorktree,
  });

  @override
  Widget build(BuildContext context) {
    final label = selectedPath != null
        ? selectedPath!.split('/').last
        : 'Worktree';

    return GestureDetector(
      onTap: () {
        onOpen();
        // Defer the menu until the frame after onOpen so that the fresh fetch
        // can complete; but we still open immediately with whatever data is
        // already cached so the picker is not sluggish.
        WidgetsBinding.instance.addPostFrameCallback((_) {
          final box = context.findRenderObject() as RenderBox?;
          if (box == null || !context.mounted) return;
          final offset = box.localToGlobal(Offset.zero);
          // Read the current worktree list at menu-open time, not the stale
          // value captured when this widget was last constructed.
          final currentWorktrees = getWorktrees();
          final items = <PopupMenuEntry<String>>[
            PopupMenuItem<String>(
              value: '__none__',
              height: 36,
              child: Text(
                'No worktree (default)',
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 12,
                ),
              ),
            ),
            const PopupMenuDivider(),
            if (currentWorktrees == null || currentWorktrees.isEmpty)
              PopupMenuItem<String>(
                enabled: false,
                height: 36,
                child: Text(
                  currentWorktrees == null ? 'Loading…' : 'No worktrees',
                  style: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 12,
                  ),
                ),
              )
            else
              ...currentWorktrees.map((wt) {
                final name = wt['name'] as String? ?? '';
                final branch = wt['branch'] as String? ?? '';
                final path = wt['path'] as String? ?? '';
                final isSelected = selectedPath == path;
                return PopupMenuItem<String>(
                  value: path,
                  height: 44,
                  child: Row(
                    children: [
                      Icon(
                        isSelected ? Icons.check_circle : Icons.call_split,
                        size: 14,
                        color: isSelected
                            ? context.appColors.accent
                            : context.appColors.textMuted,
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text(
                              name,
                              style: TextStyle(
                                color: isSelected
                                    ? context.appColors.accent
                                    : context.appColors.textPrimary,
                                fontSize: 12,
                                fontWeight: isSelected
                                    ? FontWeight.w600
                                    : FontWeight.w500,
                              ),
                              overflow: TextOverflow.ellipsis,
                            ),
                            if (branch.isNotEmpty)
                              Text(
                                branch,
                                style: TextStyle(
                                  color: context.appColors.textMuted,
                                  fontSize: 10,
                                ),
                                overflow: TextOverflow.ellipsis,
                              ),
                          ],
                        ),
                      ),
                    ],
                  ),
                );
              }),
            if (onCreateWorktree != null) ...[
              const PopupMenuDivider(),
              PopupMenuItem<String>(
                value: '__create__',
                height: 36,
                child: Row(
                  children: [
                    Icon(
                      Icons.add,
                      size: 14,
                      color: context.appColors.textSecondary,
                    ),
                    const SizedBox(width: 8),
                    Text(
                      'Create worktree',
                      style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 12,
                        fontWeight: FontWeight.w500,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ];

          showMenu<String>(
            context: context,
            position: RelativeRect.fromLTRB(
              offset.dx,
              offset.dy - (items.length * 40.0 + 16),
              offset.dx + box.size.width,
              offset.dy,
            ),
            color: context.appColors.bgSurface,
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(10),
            ),
            items: items,
          ).then((value) {
            if (value == null) return;
            if (value == '__none__') {
              onClear();
            } else if (value == '__create__') {
              onCreateWorktree?.call();
            } else {
              onSelect(value);
            }
          });
        });
      },
      child: BadgeChip(
        label: label,
        icon: Icons.account_tree_outlined,
        trailing: _trailing(context),
      ),
    );
  }

  Widget _trailing(BuildContext context) {
    if (loading) {
      return SizedBox(
        width: 12,
        height: 12,
        child: CircularProgressIndicator(
          strokeWidth: 1.5,
          color: context.appColors.textMuted,
        ),
      );
    }
    if (selectedPath != null) {
      return GestureDetector(
        onTap: onClear,
        behavior: HitTestBehavior.opaque,
        child: Icon(Icons.close, size: 14, color: context.appColors.textMuted),
      );
    }
    return BadgeChip.neutralDropdownCaret(context);
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
            Icon(
              Icons.folder_rounded,
              size: 16,
              color: context.appColors.textMuted,
            ),
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
      TextSpan(
        children: [
          if (matchIndex > 0)
            TextSpan(
              text: name.substring(0, matchIndex),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
              ),
            ),
          TextSpan(
            text: name.substring(matchIndex, matchIndex + query.length),
            style: TextStyle(
              color: context.appColors.accentLight,
              fontSize: 13,
              fontWeight: FontWeight.w600,
            ),
          ),
          if (matchIndex + query.length < name.length)
            TextSpan(
              text: name.substring(matchIndex + query.length),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
              ),
            ),
        ],
      ),
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
            Icon(
              Icons.build_rounded,
              size: 16,
              color: context.appColors.textMuted,
            ),
            SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _buildHighlightedName(context),
                  Text(
                    description,
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 11,
                    ),
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
      TextSpan(
        children: [
          if (matchIndex > 0)
            TextSpan(
              text: name.substring(0, matchIndex),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
              ),
            ),
          TextSpan(
            text: name.substring(matchIndex, matchIndex + query.length),
            style: TextStyle(
              color: context.appColors.accentLight,
              fontSize: 13,
              fontWeight: FontWeight.w600,
            ),
          ),
          if (matchIndex + query.length < name.length)
            TextSpan(
              text: name.substring(matchIndex + query.length),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
              ),
            ),
        ],
      ),
      overflow: TextOverflow.ellipsis,
    );
  }
}

class _FileMentionItem extends StatelessWidget {
  final String fileName;
  final String filePath;
  final String fileExtension;
  final bool isText;
  final String query;
  final bool selected;
  final VoidCallback onTap;

  const _FileMentionItem({
    required this.fileName,
    required this.filePath,
    required this.fileExtension,
    required this.isText,
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
            Icon(
              _iconForExtension(fileExtension),
              size: 16,
              color: context.appColors.textMuted,
            ),
            SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _buildHighlightedName(context),
                  Text(
                    filePath,
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 11,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ],
              ),
            ),
            if (!isText)
              Padding(
                padding: EdgeInsets.only(left: 6),
                child: Tooltip(
                  message: 'Binary file (metadata only)',
                  child: Icon(
                    Icons.visibility_off_rounded,
                    size: 12,
                    color: context.appColors.textMuted,
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  static IconData _iconForExtension(String ext) {
    final e = ext.toLowerCase();
    if (const [
      '.py',
      '.js',
      '.ts',
      '.dart',
      '.java',
      '.cpp',
      '.c',
      '.go',
      '.rs',
      '.rb',
      '.php',
      '.swift',
      '.kt',
    ].contains(e)) {
      return Icons.code_rounded;
    }
    if (const ['.md', '.txt', '.rst', '.log'].contains(e)) {
      return Icons.description_rounded;
    }
    if (const ['.json', '.yaml', '.yml', '.toml', '.xml'].contains(e)) {
      return Icons.data_object_rounded;
    }
    if (const ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp'].contains(e)) {
      return Icons.image_rounded;
    }
    if (e == '.pdf') return Icons.picture_as_pdf_rounded;
    return Icons.insert_drive_file_rounded;
  }

  Widget _buildHighlightedName(BuildContext context) {
    if (query.isEmpty) {
      return Text(
        fileName,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
        overflow: TextOverflow.ellipsis,
      );
    }

    final lowerName = fileName.toLowerCase();
    final lowerQuery = query.toLowerCase();
    final matchIndex = lowerName.indexOf(lowerQuery);

    if (matchIndex < 0) {
      return Text(
        fileName,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 13),
        overflow: TextOverflow.ellipsis,
      );
    }

    return Text.rich(
      TextSpan(
        children: [
          if (matchIndex > 0)
            TextSpan(
              text: fileName.substring(0, matchIndex),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
              ),
            ),
          TextSpan(
            text: fileName.substring(matchIndex, matchIndex + query.length),
            style: TextStyle(
              color: context.appColors.accentLight,
              fontSize: 13,
              fontWeight: FontWeight.w600,
            ),
          ),
          if (matchIndex + query.length < fileName.length)
            TextSpan(
              text: fileName.substring(matchIndex + query.length),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
              ),
            ),
        ],
      ),
      overflow: TextOverflow.ellipsis,
    );
  }
}

/// A slim bar shown above the input row when a subprocess (Claude Code, Codex,
/// etc.) is running. Displays the subprocess name, working directory, current
/// tool, and a kill button.
class _SubprocessStatusBar extends StatefulWidget {
  final SubprocessInfo subprocess;
  final VoidCallback onKill;

  const _SubprocessStatusBar({required this.subprocess, required this.onKill});

  @override
  State<_SubprocessStatusBar> createState() => _SubprocessStatusBarState();
}

class _SubprocessStatusBarState extends State<_SubprocessStatusBar> {
  bool _killing = false;

  void _onKill() {
    setState(() => _killing = true);
    widget.onKill();
    // Reset after a short timeout in case the server doesn't respond
    Future.delayed(const Duration(seconds: 5), () {
      if (mounted) setState(() => _killing = false);
    });
  }

  @override
  Widget build(BuildContext context) {
    final sub = widget.subprocess;
    final dirName = sub.workingDirectory.isNotEmpty
        ? sub.workingDirectory.split('/').last
        : '';
    final label = sub.currentTool != null
        ? '${sub.displayName} · ${sub.currentTool}'
        : sub.displayName;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
      decoration: BoxDecoration(
        color: context.appColors.bgElevated,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: context.appColors.divider),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          SizedBox(
            width: 10,
            height: 10,
            child: CircularProgressIndicator(
              strokeWidth: 1.5,
              color: context.appColors.accentLight,
            ),
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  label,
                  style: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 12,
                    fontWeight: FontWeight.w500,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
                if (dirName.isNotEmpty)
                  Text(
                    dirName,
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 11,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
              ],
            ),
          ),
          const SizedBox(width: 8),
          Tooltip(
            message: 'Kill subprocess',
            child: GestureDetector(
              onTap: _killing ? null : _onKill,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                decoration: BoxDecoration(
                  color: _killing
                      ? context.appColors.bgElevated
                      : context.appColors.errorBg,
                  borderRadius: BorderRadius.circular(5),
                  border: Border.all(
                    color: _killing
                        ? context.appColors.divider
                        : context.appColors.errorText.withValues(alpha: 0.4),
                  ),
                ),
                child: _killing
                    ? SizedBox(
                        width: 10,
                        height: 10,
                        child: CircularProgressIndicator(
                          strokeWidth: 1.5,
                          color: context.appColors.textMuted,
                        ),
                      )
                    : Text(
                        'Kill',
                        style: TextStyle(
                          color: context.appColors.errorText,
                          fontSize: 11,
                          fontWeight: FontWeight.w500,
                        ),
                      ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

class _SlashGroupHeader extends StatelessWidget {
  final String label;

  const _SlashGroupHeader({required this.label});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
      child: Text(
        label.toUpperCase(),
        style: TextStyle(
          color: context.appColors.textMuted,
          fontSize: 10,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.8,
        ),
      ),
    );
  }
}

class _SlashCommandItem extends StatelessWidget {
  final String name;
  final String description;
  final String source;
  final String query;
  final bool selected;
  final VoidCallback onTap;

  const _SlashCommandItem({
    required this.name,
    required this.description,
    required this.source,
    required this.query,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final isRCFlow = source == 'rcflow';
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        color: selected ? context.appColors.bgOverlay : Colors.transparent,
        child: Row(
          children: [
            Icon(
              isRCFlow ? Icons.electric_bolt_rounded : Icons.terminal_rounded,
              size: 15,
              color: isRCFlow
                  ? context.appColors.accentLight
                  : context.appColors.textMuted,
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _buildHighlightedName(context),
                  if (description.isNotEmpty)
                    Text(
                      description,
                      style: TextStyle(
                        color: context.appColors.textMuted,
                        fontSize: 11,
                      ),
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
        '/$name',
        style: TextStyle(
          color: context.appColors.textPrimary,
          fontSize: 13,
          fontWeight: FontWeight.w500,
        ),
        overflow: TextOverflow.ellipsis,
      );
    }

    final lowerName = name.toLowerCase();
    final lowerQuery = query.toLowerCase();
    final matchIndex = lowerName.indexOf(lowerQuery);

    if (matchIndex < 0) {
      return Text(
        '/$name',
        style: TextStyle(
          color: context.appColors.textPrimary,
          fontSize: 13,
          fontWeight: FontWeight.w500,
        ),
        overflow: TextOverflow.ellipsis,
      );
    }

    return Text.rich(
      TextSpan(
        children: [
          TextSpan(
            text: '/',
            style: TextStyle(
              color: context.appColors.textSecondary,
              fontSize: 13,
              fontWeight: FontWeight.w500,
            ),
          ),
          if (matchIndex > 0)
            TextSpan(
              text: name.substring(0, matchIndex),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
                fontWeight: FontWeight.w500,
              ),
            ),
          TextSpan(
            text: name.substring(matchIndex, matchIndex + query.length),
            style: TextStyle(
              color: context.appColors.accentLight,
              fontSize: 13,
              fontWeight: FontWeight.w600,
            ),
          ),
          if (matchIndex + query.length < name.length)
            TextSpan(
              text: name.substring(matchIndex + query.length),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
                fontWeight: FontWeight.w500,
              ),
            ),
        ],
      ),
      overflow: TextOverflow.ellipsis,
    );
  }
}
