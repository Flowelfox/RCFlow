import '../models/ws_messages.dart';

/// In-memory mirror of a pane's queued (not-yet-delivered) user messages,
/// owned by [PaneState].  Pure list manipulation kept ordered by ``position``;
/// PaneState keeps the notify + WebSocket-send responsibilities and parses the
/// wire messages.  Part of the Phase 5 step-3 carve.
class PaneQueueState {
  final List<QueuedMessage> _queue = [];

  List<QueuedMessage> get snapshot => List.unmodifiable(_queue);
  int get length => _queue.length;

  void clear() => _queue.clear();

  /// Insert [entry], or update the matching entry in place; keep ordered by
  /// ``position``.  An update clears the optimistic ``pendingLocalEcho`` flag.
  void upsert(QueuedMessage entry) {
    final idx = _queue.indexWhere((q) => q.queuedId == entry.queuedId);
    if (idx >= 0) {
      _queue[idx]
        ..position = entry.position
        ..content = entry.content
        ..displayContent = entry.displayContent
        ..submittedAt = entry.submittedAt
        ..updatedAt = entry.updatedAt
        ..pendingLocalEcho = false;
    } else {
      _queue.add(entry);
    }
    _queue.sort((a, b) => a.position.compareTo(b.position));
  }

  /// Remove [queuedId] and renumber positions densely from 0.  Returns whether
  /// an entry was actually removed.
  bool dequeue(String queuedId) {
    final idx = _queue.indexWhere((q) => q.queuedId == queuedId);
    if (idx < 0) return false;
    _queue.removeAt(idx);
    for (var i = 0; i < _queue.length; i++) {
      _queue[i].position = i;
    }
    return true;
  }

  /// Partially update text fields on [queuedId] (null values keep the prior
  /// value).  Returns whether the entry was found.
  bool update(
    String queuedId, {
    String? content,
    String? displayContent,
    DateTime? updatedAt,
  }) {
    final idx = _queue.indexWhere((q) => q.queuedId == queuedId);
    if (idx < 0) return false;
    final entry = _queue[idx];
    entry.content = content ?? entry.content;
    entry.displayContent = displayContent ?? entry.displayContent;
    entry.updatedAt = updatedAt ?? entry.updatedAt;
    return true;
  }

  /// Replace the whole queue with the authoritative server snapshot.
  void replaceSnapshot(List<QueuedMessage> incoming) {
    incoming.sort((a, b) => a.position.compareTo(b.position));
    _queue
      ..clear()
      ..addAll(incoming);
  }

  /// Optimistically set the text of [queuedId].  Returns whether it was found.
  bool editText(String queuedId, String content, DateTime updatedAt) {
    final idx = _queue.indexWhere((q) => q.queuedId == queuedId);
    if (idx < 0) return false;
    _queue[idx]
      ..content = content
      ..displayContent = content
      ..updatedAt = updatedAt;
    return true;
  }
}
