import '../../ui/widgets/terminal_pane.dart';

/// In-memory store of terminal sessions keyed by terminalId, owned by
/// [AppState].  Survives pane close/reopen.  Holds the data + read-only
/// projections; AppState keeps the pane-management + notify responsibilities.
/// Part of the Phase 5 step-3 carve of AppState into per-feature stores.
class TerminalSessionStore {
  final Map<String, TerminalSessionInfo> _sessions = {};

  /// Live view of all terminal sessions (mutable entries).
  Iterable<TerminalSessionInfo> get values => _sessions.values;

  /// Unmodifiable snapshot keyed by terminalId.
  Map<String, TerminalSessionInfo> get unmodifiable =>
      Map.unmodifiable(_sessions);

  TerminalSessionInfo? get(String terminalId) => _sessions[terminalId];

  void put(String terminalId, TerminalSessionInfo info) =>
      _sessions[terminalId] = info;

  TerminalSessionInfo? remove(String terminalId) =>
      _sessions.remove(terminalId);

  /// The terminal session currently attached to [paneId], or null.
  TerminalSessionInfo? findByPane(String paneId) {
    for (final info in _sessions.values) {
      if (info.paneId == paneId) return info;
    }
    return null;
  }

  /// Terminal sessions grouped by workerId, each sorted by createdAt desc.
  Map<String, List<TerminalSessionInfo>> byWorker() {
    final map = <String, List<TerminalSessionInfo>>{};
    for (final info in _sessions.values) {
      map.putIfAbsent(info.workerId, () => []).add(info);
    }
    for (final list in map.values) {
      list.sort((a, b) => b.createdAt.compareTo(a.createdAt));
    }
    return map;
  }
}
