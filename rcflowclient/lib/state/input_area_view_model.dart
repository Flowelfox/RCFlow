/// ViewModel for the input area widget.
///
/// Owns non-widget state that is independent of the Flutter rendering
/// lifecycle: pending file attachments and the per-project worktree cache.
///
/// UI-lifecycle state (TextEditingController, FocusNode, overlay entries,
/// draft timer, mention detection) is intentionally kept in
/// [_InputAreaState] since it requires direct widget-tree access.
library;

import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';

import '../services/websocket_service.dart';
import '../state/app_state.dart';

// ---------------------------------------------------------------------------
// PendingAttachment
// ---------------------------------------------------------------------------

/// A file selected by the user but not yet uploaded.
class PendingAttachment {
  final String name;
  final String mimeType;
  final List<int> bytes;

  const PendingAttachment({
    required this.name,
    required this.mimeType,
    required this.bytes,
  });
}

// ---------------------------------------------------------------------------
// InputAreaViewModel
// ---------------------------------------------------------------------------

class InputAreaViewModel extends ChangeNotifier {
  final AppState _appState;

  // --- Pending attachments ---

  final List<PendingAttachment> _pendingAttachments = [];
  List<PendingAttachment> get pendingAttachments =>
      List.unmodifiable(_pendingAttachments);

  bool uploadingAttachments = false;

  // --- Worktree cache ---

  /// Cache key is `"$workerId:$projectPath"`.
  String? _worktreeCacheKey;
  String? get worktreeCacheKey => _worktreeCacheKey;

  List<Map<String, dynamic>>? preSessionWorktrees;
  bool loadingWorktrees = false;

  /// Set after a fetch failure; suppresses auto-retry on rebuilds.
  /// Reset when the cache key changes or the user explicitly opens the dropdown.
  bool worktreeFetchFailed = false;

  /// Set when the server reports "Not a git repository" — a permanent
  /// condition that suppresses the worktree chip until the project changes.
  bool noGitRepo = false;

  // ---------------------------------------------------------------------------
  // Constructor
  // ---------------------------------------------------------------------------

  InputAreaViewModel(this._appState);

  // ---------------------------------------------------------------------------
  // Attachment management
  // ---------------------------------------------------------------------------

  Future<void> pickAttachments({required bool supportsImages}) async {
    final result = await FilePicker.pickFiles(
      allowMultiple: true,
      withData: true,
      type: supportsImages ? FileType.any : FileType.custom,
      allowedExtensions: supportsImages ? null : _kTextOnlyExtensions,
    );
    if (result == null || result.files.isEmpty) return;
    for (final f in result.files) {
      final bytes = f.bytes;
      if (bytes == null) continue;
      final ext = f.extension?.toLowerCase() ?? '';
      _pendingAttachments.add(
        PendingAttachment(
          name: f.name,
          mimeType: _mimeForExtension(ext),
          bytes: bytes,
        ),
      );
    }
    notifyListeners();
  }

  void removeAttachment(int index) {
    _pendingAttachments.removeAt(index);
    notifyListeners();
  }

  void clearAttachments() {
    _pendingAttachments.clear();
    notifyListeners();
  }

  /// Snapshot and drain pending attachments for upload; returns the snapshot.
  List<PendingAttachment> takeAttachments() {
    final snapshot = List<PendingAttachment>.from(_pendingAttachments);
    _pendingAttachments.clear();
    notifyListeners();
    return snapshot;
  }

  Future<List<Map<String, dynamic>>?> uploadAttachments(
    List<PendingAttachment> attachments,
    WebSocketService ws,
  ) async {
    if (attachments.isEmpty) return null;
    uploadingAttachments = true;
    notifyListeners();

    try {
      final results = await Future.wait(
        attachments.map(
          (att) => ws.uploadAttachment(
            bytes: att.bytes,
            fileName: att.name,
            mimeType: att.mimeType,
          ),
        ),
      );
      return [
        for (int i = 0; i < results.length; i++)
          {
            'id': results[i]['attachment_id'] as String,
            'name': attachments[i].name,
            'mime_type': attachments[i].mimeType,
          },
      ];
    } finally {
      uploadingAttachments = false;
      notifyListeners();
    }
  }

  // ---------------------------------------------------------------------------
  // Worktree cache
  // ---------------------------------------------------------------------------

  /// Fetch worktrees for [projectPath] via [workerId].
  ///
  /// Results are cached per `workerId:projectPath`. Set [force] to bypass the
  /// cache (e.g. when the user opens the dropdown to refresh).
  /// Reset all worktree cache state — called when the active pane changes.
  void resetWorktreeCache() {
    preSessionWorktrees = null;
    _worktreeCacheKey = null;
    noGitRepo = false;
    notifyListeners();
  }

  Future<void> fetchWorktrees(
    String projectPath,
    String workerId, {
    bool force = false,
  }) async {
    final cacheKey = '$workerId:$projectPath';

    if (_worktreeCacheKey != cacheKey) {
      worktreeFetchFailed = false;
      final cached = _appState.getProjectDataCache(cacheKey);
      noGitRepo = cached?.noGitRepo ?? false;
      if (noGitRepo) {
        _worktreeCacheKey = cacheKey;
        notifyListeners();
        return;
      }
    }

    if (noGitRepo) return;

    if (!force &&
        _worktreeCacheKey == cacheKey &&
        (preSessionWorktrees != null || worktreeFetchFailed)) {
      return;
    }

    if (loadingWorktrees) return;

    loadingWorktrees = true;
    worktreeFetchFailed = false;
    if (_worktreeCacheKey != cacheKey) {
      preSessionWorktrees = null;
      _worktreeCacheKey = cacheKey;
    }
    notifyListeners();

    try {
      final ws = _appState.wsForWorker(workerId);
      final result = await ws.listWorktrees(projectPath);
      if (_worktreeCacheKey == cacheKey) {
        preSessionWorktrees =
            (result['worktrees'] as List<dynamic>? ?? [])
                .cast<Map<String, dynamic>>();
      }
    } catch (e) {
      final isNoGit = e.toString().contains('Not a git repository');
      if (isNoGit) {
        _appState.setProjectDataCache(cacheKey, noGitRepo: true);
      }
      worktreeFetchFailed = true;
      noGitRepo = isNoGit;
    } finally {
      loadingWorktrees = false;
      notifyListeners();
    }
  }

  // ---------------------------------------------------------------------------
  // MIME helpers
  // ---------------------------------------------------------------------------

  static String _mimeForExtension(String ext) {
    switch (ext) {
      case 'jpg':
      case 'jpeg':
        return 'image/jpeg';
      case 'png':
        return 'image/png';
      case 'gif':
        return 'image/gif';
      case 'webp':
        return 'image/webp';
      case 'pdf':
        return 'application/pdf';
      case 'txt':
      case 'log':
      case 'rst':
      case 'md':
        return 'text/plain';
      case 'html':
      case 'htm':
        return 'text/html';
      case 'css':
        return 'text/css';
      case 'csv':
        return 'text/csv';
      case 'json':
        return 'application/json';
      case 'yaml':
      case 'yml':
        return 'application/x-yaml';
      case 'xml':
        return 'application/xml';
      default:
        return 'text/plain';
    }
  }
}

// ---------------------------------------------------------------------------
// Text-only extension list (shown when model doesn't support images)
// ---------------------------------------------------------------------------

const List<String> _kTextOnlyExtensions = [
  'txt', 'log', 'rst', 'md', 'html', 'htm', 'css', 'csv', 'json',
  'yaml', 'yml', 'toml', 'xml', 'py', 'js', 'ts', 'jsx', 'tsx',
  'dart', 'java', 'kt', 'swift', 'go', 'rs', 'rb', 'c', 'cpp',
  'h', 'hpp', 'cs', 'php', 'scss', 'less', 'sh', 'bash', 'zsh',
  'fish', 'ps1', 'sql', 'graphql', 'proto', 'gitignore', 'env',
];
