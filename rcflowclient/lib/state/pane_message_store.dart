part of 'pane_state.dart';

/// Owns a pane's chat content: the display-message list, the live-streaming
/// assembler (assistant text / tool blocks / agent groups), todo state, queued
/// user messages, and history pagination.
///
/// Carved out of [PaneState] (the message/stream half of the class). It holds a
/// back-reference to its owning [PaneState] so it can read the current session
/// id and WebSocket, request a rebuild ([PaneState.notifyListeners]), and toggle
/// the shared right-panel selection. [PaneState] keeps the public surface and
/// forwards to this store; the streaming output handlers call those forwards.
class PaneMessageStore {
  /// [pane] is the owning state this store reads session/transport context from
  /// and notifies on every mutation.
  PaneMessageStore(this._pane);

  final PaneState _pane;

  String? get _sessionId => _pane._sessionId;
  WebSocketService? get _ws => _pane._ws;
  void _notify() => _pane.notifyListeners();

  static const _tickMs = 16;
  static const _pageSize = 50;

  // Message display.
  final List<DisplayMessage> _messages = [];
  List<DisplayMessage> get messages => _messages;

  // Pagination state (archived session message loading).
  int? _nextCursor;
  int _totalMessageCount = 0;
  bool _loadingMore = false;
  bool get loadingMore => _loadingMore;
  bool get hasMoreMessages => _nextCursor != null;
  int get totalMessageCount => _totalMessageCount;

  // Counter for locally-added user messages not yet echoed by server.
  // Prevents duplicate display when the server replays user messages.
  int _pendingLocalUserMessages = 0;

  // Queued user messages — pinned at the bottom of the chat while the agent
  // is busy processing a prior turn.  Sourced from ``message_queued`` /
  // ``message_dequeued`` / ``message_queued_updated`` events and the
  // ``queued_messages`` snapshot on ``session_update``.  See
  // ``Queued User Messages`` in ``docs/design/sessions.md``.
  final PaneQueueState _queue = PaneQueueState();
  List<QueuedMessage> get queuedMessages => _queue.snapshot;

  // Todo list state (from TodoWrite tool calls).
  List<TodoItem> _todos = [];
  List<TodoItem> get todos => _todos;

  // Agent-group assembly state.
  // True between agent_group_start and agent_group_end.
  bool _inAgentMode = false;
  // The tool name for the current agent group (e.g. 'claude_code', 'codex').
  String _agentToolName = 'claude_code';
  String get agentToolName => _agentToolName;
  // Human-readable display name for the current agent group.
  String? _agentDisplayName;
  // Index in _messages of the current tool sub-group being built.
  // Null when no tool group is open (e.g. between text and next tool batch).
  int? _agentToolGroupIndex;

  // Dynamic streaming.
  Timer? _streamingTimer;

  /// The list that streaming content should be appended to: either the active
  /// tool sub-group's children, or the top-level message list.
  List<DisplayMessage> get _streamTargetList {
    if (_agentToolGroupIndex != null &&
        _agentToolGroupIndex! < _messages.length &&
        _messages[_agentToolGroupIndex!].type ==
            DisplayMessageType.agentGroup) {
      return _messages[_agentToolGroupIndex!].children!;
    }
    return _messages;
  }

  /// The message that text is currently being written into.
  /// Returns null when the list is empty or the last message is already finished.
  DisplayMessage? get _streamTarget {
    final list = _streamTargetList;
    if (list.isEmpty) return null;
    final last = list.last;
    return last.finished ? null : last;
  }

  /// The last message in the current stream target list (public, for handlers).
  DisplayMessage? get lastStreamMessage => _streamTarget;

  /// Enter agent mode — subsequent tool calls will be auto-grouped.
  void startAgentGroup(
    String name,
    Map<String, dynamic>? input, {
    String? displayName,
  }) {
    finalizeStream();
    _inAgentMode = true;
    _agentToolName = name;
    _agentDisplayName = displayName;
    _messages.add(
      DisplayMessage(
        type: DisplayMessageType.agentGroup,
        sessionId: _sessionId,
        toolName: name,
        displayName: displayName,
        children: [],
        expanded: true,
      ),
    );
    _agentToolGroupIndex = _messages.length - 1;
    _notify();
  }

  /// Exit agent mode — close any open tool sub-group.
  void endAgentGroup() {
    finalizeStream();
    _closeAgentToolGroup();
    _inAgentMode = false;
    _notify();
  }

  /// Close any open tool sub-group and leave agent mode (no notify).
  /// Used by [PaneState.sendPrompt] before adding the optimistic user message.
  void exitAgentMode() {
    _closeAgentToolGroup();
    _inAgentMode = false;
  }

  /// Ensure a tool sub-group exists (create one if needed).
  void _ensureAgentToolGroup() {
    if (_agentToolGroupIndex != null) return;
    _messages.add(
      DisplayMessage(
        type: DisplayMessageType.agentGroup,
        sessionId: _sessionId,
        toolName: _agentToolName,
        displayName: _agentDisplayName,
        children: [],
        expanded: true,
      ),
    );
    _agentToolGroupIndex = _messages.length - 1;
  }

  /// Close the current tool sub-group (mark finished, collapse).
  void _closeAgentToolGroup() {
    if (_agentToolGroupIndex == null) return;
    if (_agentToolGroupIndex! < _messages.length &&
        _messages[_agentToolGroupIndex!].type ==
            DisplayMessageType.agentGroup) {
      final group = _messages[_agentToolGroupIndex!];
      group.finished = true;
      group.expanded = false;
      for (final child in group.children ?? <DisplayMessage>[]) {
        if (child.type == DisplayMessageType.toolBlock &&
            !child.finished &&
            !child.isQuestion) {
          child.finished = true;
        }
      }
    }
    _agentToolGroupIndex = null;
  }

  // -- Todo list management --

  /// Replace the todo list; auto-opens the todo panel when items appear.
  void updateTodos(List<TodoItem> todos) {
    _todos = todos;
    if (todos.isNotEmpty && _pane._activeRightPanel == null) {
      _pane._activeRightPanel = 'todo';
    }
    _notify();
  }

  /// Clear the todo list, closing the todo panel if it was open.
  void clearTodos() {
    if (_todos.isEmpty) return;
    _todos = [];
    if (_pane._activeRightPanel == 'todo') _pane._activeRightPanel = null;
    _notify();
  }

  /// Reconstruct todo state from a history message's metadata.
  void _reconstructTodosFromHistory(Map<String, dynamic> msg) {
    final metadata = msg['metadata'] as Map<String, dynamic>? ?? {};
    final rawTodos = metadata['todos'] as List<dynamic>? ?? [];
    if (rawTodos.isNotEmpty) {
      _todos = rawTodos
          .whereType<Map<String, dynamic>>()
          .map((t) => TodoItem.fromJson(t))
          .toList();
    }
  }

  // --- Streaming helpers ---

  /// Append streamed assistant text, opening a new assistant message as needed.
  void appendAssistantChunk(String text) {
    // Assistant text breaks a tool group — close it and add text at top level.
    if (_inAgentMode && _agentToolGroupIndex != null) {
      finalizeStream();
      _closeAgentToolGroup();
    }
    if (_messages.isEmpty ||
        _messages.last.type != DisplayMessageType.assistant ||
        _messages.last.finished) {
      finalizeStream();
      _messages.add(
        DisplayMessage(
          type: DisplayMessageType.assistant,
          sessionId: _sessionId,
        ),
      );
      _scheduleNotify();
    }
    _enqueueText(text);
  }

  /// Open a new streaming tool block, grouping it under the agent if active.
  void startToolBlock(
    String name,
    Map<String, dynamic>? input, {
    String? displayName,
    bool answered = false,
    String? answer,
  }) {
    finalizeStream();
    // AskUserQuestion renders at the top level rather than inside the agent
    // tool-group: the group collapses when the turn ends, which would otherwise
    // bury the (still-relevant) answered question behind a tool-count chip.
    // Close any open group so subsequent tools start a fresh group after the
    // question, preserving chronological order.
    if (name == 'AskUserQuestion') {
      if (_inAgentMode) {
        _closeAgentToolGroup();
        // Drop the group if the question was its first tool — an empty closed
        // group would otherwise render as a stray "0 tools" header.
        if (_messages.isNotEmpty &&
            _messages.last.type == DisplayMessageType.agentGroup &&
            (_messages.last.children?.isEmpty ?? true)) {
          _messages.removeLast();
        }
      }
      _messages.add(
        DisplayMessage(
          type: DisplayMessageType.toolBlock,
          sessionId: _sessionId,
          toolName: name,
          displayName: displayName,
          toolInput: input,
          // On replay an already-answered question comes back resolved.
          finished: answered,
          expanded: answered,
          content: answered ? (answer ?? '') : '',
        ),
      );
      // The question is finished via answerQuestion (or the session-end
      // sweep), never by finalizeStream (which skips isQuestion blocks).
      _scheduleNotify();
      return;
    }
    if (_inAgentMode) {
      _ensureAgentToolGroup();
    }
    _streamTargetList.add(
      DisplayMessage(
        type: DisplayMessageType.toolBlock,
        sessionId: _sessionId,
        toolName: name,
        displayName: displayName,
        toolInput: input,
      ),
    );
    _scheduleNotify();
  }

  /// Append output text to the current tool block.
  void appendToolOutput(String text, {bool isError = false}) {
    final target = _streamTargetList;
    if (target.isEmpty || target.last.type != DisplayMessageType.toolBlock) {
      target.add(
        DisplayMessage(
          type: DisplayMessageType.toolBlock,
          sessionId: _sessionId,
          toolName: 'output',
        ),
      );
      _scheduleNotify();
    }
    if (isError && target.isNotEmpty) {
      target.last.isError = true;
      target.last.expanded = true;
    }
    _enqueueText(text);
  }

  /// Attach a unified diff to the most recent tool block in the stream target.
  void applyDiffToLastToolBlock(String diff) {
    final list = _streamTargetList;
    for (int i = list.length - 1; i >= 0; i--) {
      if (list[i].type == DisplayMessageType.toolBlock) {
        list[i].fileDiff = diff;
        // Auto-expand Edit/Write blocks so the diff is visible immediately.
        final tn = list[i].toolName?.toLowerCase();
        if (tn == 'edit' || tn == 'write') {
          list[i].expanded = true;
        }
        _scheduleNotify();
        return;
      }
    }
  }

  void _enqueueText(String text) {
    final target = _streamTarget;
    if (target == null) return;
    target.content += text;
    _scheduleNotify();
  }

  /// Coalesce streaming-path mutations into a single rebuild per [_tickMs]
  /// window. Use for any state change driven by the inbound WS stream
  /// (append text, open tool block, attach diff, add streamed message). Use
  /// [_notify] directly only for terminal transitions where the 16 ms latency
  /// would feel wrong (finalize, session switch, errors).
  void _scheduleNotify() {
    _streamingTimer ??= Timer(
      const Duration(milliseconds: _tickMs),
      _renderChars,
    );
  }

  void _renderChars() {
    _streamingTimer = null;
    _notify();
  }

  /// Finish the current streaming message (assistant or tool block).
  void finalizeStream() {
    _streamingTimer?.cancel();
    _streamingTimer = null;

    final target = _streamTarget;
    if (target != null) {
      // Finish any open tool/output block (questions excepted — they are
      // resolved via answerQuestion / the session-end sweep). A standalone
      // TOOL_OUTPUT, e.g. a native background command's between-turns completion
      // notice, creates an orphan 'output' toolBlock via appendToolOutput; it
      // must be closed here too, otherwise that block spins forever.
      if (target.type == DisplayMessageType.toolBlock && !target.isQuestion) {
        target.finished = true;
      } else if (target.type == DisplayMessageType.assistant) {
        target.finished = true;
      }
    }

    _notify();
  }

  /// Returns true if a locally-added user message matches [echoContent] —
  /// meaning the server echo should be skipped.
  ///
  /// Uses content-based matching against pending local messages, with the
  /// counter as a fallback. This is robust against timing issues where the
  /// counter might be reset (e.g. by an intervening [PaneState.switchSession]).
  bool consumeLocalUserMessage(String echoContent) {
    // Primary: content-based match against pending local messages.
    for (int i = _messages.length - 1; i >= 0; i--) {
      final m = _messages[i];
      if (m.type == DisplayMessageType.user &&
          m.pendingLocalEcho &&
          m.content == echoContent) {
        m.pendingLocalEcho = false;
        if (_pendingLocalUserMessages > 0) _pendingLocalUserMessages--;
        return true;
      }
    }
    // Fallback: counter only (e.g. if content was mutated).
    if (_pendingLocalUserMessages > 0) {
      _pendingLocalUserMessages--;
      return true;
    }
    return false;
  }

  // ---------------------------------------------------------------------------
  // Queued-message reconciliation — called by output handlers and ack handlers.
  // See ``Queued User Messages`` in ``docs/design/sessions.md`` for the full
  // protocol.

  /// Promote an optimistic pending-echo DisplayMessage into a real
  /// [QueuedMessage] after the server ack confirms the message was queued.
  void promoteLocalEchoToQueued({
    required String queuedId,
    required String content,
  }) {
    for (int i = _messages.length - 1; i >= 0; i--) {
      final m = _messages[i];
      if (m.type == DisplayMessageType.user &&
          m.pendingLocalEcho &&
          m.content == content) {
        _messages.removeAt(i);
        if (_pendingLocalUserMessages > 0) _pendingLocalUserMessages--;
        break;
      }
    }
    final now = DateTime.now();
    _queue.upsert(
      QueuedMessage(
        queuedId: queuedId,
        position: _queue.length,
        content: content,
        displayContent: content,
        submittedAt: now,
        updatedAt: now,
        pendingLocalEcho: true,
      ),
    );
    _notify();
  }

  /// Add or update a queued message from a ``message_queued`` event.
  void applyMessageQueued(Map<String, dynamic> msg) {
    final queuedId = msg['queued_id'] as String?;
    if (queuedId == null) return;
    _queue.upsert(
      QueuedMessage(
        queuedId: queuedId,
        position: (msg['position'] as num?)?.toInt() ?? _queue.length,
        content: msg['content'] as String? ?? '',
        displayContent:
            msg['display_content'] as String? ??
            msg['content'] as String? ??
            '',
        submittedAt:
            DateTime.tryParse(msg['submitted_at'] as String? ?? '') ??
            DateTime.now(),
        updatedAt:
            DateTime.tryParse(msg['submitted_at'] as String? ?? '') ??
            DateTime.now(),
      ),
    );
    _notify();
  }

  /// Remove a queued message from a ``message_dequeued`` event.
  void applyMessageDequeued(String queuedId) {
    if (!_queue.dequeue(queuedId)) return;
    _notify();
  }

  /// Update a queued message's text from a ``message_queued_updated`` event.
  void applyMessageQueuedUpdated(Map<String, dynamic> msg) {
    final queuedId = msg['queued_id'] as String?;
    if (queuedId == null) return;
    final found = _queue.update(
      queuedId,
      content: msg['content'] as String?,
      displayContent: msg['display_content'] as String?,
      updatedAt: DateTime.tryParse(msg['updated_at'] as String? ?? ''),
    );
    if (found) _notify();
  }

  /// Replace the local queue with the authoritative server snapshot.
  void applyQueueSnapshot(List<Map<String, dynamic>> snapshot) {
    _queue.replaceSnapshot([
      for (final raw in snapshot) QueuedMessage.fromSnapshot(raw),
    ]);
    _notify();
  }

  /// Handle a ``cancel_ack`` — visible state is driven by the dequeue stream.
  void applyCancelAck(Map<String, dynamic> msg) {
    // Cancel success already removed the entry via message_dequeued; nothing to
    // do here.  Cancel failure means the message was already delivered — the
    // subsequent text_chunk will insert it into history, so also nothing to do.
    // Kept for hook symmetry so ack messages are not logged as "unknown type".
    final ok = msg['ok'] as bool? ?? false;
    if (ok) return;
    // Swallow — the dequeue stream handles the visible state transition.
  }

  /// Handle an ``edit_ack`` — the visible update arrives via queued_updated.
  void applyEditAck(Map<String, dynamic> msg) {
    final ok = msg['ok'] as bool? ?? false;
    if (ok) {
      // message_queued_updated already carried the new text to the UI.
      return;
    }
    // Edit failed (message already delivered or empty).  The UI rolls back via
    // the caller-side optimistic tracking layer in the edit widget.
  }

  /// Request cancellation of a queued message.  Optimistically removes it
  /// from the local queue; on ``cancel_ack{ok: false, already_delivered}``
  /// the subsequent ``text_chunk`` reinserts it into chat history.
  void cancelQueuedMessage(String queuedId) {
    final sid = _sessionId;
    if (sid == null) return;
    applyMessageDequeued(queuedId);
    _ws?.cancelQueued(sid, queuedId);
  }

  /// Update the text of a queued message.  Optimistically mutates the local
  /// queue entry; the server confirms via ``edit_ack`` and mirror-broadcasts
  /// ``message_queued_updated``.
  void editQueuedMessage(String queuedId, String content) {
    final sid = _sessionId;
    if (sid == null) return;
    if (!_queue.editText(queuedId, content, DateTime.now())) return;
    _notify();
    _ws?.editQueued(sid, queuedId, content);
  }

  /// Append a finished display message at the top level.
  void addDisplayMessage(DisplayMessage msg) {
    _messages.add(msg);
    _notify();
  }

  /// Add a display message respecting agent group nesting.
  void addDisplayMessageInStream(DisplayMessage msg) {
    if (_inAgentMode) {
      _ensureAgentToolGroup();
    }
    _streamTargetList.add(msg);
    _scheduleNotify();
  }

  /// Append a system/error notice to the message list.
  void addSystemMessage(String text, {bool isError = false}) {
    _addSystemMessage(text, isError: isError);
  }

  void _addSystemMessage(String text, {bool isError = false}) {
    _messages.add(
      DisplayMessage(
        type: isError ? DisplayMessageType.error : DisplayMessageType.system,
        content: text,
        isError: isError,
      ),
    );
    _notify();
  }

  /// Append an optimistic local user message and bump the pending-echo counter.
  /// Used by [PaneState.sendPrompt] so the typed prompt shows instantly.
  void addLocalUserMessage(DisplayMessage msg) {
    _pendingLocalUserMessages++;
    _messages.add(msg);
  }

  /// Mark any unanswered question blocks as finished (e.g. on session end).
  void finishPendingQuestions() {
    for (final m in _messages) {
      if (m.isQuestion && !m.finished) m.finished = true;
      if (m.type == DisplayMessageType.agentGroup) {
        for (final child in m.children ?? <DisplayMessage>[]) {
          if (child.isQuestion && !child.finished) child.finished = true;
        }
      }
    }
  }

  /// Clear the displayed message list for this pane (client-side only).
  /// Does not affect the server-side session or database history.
  void clearMessages() {
    finalizeStream();
    _messages.clear();
    _todos = [];
    if (_pane._activeRightPanel == 'todo') _pane._activeRightPanel = null;
    _pendingLocalUserMessages = 0;
    _queue.clear();
    _notify();
  }

  /// Reset the entire chat content for a session switch / new chat / home.
  /// Mirrors the field-by-field reset the lifecycle methods used to inline;
  /// the owning [PaneState] separately resets its own (session/panel) state.
  void resetForSwitch() {
    finalizeStream();
    _inAgentMode = false;
    _agentToolGroupIndex = null;
    _messages.clear();
    _todos = [];
    resetPagination();
    _pendingLocalUserMessages = 0;
    _queue.clear();
  }

  // --- Pagination ---

  /// Load the first page of an archived session's history into the message list.
  Future<void> loadSessionMessages(String sessionId) async {
    _addSystemMessage('Loading session history...');
    try {
      final ws = _ws;
      if (ws == null) {
        _addSystemMessage('Not connected to worker', isError: true);
        return;
      }
      final response = await ws.fetchSessionMessages(
        sessionId,
        limit: _pageSize,
      );
      if (_sessionId != sessionId) return;

      if (_messages.isNotEmpty &&
          _messages.last.type == DisplayMessageType.system) {
        _messages.removeLast();
      }

      final rawMessages = (response['messages'] as List<dynamic>? ?? [])
          .cast<Map<String, dynamic>>();
      final pagination = response['pagination'] as Map<String, dynamic>? ?? {};
      _totalMessageCount = pagination['total_count'] as int? ?? 0;
      final hasMore = pagination['has_more'] as bool? ?? false;
      _nextCursor = hasMore ? pagination['next_cursor'] as int? : null;

      if (rawMessages.isEmpty) {
        _addSystemMessage('No message history available');
      } else {
        _buildDisplayFromHistoryInto(rawMessages, sessionId, _messages);
      }
    } catch (e) {
      _addSystemMessage('Failed to load session history: $e', isError: true);
    }
  }

  /// Load the next older page of history and prepend it to the message list.
  Future<void> loadOlderMessages() async {
    if (_loadingMore || _nextCursor == null || _sessionId == null) return;
    final sessionId = _sessionId!;
    final ws = _ws;
    if (ws == null) return;

    _loadingMore = true;
    _notify();

    try {
      final response = await ws.fetchSessionMessages(
        sessionId,
        before: _nextCursor,
        limit: _pageSize,
      );
      if (_sessionId != sessionId) return;

      final rawMessages = (response['messages'] as List<dynamic>? ?? [])
          .cast<Map<String, dynamic>>();
      final pagination = response['pagination'] as Map<String, dynamic>? ?? {};
      final hasMore = pagination['has_more'] as bool? ?? false;
      _nextCursor = hasMore ? pagination['next_cursor'] as int? : null;

      if (rawMessages.isNotEmpty) {
        final olderMessages = <DisplayMessage>[];
        _buildDisplayFromHistoryInto(rawMessages, sessionId, olderMessages);
        _messages.insertAll(0, olderMessages);
      }
    } catch (e) {
      _addSystemMessage('Failed to load older messages: $e', isError: true);
    } finally {
      _loadingMore = false;
      _notify();
    }
  }

  void _buildDisplayFromHistoryInto(
    List<Map<String, dynamic>> rawMessages,
    String sessionId,
    List<DisplayMessage> target,
  ) {
    bool inAgent = false;
    int? toolGroupIdx;
    String? agentDisplayName;
    String agentToolName = 'claude_code';

    void closeToolGroup() {
      if (toolGroupIdx != null && toolGroupIdx! < target.length) {
        final group = target[toolGroupIdx!];
        group.finished = true;
        group.expanded = false;
        for (final child in group.children ?? <DisplayMessage>[]) {
          if (child.type == DisplayMessageType.toolBlock && !child.finished) {
            child.finished = true;
          }
        }
      }
      toolGroupIdx = null;
    }

    void ensureToolGroup() {
      if (toolGroupIdx != null) return;
      target.add(
        DisplayMessage(
          type: DisplayMessageType.agentGroup,
          sessionId: sessionId,
          toolName: agentToolName,
          displayName: agentDisplayName,
          children: [],
          finished: false,
        ),
      );
      toolGroupIdx = target.length - 1;
    }

    for (final msg in rawMessages) {
      final type = msg['type'] as String? ?? '';

      if (type == 'agent_group_start') {
        inAgent = true;
        final meta = msg['metadata'] as Map<String, dynamic>?;
        agentDisplayName = meta?['display_name'] as String?;
        agentToolName = meta?['tool_name'] as String? ?? 'claude_code';
        continue;
      }

      if (type == 'agent_group_end') {
        closeToolGroup();
        inAgent = false;
        continue;
      }

      if (inAgent) {
        if (type == 'todo_update') {
          // Todo updates go into the agent group but also update pane state
          ensureToolGroup();
          final builder = historyBuilderRegistry[type];
          if (builder != null) {
            builder(msg, sessionId, target[toolGroupIdx!].children!);
          }
          _reconstructTodosFromHistory(msg);
        } else if (type == 'tool_start' &&
            (msg['metadata'] as Map<String, dynamic>?)?['tool_name'] ==
                'AskUserQuestion') {
          // Questions render at the top level (see startToolBlock); lift them
          // out of the agent group on replay too. Close the group so order is
          // preserved and a fresh group opens for any later tools.
          closeToolGroup();
          final builder = historyBuilderRegistry[type];
          if (builder != null) builder(msg, sessionId, target);
        } else if (type == 'tool_start' || type == 'tool_output') {
          ensureToolGroup();
          final builder = historyBuilderRegistry[type];
          if (builder != null) {
            builder(msg, sessionId, target[toolGroupIdx!].children!);
          }
        } else if (type == 'text_chunk') {
          final metadata = msg['metadata'] as Map<String, dynamic>? ?? {};
          if (metadata['role'] == 'user') {
            final builder = historyBuilderRegistry[type];
            if (builder != null) builder(msg, sessionId, target);
          } else {
            // Assistant text closes the current tool group
            closeToolGroup();
            final builder = historyBuilderRegistry[type];
            if (builder != null) builder(msg, sessionId, target);
          }
        } else {
          // Other types (error, summary, etc.) go to top level
          closeToolGroup();
          final builder = historyBuilderRegistry[type];
          if (builder != null) builder(msg, sessionId, target);
        }
      } else {
        final builder = historyBuilderRegistry[type];
        if (builder != null) builder(msg, sessionId, target);
        if (type == 'todo_update') {
          _reconstructTodosFromHistory(msg);
        }
      }
    }

    // Finalize any remaining open groups
    closeToolGroup();

    for (final m in target) {
      if (m.type == DisplayMessageType.toolBlock && !m.finished) {
        m.finished = true;
      }
      if (m.type == DisplayMessageType.agentGroup) {
        for (final child in m.children ?? <DisplayMessage>[]) {
          if (child.type == DisplayMessageType.toolBlock && !child.finished) {
            child.finished = true;
          }
        }
      }
    }

    _notify();
  }

  /// Reset pagination cursors (called on session switch / new chat).
  void resetPagination() {
    _nextCursor = null;
    _totalMessageCount = 0;
    _loadingMore = false;
  }

  /// Cancel the streaming-coalesce timer (called from [PaneState.dispose]).
  void disposeTimers() {
    _streamingTimer?.cancel();
  }
}
