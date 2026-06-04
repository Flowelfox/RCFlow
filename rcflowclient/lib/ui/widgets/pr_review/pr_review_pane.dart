import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../models/app_notification.dart';
import '../../../models/github_pr_info.dart';
import '../../../services/websocket_service.dart';
import '../../../state/app_state.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';
import '../../../theme/spacing.dart';
import '../collapsible_group_header.dart';
import '../diff/diff_viewer.dart';
import 'comment_thread.dart';
import 'review_action_bar.dart';

/// Full-pane review view for a cached GitHub pull request.
///
/// Shows PR metadata in the header (repo/number/title/state, an open-in-GitHub
/// button, and a unified/split diff toggle), a list of changed files on the
/// left, and the selected file's diff on the right.
class PrReviewPane extends StatelessWidget {
  final String paneId;
  final PaneState pane;

  const PrReviewPane({super.key, required this.paneId, required this.pane});

  @override
  Widget build(BuildContext context) {
    final appState = context.watch<AppState>();
    final prId = pane.githubPrId;
    if (prId == null) return _emptyState(context, appState, null);

    final pr = appState.getGithubPr(prId);
    if (pr == null) return _emptyState(context, appState, null);

    return _PrReviewBody(paneId: paneId, pr: pr, appState: appState);
  }

  Widget _emptyState(
    BuildContext context,
    AppState appState,
    GithubPrInfo? pr,
  ) {
    return Column(
      children: [
        _PrReviewHeader(
          paneId: paneId,
          pr: pr,
          appState: appState,
          mode: DiffViewMode.unified,
          onModeChanged: (_) {},
        ),
        Expanded(
          child: Center(
            child: Text(
              'Pull request not found',
              style: TextStyle(color: context.appColors.textMuted),
            ),
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Body — stateful: holds the diff mode, fetched files, and selection.
// ---------------------------------------------------------------------------

class _PrReviewBody extends StatefulWidget {
  final String paneId;
  final GithubPrInfo pr;
  final AppState appState;

  const _PrReviewBody({
    required this.paneId,
    required this.pr,
    required this.appState,
  });

  @override
  State<_PrReviewBody> createState() => _PrReviewBodyState();
}

class _PrReviewBodyState extends State<_PrReviewBody> {
  DiffViewMode _mode = DiffViewMode.unified;

  /// null = not yet loaded; true/false = whether a GitHub token is configured.
  bool? _tokenConfigured;
  bool _loading = false;
  String? _error;
  List<Map<String, dynamic>> _files = [];
  int _selectedIndex = 0;

  /// File-list layout mode (flat list / directory tree / commented-only).
  FileListMode _fileListMode = FileListMode.flat;

  /// The local project the PR's repo resolves to, or null if unmapped. Loaded
  /// in [_load] and forwarded into every assist session so the session shows
  /// the project badge.
  String? _linkedProjectName;

  /// The current GitHub user's login (from the integration status), used to
  /// decide which review comments the user is allowed to delete. Null until the
  /// status check completes (or if it omits a login).
  String? _currentUserLogin;

  /// Directory paths currently collapsed in tree view (default = all expanded).
  final Set<String> _collapsedDirs = {};

  /// All review threads for the PR (across files), mapped from the backend.
  List<DiffThread> _threads = [];

  /// The local review draft event ("APPROVE"|"REQUEST_CHANGES"|"COMMENT").
  String _draftEvent = 'COMMENT';

  /// The local review draft summary body.
  String _draftBody = '';

  /// Queued (not-yet-submitted) inline comments across all files.
  List<DraftComment> _draftComments = [];

  /// PR id the current [_files] belong to; guards against showing stale files
  /// after the pane is repointed at a different PR.
  String? _loadedPrId;

  /// The diff row that currently has an open inline comment composer, or null
  /// when none is open. Lifted here so the composer renders inline inside the
  /// [DiffViewer] (GitHub-style) instead of in a popup dialog.
  ComposerAnchor? _composerAnchor;

  /// True while the open composer's comment is being POSTed to the draft.
  bool _submittingComposer = false;

  /// True while a gutter range-drag is in progress in the [DiffViewer]; the
  /// diff scroll view is frozen during it so the drag doesn't fight the scroll.
  bool _gutterDragging = false;

  /// Cache of the full head-side file content (split into lines) per file path,
  /// used to answer the diff viewer's expand-context requests without refetching.
  final Map<String, List<String>> _fileContentCache = {};

  /// Paths whose content is currently being fetched, to dedupe concurrent
  /// expand requests for the same file.
  final Set<String> _fetchingFileContent = {};

  WebSocketService? get _ws {
    final worker = widget.appState.getWorker(widget.pr.workerId);
    return worker?.ws;
  }

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(_PrReviewBody oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.pr.id != widget.pr.id) {
      _load();
    }
  }

  Future<void> _load() async {
    final worker = widget.appState.getWorker(widget.pr.workerId);
    if (worker == null || !worker.isConnected) {
      setState(() {
        _error = 'Worker not connected.';
        _loading = false;
      });
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
      _files = [];
      _threads = [];
      _draftComments = [];
      _draftEvent = 'COMMENT';
      _draftBody = '';
      _selectedIndex = 0;
      _loadedPrId = widget.pr.id;
      _composerAnchor = null;
      _submittingComposer = false;
      _gutterDragging = false;
      _linkedProjectName = null;
      _currentUserLogin = null;
      _fileContentCache.clear();
      _fetchingFileContent.clear();
    });

    // Check the GitHub integration status first so we can show a helpful hint
    // when no token is set instead of a generic error.
    try {
      final status = await worker.ws.fetchGithubStatus();
      final configured =
          status['configured'] as bool? ??
          (status['token_set'] as bool? ?? true);
      if (!mounted) return;
      setState(() {
        _tokenConfigured = configured;
        _currentUserLogin = status['login'] as String?;
      });
      if (!configured) {
        setState(() => _loading = false);
        return;
      }
    } catch (_) {
      // Status check is best-effort; fall through to the files fetch.
      if (mounted) setState(() => _tokenConfigured = true);
    }

    try {
      final result = await worker.ws.getGithubPrFiles(widget.pr.id);
      if (!mounted || _loadedPrId != widget.pr.id) return;
      final files = (result['files'] as List<dynamic>? ?? [])
          .cast<Map<String, dynamic>>();
      setState(() {
        _files = files;
        _loading = false;
        _selectedIndex = 0;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
      return;
    }

    // Resolve the PR's repo to a local project (best-effort) so assist sessions
    // opened from here carry the project and show its badge.
    try {
      final project = await worker.ws.getGithubPrProject(widget.pr.id);
      if (mounted && _loadedPrId == widget.pr.id) {
        setState(() => _linkedProjectName = project['project_name'] as String?);
      }
    } catch (_) {
      // Best-effort; leave [_linkedProjectName] null.
    }

    // Threads and the draft are best-effort; a failure here shouldn't blank
    // out the diff the user already has.
    await _refreshThreads();
    await _refreshDraft();
  }

  /// Refetch the review threads and remap them into [DiffThread]s.
  Future<void> _refreshThreads() async {
    final ws = _ws;
    if (ws == null) return;
    try {
      final result = await ws.getGithubPrThreads(widget.pr.id);
      if (!mounted || _loadedPrId != widget.pr.id) return;
      final raw = (result['threads'] as List<dynamic>? ?? [])
          .cast<Map<String, dynamic>>();
      setState(() => _threads = raw.map(_threadFromJson).toList());
    } catch (_) {
      // Best-effort; leave existing threads in place.
    }
  }

  /// Refetch the local review draft and remap its queued comments.
  Future<void> _refreshDraft() async {
    final ws = _ws;
    if (ws == null) return;
    try {
      final draft = await ws.getGithubPrDraft(widget.pr.id);
      if (!mounted || _loadedPrId != widget.pr.id) return;
      final comments = (draft['comments'] as List<dynamic>? ?? [])
          .cast<Map<String, dynamic>>();
      setState(() {
        _draftEvent = draft['event'] as String? ?? 'COMMENT';
        _draftBody = draft['body'] as String? ?? '';
        _draftComments = [
          for (var i = 0; i < comments.length; i++)
            _draftCommentFromJson(comments[i], i),
        ];
      });
    } catch (_) {
      // Best-effort.
    }
  }

  static DiffThread _threadFromJson(Map<String, dynamic> json) {
    final comments = (json['comments'] as List<dynamic>? ?? [])
        .cast<Map<String, dynamic>>();
    return DiffThread(
      threadId: json['thread_id'] as String? ?? '',
      isResolved: json['is_resolved'] as bool? ?? false,
      isOutdated: json['is_outdated'] as bool? ?? false,
      path: json['path'] as String? ?? '',
      line: (json['line'] as num?)?.toInt(),
      side: json['side'] as String? ?? 'RIGHT',
      comments: comments
          .map(
            (c) => DiffThreadComment(
              id: c['id']?.toString() ?? '',
              databaseId: (c['database_id'] as num?)?.toInt() ?? 0,
              author: c['author'] as String? ?? '',
              body: c['body'] as String? ?? '',
              createdAt: c['created_at'] as String? ?? '',
            ),
          )
          .toList(),
    );
  }

  static DraftComment _draftCommentFromJson(
    Map<String, dynamic> json,
    int index,
  ) {
    return DraftComment(
      index: index,
      path: json['path'] as String? ?? '',
      line: (json['line'] as num?)?.toInt() ?? 0,
      side: json['side'] as String? ?? 'RIGHT',
      body: json['body'] as String? ?? '',
    );
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        _PrReviewHeader(
          paneId: widget.paneId,
          pr: widget.pr,
          appState: widget.appState,
          mode: _mode,
          onModeChanged: (m) => setState(() => _mode = m),
          linkedProjectName: _linkedProjectName,
        ),
        Expanded(child: _buildBody(context)),
      ],
    );
  }

  Widget _buildBody(BuildContext context) {
    if (_tokenConfigured == false) {
      return _buildNoToken(context);
    }
    if (_loading) {
      return Center(
        child: SizedBox(
          width: 22,
          height: 22,
          child: CircularProgressIndicator(
            strokeWidth: 2,
            color: context.appColors.textMuted,
          ),
        ),
      );
    }
    if (_error != null) {
      return _buildError(context);
    }
    if (_files.isEmpty) {
      return Center(
        child: Text(
          'No changed files',
          style: TextStyle(color: context.appColors.textMuted, fontSize: 13),
        ),
      );
    }

    final selected = _files[_selectedIndex.clamp(0, _files.length - 1)];
    final patch = selected['patch'] as String?;
    final ws = _ws;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              SizedBox(width: 240, child: _buildFileList(context)),
              Container(width: 1, color: context.appColors.divider),
              Expanded(child: _buildDiffArea(context, selected, patch)),
            ],
          ),
        ),
        if (ws != null)
          ReviewActionBar(
            ws: ws,
            pr: widget.pr,
            draftEvent: _draftEvent,
            draftBody: _draftBody,
            draftComments: _draftComments,
            onSubmitted: _onReviewSubmitted,
            onMerged: _onMerged,
            onRemoveDraftComment: _removeDraftComment,
          ),
      ],
    );
  }

  /// Map a backend PR dict (returned by submit/merge) onto the cached PR via
  /// AppState so the header/state badge update in place.
  void _applyPrUpdate(Map<String, dynamic>? prJson) {
    if (prJson == null) return;
    widget.appState.upsertGithubPr(prJson, workerId: widget.pr.workerId);
  }

  Future<void> _onReviewSubmitted(Map<String, dynamic> result) async {
    _applyPrUpdate(result['pr'] as Map<String, dynamic>?);
    await _refreshThreads();
    await _refreshDraft();
  }

  Future<void> _onMerged(Map<String, dynamic> result) async {
    _applyPrUpdate(result['pr'] as Map<String, dynamic>?);
    await _refreshThreads();
  }

  Future<void> _removeDraftComment(DraftComment comment) async {
    final ws = _ws;
    if (ws == null) return;
    try {
      await ws.deleteGithubPrDraftComment(widget.pr.id, comment.index);
    } catch (_) {
      // ignore; refresh below reflects truth
    }
    await _refreshDraft();
  }

  /// Open the inline composer for a new comment anchored at ([line], [side]) of
  /// [path]. Just sets the active anchor; the composer renders inline inside the
  /// [DiffViewer] (see [_buildComposerInline]). [startLine] is set for a
  /// multi-line range comment and left null for a single line.
  void _openComposer(String path, int line, String side, {int? startLine}) {
    setState(() {
      _composerAnchor = ComposerAnchor(
        path: path,
        line: line,
        side: side,
        startLine: startLine,
      );
      _submittingComposer = false;
    });
  }

  /// Open the inline composer for a multi-line range selection spanning
  /// [startLine]..[endLine] on [side]. The composer anchors after the END row.
  void _openRangeComposer(
    String path,
    int startLine,
    int endLine,
    String side,
  ) {
    _openComposer(path, endLine, side, startLine: startLine);
  }

  /// Close the inline composer without submitting.
  void _cancelComposer() {
    setState(() {
      _composerAnchor = null;
      _submittingComposer = false;
    });
  }

  /// POST the composer's [body] to the draft, then close it and refresh.
  Future<void> _submitComposer(ComposerAnchor anchor, String body) async {
    final ws = _ws;
    if (ws == null) return;
    final trimmed = body.trim();
    if (trimmed.isEmpty) return;
    setState(() => _submittingComposer = true);
    try {
      await ws.addGithubPrDraftComment(
        widget.pr.id,
        path: anchor.path,
        line: anchor.line,
        side: anchor.side,
        body: trimmed,
        // For a range comment the backend anchors start_line..line; start_side
        // defaults to side on the backend, so we pass the (same) side.
        startLine: anchor.startLine,
        startSide: anchor.startLine != null ? anchor.side : null,
      );
      if (mounted) {
        setState(() {
          _composerAnchor = null;
          _submittingComposer = false;
        });
      }
      await _refreshDraft();
    } catch (e) {
      if (mounted) setState(() => _submittingComposer = false);
      widget.appState.showNotification(
        level: NotificationLevel.error,
        title: 'Failed to queue comment',
        body: '$e',
      );
    }
  }

  /// Answer the diff viewer's request for hidden context lines on [filename].
  ///
  /// Fetches the file's full head-side content once (cached), splits it into
  /// lines, and returns the requested inclusive 1-based NEW-side slice. Returns
  /// an empty list if the file content can't be fetched, or for an out-of-range
  /// request (which the viewer treats as "no more lines / EOF").
  Future<List<String>> _fetchExpandContext(
    String filename,
    String side,
    int startLine,
    int endLineInclusive,
  ) async {
    final ws = _ws;
    if (ws == null) return const [];

    var lines = _fileContentCache[filename];
    if (lines == null) {
      if (_fetchingFileContent.contains(filename)) return const [];
      _fetchingFileContent.add(filename);
      try {
        final result = await ws.getGithubPrFile(widget.pr.id, filename);
        final content = result['content'] as String? ?? '';
        lines = content.split('\n');
        // GitHub file content usually ends with a trailing newline, which split
        // turns into a spurious empty final element; drop it so line counts and
        // EOF detection line up with the 1-based new-side numbering.
        if (lines.isNotEmpty && lines.last.isEmpty) {
          lines.removeLast();
        }
        _fileContentCache[filename] = lines;
      } catch (_) {
        return const [];
      } finally {
        _fetchingFileContent.remove(filename);
      }
    }

    // 1-based new-side line numbers → 0-based list indices. Clamp to the file's
    // bounds; a short/empty return signals EOF to the viewer.
    final fromIdx = (startLine - 1).clamp(0, lines.length);
    final toIdx = endLineInclusive.clamp(0, lines.length);
    if (fromIdx >= toIdx) return const [];
    return lines.sublist(fromIdx, toIdx);
  }

  Widget _buildFileList(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        _buildFileListToolbar(context),
        Divider(height: 1, color: context.appColors.divider),
        Expanded(child: _buildFileListBody(context)),
      ],
    );
  }

  Widget _buildFileListBody(BuildContext context) {
    switch (_fileListMode) {
      case FileListMode.tree:
        return _buildFileTree(context);
      case FileListMode.commented:
        return _buildCommentedFileList(context);
      case FileListMode.flat:
        return _buildFlatFileList(context);
    }
  }

  /// File indices that carry at least one review thread or queued draft comment.
  /// Order follows [_files] so the rail stays stable across mode switches.
  List<int> _commentedFileIndices() {
    final commentedPaths = <String>{
      for (final t in _threads) t.path,
      for (final d in _draftComments) d.path,
    }..remove('');
    return [
      for (var i = 0; i < _files.length; i++)
        if (commentedPaths.contains(_files[i]['filename'] as String? ?? '')) i,
    ];
  }

  /// Header above the file list with the flat/tree/commented layout toggle.
  Widget _buildFileListToolbar(BuildContext context) {
    final colors = context.appColors;
    Widget toggle(FileListMode mode, IconData icon, String tooltip) {
      final selected = _fileListMode == mode;
      return SizedBox(
        width: 26,
        height: 22,
        child: IconButton(
          padding: EdgeInsets.zero,
          iconSize: 14,
          tooltip: tooltip,
          icon: Icon(
            icon,
            color: selected ? colors.accentLight : colors.textMuted,
          ),
          onPressed: () => setState(() => _fileListMode = mode),
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.fromLTRB(kSpace3, kSpace1, kSpace2, kSpace1),
      child: Row(
        children: [
          Expanded(
            child: Text(
              '${_files.length} file${_files.length == 1 ? '' : 's'}',
              style: TextStyle(
                color: colors.textMuted,
                fontSize: 10,
                fontWeight: FontWeight.w600,
                letterSpacing: 0.5,
              ),
            ),
          ),
          Container(
            decoration: BoxDecoration(
              color: colors.bgElevated,
              borderRadius: BorderRadius.circular(kRadiusSmall),
              border: Border.all(color: colors.divider, width: 0.5),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                toggle(FileListMode.flat, Icons.notes, 'Flat list'),
                toggle(FileListMode.tree, Icons.account_tree, 'Directory tree'),
                toggle(
                  FileListMode.commented,
                  Icons.mode_comment_outlined,
                  'Commented files only',
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// Flat file list — one row per file with its full path tail.
  Widget _buildFlatFileList(BuildContext context) {
    return ListView.builder(
      padding: const EdgeInsets.symmetric(vertical: kSpace1),
      itemCount: _files.length,
      itemBuilder: (context, index) {
        final file = _files[index];
        final filename = file['filename'] as String? ?? '';
        return _buildFileLeaf(
          context,
          index: index,
          label: filename.split('/').last,
          file: file,
          indent: kSpace3,
        );
      },
    );
  }

  /// Flat list of only the files that have a review thread or queued draft
  /// comment. Reuses the flat leaf rendering; shows a placeholder when none.
  Widget _buildCommentedFileList(BuildContext context) {
    final indices = _commentedFileIndices();
    if (indices.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(kSpace4),
          child: Text(
            'No commented files',
            textAlign: TextAlign.center,
            style: TextStyle(color: context.appColors.textMuted, fontSize: 11),
          ),
        ),
      );
    }
    return ListView.builder(
      padding: const EdgeInsets.symmetric(vertical: kSpace1),
      itemCount: indices.length,
      itemBuilder: (context, i) {
        final index = indices[i];
        final file = _files[index];
        final filename = file['filename'] as String? ?? '';
        return _buildFileLeaf(
          context,
          index: index,
          label: filename.split('/').last,
          file: file,
          indent: kSpace3,
        );
      },
    );
  }

  /// A selectable file row used by both flat and tree views. Selecting it
  /// points [_selectedIndex] at this file's index in [_files].
  Widget _buildFileLeaf(
    BuildContext context, {
    required int index,
    required String label,
    required Map<String, dynamic> file,
    required double indent,
  }) {
    final colors = context.appColors;
    final additions = (file['additions'] as num?)?.toInt() ?? 0;
    final deletions = (file['deletions'] as num?)?.toInt() ?? 0;
    final isSelected = index == _selectedIndex;
    return InkWell(
      onTap: () => setState(() => _selectedIndex = index),
      child: Container(
        color: isSelected ? colors.accent.withAlpha(25) : null,
        padding: EdgeInsets.only(
          left: indent,
          right: kSpace3,
          top: 6,
          bottom: 6,
        ),
        child: Row(
          children: [
            Icon(
              _statusIcon(file['status'] as String?),
              size: 13,
              color: colors.textMuted,
            ),
            const SizedBox(width: kGapInline),
            Expanded(
              child: Text(
                label,
                style: TextStyle(
                  color: isSelected ? colors.accentLight : colors.textPrimary,
                  fontSize: 11,
                  fontWeight: isSelected ? FontWeight.w600 : FontWeight.w400,
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            const SizedBox(width: kGapInline),
            Text(
              '+$additions',
              style: const TextStyle(color: Color(0xFF56D364), fontSize: 10),
            ),
            const SizedBox(width: 2),
            Text(
              '-$deletions',
              style: const TextStyle(color: Color(0xFFF85149), fontSize: 10),
            ),
          ],
        ),
      ),
    );
  }

  /// Directory-tree file list. Files are grouped into a nested tree by
  /// splitting each filename on "/"; directories are collapsible and leaves
  /// map back to the file's index in [_files] via [_buildFileLeaf].
  Widget _buildFileTree(BuildContext context) {
    final root = _buildTreeRoot();
    final rows = <Widget>[];
    _appendTreeRows(context, root, rows, depth: 0, prefix: '');
    return ListView(
      padding: const EdgeInsets.symmetric(vertical: kSpace1),
      children: rows,
    );
  }

  /// Build the in-memory directory tree from [_files] filenames. Each node maps
  /// a directory segment to either a child [_TreeNode] (directory) or, for the
  /// final segment, records the file's index in [_files] on the leaf.
  _TreeNode _buildTreeRoot() {
    final root = _TreeNode();
    for (var i = 0; i < _files.length; i++) {
      final filename = _files[i]['filename'] as String? ?? '';
      final parts = filename.split('/');
      var node = root;
      for (var p = 0; p < parts.length; p++) {
        final segment = parts[p];
        final isLeaf = p == parts.length - 1;
        if (isLeaf) {
          node.files.add(_TreeLeaf(name: segment, fileIndex: i));
        } else {
          node = node.dirs.putIfAbsent(segment, () => _TreeNode());
        }
      }
    }
    return root;
  }

  /// Recursively flatten the tree into rows: directory headers (collapsible)
  /// followed by their files when expanded.
  void _appendTreeRows(
    BuildContext context,
    _TreeNode node,
    List<Widget> rows, {
    required int depth,
    required String prefix,
  }) {
    final dirNames = node.dirs.keys.toList()..sort();
    for (final dirName in dirNames) {
      final dirPath = prefix.isEmpty ? dirName : '$prefix/$dirName';
      final collapsed = _collapsedDirs.contains(dirPath);
      final child = node.dirs[dirName]!;
      rows.add(
        CollapsibleGroupHeader(
          label: dirName,
          count: child.fileCount,
          collapsed: collapsed,
          icon: Icons.folder_outlined,
          padding: EdgeInsets.only(
            left: kSpace3 + depth * kSpace4,
            right: kSpace3,
            top: 4,
            bottom: 4,
          ),
          onToggle: () => setState(() {
            if (collapsed) {
              _collapsedDirs.remove(dirPath);
            } else {
              _collapsedDirs.add(dirPath);
            }
          }),
        ),
      );
      if (!collapsed) {
        _appendTreeRows(
          context,
          child,
          rows,
          depth: depth + 1,
          prefix: dirPath,
        );
      }
    }

    final files = node.files.toList()
      ..sort((a, b) => a.name.toLowerCase().compareTo(b.name.toLowerCase()));
    for (final leaf in files) {
      rows.add(
        _buildFileLeaf(
          context,
          index: leaf.fileIndex,
          label: leaf.name,
          file: _files[leaf.fileIndex],
          indent: kSpace3 + (depth + 1) * kSpace4,
        ),
      );
    }
  }

  Widget _buildDiffArea(
    BuildContext context,
    Map<String, dynamic> file,
    String? patch,
  ) {
    final filename = file['filename'] as String? ?? '';
    final ws = _ws;
    // Filter threads/drafts down to the file currently rendered.
    final fileThreads = _threads.where((t) => t.path == filename).toList();
    final fileDrafts = _draftComments.where((d) => d.path == filename).toList();
    // Only show the composer when it belongs to the file currently rendered.
    final composerAnchor =
        (_composerAnchor != null && _composerAnchor!.path == filename)
        ? _composerAnchor
        : null;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(
            kSpace3,
            kSpace2,
            kSpace2,
            kSpace1,
          ),
          child: Row(
            children: [
              Expanded(
                child: SelectableText(
                  filename,
                  style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 12,
                    fontFamily: 'monospace',
                    fontWeight: FontWeight.w500,
                  ),
                  maxLines: 1,
                ),
              ),
              if (patch != null && patch.isNotEmpty)
                TextButton.icon(
                  style: TextButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: kSpace2),
                    minimumSize: const Size(0, 26),
                    foregroundColor: context.appColors.textMuted,
                  ),
                  icon: const Icon(Icons.auto_awesome, size: 13),
                  label: const Text('Explain', style: TextStyle(fontSize: 11)),
                  onPressed: () => widget.appState.startPrAssist(
                    widget.paneId,
                    widget.pr,
                    'explain',
                    filePath: filename,
                    projectName: _linkedProjectName,
                  ),
                ),
            ],
          ),
        ),
        Expanded(
          child: (patch == null || patch.isEmpty)
              ? Center(
                  child: Text(
                    'No textual diff available (binary or too large)',
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 13,
                    ),
                  ),
                )
              : SingleChildScrollView(
                  // Freeze vertical scroll while a gutter range-drag is active
                  // so the drag doesn't fight the scroll view.
                  physics: _gutterDragging
                      ? const NeverScrollableScrollPhysics()
                      : null,
                  child: DiffViewer(
                    diff: patch,
                    mode: _mode,
                    threads: fileThreads,
                    draftComments: fileDrafts,
                    onAddComment: ws == null
                        ? null
                        : (line, side) => _openComposer(filename, line, side),
                    onAddRangeComment: ws == null
                        ? null
                        : (start, end, side) =>
                              _openRangeComposer(filename, start, end, side),
                    onGutterDragStart: ws == null
                        ? null
                        : () => setState(() => _gutterDragging = true),
                    onGutterDragEnd: ws == null
                        ? null
                        : () => setState(() => _gutterDragging = false),
                    onExpandContext: ws == null
                        ? null
                        : (side, start, end) =>
                              _fetchExpandContext(filename, side, start, end),
                    composerAnchor: composerAnchor,
                    composerBuilder: ws == null
                        ? null
                        : (a) => _buildComposerInline(context, a),
                    threadBuilder: ws == null
                        ? null
                        : (t) => CommentThread(
                            ws: ws,
                            prId: widget.pr.id,
                            thread: t,
                            currentUserLogin: _currentUserLogin,
                            onChanged: _refreshThreads,
                            onFix: () => widget.appState.startPrAssist(
                              widget.paneId,
                              widget.pr,
                              'fix',
                              filePath: t.path,
                              line: t.line,
                              projectName: _linkedProjectName,
                              commentBody: t.comments.isNotEmpty
                                  ? t.comments.first.body
                                  : '',
                            ),
                          ),
                    draftBuilder: (d) => _buildDraftCommentInline(context, d),
                  ),
                ),
        ),
      ],
    );
  }

  /// Inline "pending" rendering of a queued draft comment under its diff row.
  Widget _buildDraftCommentInline(BuildContext context, DraftComment d) {
    final colors = context.appColors;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.symmetric(
        horizontal: kSpace2,
        vertical: kSpace1,
      ),
      padding: const EdgeInsets.all(kSpace2),
      decoration: BoxDecoration(
        color: colors.accent.withAlpha(18),
        borderRadius: BorderRadius.circular(kRadiusSmall),
        border: Border.all(color: colors.accent.withAlpha(80)),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.pending_outlined, size: 13, color: colors.accentLight),
          const SizedBox(width: kGapInline),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Pending comment · line ${d.line} (${d.side})',
                  style: TextStyle(
                    color: colors.accentLight,
                    fontSize: 10,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 2),
                Text(
                  d.body,
                  style: TextStyle(color: colors.textSecondary, fontSize: 12),
                ),
              ],
            ),
          ),
          IconButton(
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(),
            iconSize: 14,
            splashRadius: 12,
            tooltip: 'Remove queued comment',
            icon: Icon(Icons.close, color: colors.textMuted),
            onPressed: () => _removeDraftComment(d),
          ),
        ],
      ),
    );
  }

  /// Inline comment composer rendered under the anchored diff row (GitHub-style)
  /// in place of the old popup dialog.
  Widget _buildComposerInline(BuildContext context, ComposerAnchor anchor) {
    return _InlineCommentComposer(
      // Key by anchor so switching the open row gets a fresh controller.
      key: ValueKey(
        '${anchor.path}:${anchor.startLine}:${anchor.line}:${anchor.side}',
      ),
      anchor: anchor,
      submitting: _submittingComposer,
      onCancel: _cancelComposer,
      onSubmit: (body) => _submitComposer(anchor, body),
    );
  }

  Widget _buildNoToken(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(kSpace5),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.key_off_outlined,
              color: context.appColors.textMuted,
              size: 40,
            ),
            const SizedBox(height: kGapRelaxed),
            Text(
              'GitHub token not configured',
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 15,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: kGapInline),
            Text(
              'Set GITHUB_TOKEN in Worker Settings to load pull request diffs.',
              textAlign: TextAlign.center,
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

  Widget _buildError(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(kSpace5),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              Icons.error_outline,
              color: context.appColors.textMuted,
              size: 36,
            ),
            const SizedBox(height: kGapRelaxed),
            Text(
              'Failed to load files',
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 14,
                fontWeight: FontWeight.w600,
              ),
            ),
            const SizedBox(height: kGapInline),
            Text(
              _error ?? '',
              textAlign: TextAlign.center,
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 12,
              ),
            ),
            const SizedBox(height: kSpace4),
            OutlinedButton.icon(
              onPressed: _load,
              icon: Icon(
                Icons.refresh,
                size: 16,
                color: context.appColors.textSecondary,
              ),
              label: Text(
                'Retry',
                style: TextStyle(
                  color: context.appColors.textSecondary,
                  fontSize: 13,
                ),
              ),
              style: OutlinedButton.styleFrom(
                side: BorderSide(color: context.appColors.divider),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(8),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  IconData _statusIcon(String? status) {
    switch (status) {
      case 'added':
        return Icons.add_circle_outline;
      case 'removed':
        return Icons.remove_circle_outline;
      case 'renamed':
        return Icons.drive_file_rename_outline;
      default:
        return Icons.edit_outlined;
    }
  }
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

class _PrReviewHeader extends StatelessWidget {
  final String paneId;
  final GithubPrInfo? pr;
  final AppState appState;
  final DiffViewMode mode;
  final ValueChanged<DiffViewMode> onModeChanged;

  /// The PR's auto-linked local project (or null). Forwarded into the assist
  /// sessions started from the header and shown as a small chip.
  final String? linkedProjectName;

  const _PrReviewHeader({
    required this.paneId,
    required this.pr,
    required this.appState,
    required this.mode,
    required this.onModeChanged,
    this.linkedProjectName,
  });

  @override
  Widget build(BuildContext context) {
    final isActive = appState.activePaneId == paneId;
    final pr = this.pr;

    return Container(
      height: 32,
      decoration: BoxDecoration(
        color: isActive
            ? context.appColors.accent.withAlpha(20)
            : context.appColors.bgSurface,
        border: Border(bottom: BorderSide(color: context.appColors.divider)),
      ),
      padding: const EdgeInsets.symmetric(horizontal: kSpace2),
      child: Row(
        children: [
          if (appState.panes[paneId]?.canGoBack ?? false)
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.arrow_back_rounded,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Back',
                onPressed: () => appState.goBack(paneId),
              ),
            ),
          if (isActive)
            Container(
              width: 6,
              height: 6,
              margin: const EdgeInsets.only(right: 6),
              decoration: BoxDecoration(
                color: context.appColors.accent,
                shape: BoxShape.circle,
              ),
            ),
          Icon(Icons.merge_type, size: 14, color: context.appColors.textMuted),
          const SizedBox(width: 6),
          if (pr != null) ...[
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
              decoration: BoxDecoration(
                color: context.appColors.bgElevated,
                borderRadius: BorderRadius.circular(4),
                border: Border.all(
                  color: context.appColors.divider,
                  width: 0.5,
                ),
              ),
              child: Text(
                '${pr.repoSlug} #${pr.number}',
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 10,
                  fontWeight: FontWeight.w600,
                  fontFamily: 'monospace',
                ),
              ),
            ),
            const SizedBox(width: 6),
            if (linkedProjectName != null) ...[
              _projectChip(context, linkedProjectName!),
              const SizedBox(width: 6),
            ],
          ],
          Expanded(
            child: Text(
              pr?.title ?? 'Pull Request',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 12,
                fontWeight: FontWeight.w500,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (pr != null) ...[
            _stateBadge(context, pr),
            const SizedBox(width: 6),
            _DiffModeToggle(mode: mode, onChanged: onModeChanged),
            const SizedBox(width: 6),
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.auto_awesome,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Summarise this PR (AI assist)',
                onPressed: () => appState.startPrAssist(
                  paneId,
                  pr,
                  'summary',
                  projectName: linkedProjectName,
                ),
              ),
            ),
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.add_to_queue_outlined,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Open a PR from a worktree',
                onPressed: () => _openPrFromWorktreeDialog(context, pr),
              ),
            ),
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.open_in_new,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Open on GitHub',
                onPressed: () => _openUrl(pr.url),
              ),
            ),
          ],
          // Close this pane. Closing the last pane is allowed — it returns to
          // the welcome screen.
          SizedBox(
            width: 26,
            height: 26,
            child: IconButton(
              padding: EdgeInsets.zero,
              icon: Icon(
                Icons.close_rounded,
                color: context.appColors.textMuted,
                size: 14,
              ),
              tooltip: 'Close pane',
              onPressed: () => appState.closePane(paneId),
            ),
          ),
        ],
      ),
    );
  }

  Future<void> _openUrl(String url) async {
    final uri = Uri.tryParse(url);
    if (uri == null) return;
    if (await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    }
  }

  /// Prompt for the worktree/project + PR metadata, then open a PR from that
  /// worktree via the GitHub integration REST endpoint.
  Future<void> _openPrFromWorktreeDialog(
    BuildContext context,
    GithubPrInfo pr,
  ) async {
    final pane = appState.panes[paneId];
    final worktreeController = TextEditingController();
    final projectController = TextEditingController(
      text: pane?.selectedProjectName ?? '',
    );
    final titleController = TextEditingController();
    final baseController = TextEditingController(text: 'main');
    final commitController = TextEditingController();

    final submitted = await showDialog<bool>(
      context: context,
      builder: (ctx) {
        final colors = ctx.appColors;
        InputDecoration deco(String hint) => InputDecoration(
          hintText: hint,
          isDense: true,
          filled: true,
          fillColor: colors.bgOverlay,
          contentPadding: const EdgeInsets.symmetric(
            horizontal: kSpace3,
            vertical: kSpace2,
          ),
          border: OutlineInputBorder(
            borderSide: BorderSide.none,
            borderRadius: BorderRadius.circular(kRadiusSmall),
          ),
        );
        Widget field(
          String label,
          TextEditingController controller,
          String hint,
        ) => Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              label,
              style: TextStyle(color: colors.textMuted, fontSize: 11),
            ),
            const SizedBox(height: kGapTight),
            TextField(
              controller: controller,
              style: TextStyle(color: colors.textPrimary, fontSize: 13),
              decoration: deco(hint),
            ),
            const SizedBox(height: kGapRelaxed),
          ],
        );
        return AlertDialog(
          backgroundColor: colors.bgElevated,
          title: Text(
            'Open a PR from a worktree',
            style: TextStyle(color: colors.textPrimary, fontSize: 15),
          ),
          content: SizedBox(
            width: 360,
            child: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  field(
                    'Worktree path (optional)',
                    worktreeController,
                    '/path/to/worktree',
                  ),
                  field(
                    'Project name (used if no path)',
                    projectController,
                    'my-project',
                  ),
                  field('Title', titleController, 'PR title'),
                  field('Base branch', baseController, 'main'),
                  field(
                    'Commit message (optional)',
                    commitController,
                    'Commit pending changes first',
                  ),
                ],
              ),
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: Text('Cancel', style: TextStyle(color: colors.textMuted)),
            ),
            FilledButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              style: FilledButton.styleFrom(backgroundColor: colors.accent),
              child: const Text('Open PR'),
            ),
          ],
        );
      },
    );

    if (submitted != true) return;

    final title = titleController.text.trim();
    if (title.isEmpty) {
      appState.showNotification(
        level: NotificationLevel.warning,
        title: 'A title is required to open a PR.',
      );
      return;
    }

    final ws = appState.getWorker(pr.workerId)?.ws;
    if (ws == null) {
      appState.showNotification(
        level: NotificationLevel.warning,
        title: 'Worker not connected.',
      );
      return;
    }

    final worktreePath = worktreeController.text.trim();
    final projectName = projectController.text.trim();
    final base = baseController.text.trim().isEmpty
        ? 'main'
        : baseController.text.trim();
    final commitMessage = commitController.text.trim();

    try {
      final result = await ws.openGithubPr(
        selectedWorktreePath: worktreePath.isEmpty ? null : worktreePath,
        projectName: projectName.isEmpty ? null : projectName,
        title: title,
        base: base,
        commitMessage: commitMessage.isEmpty ? null : commitMessage,
      );
      final prJson = result['pr'] as Map<String, dynamic>?;
      if (prJson != null) {
        appState.upsertGithubPr(prJson, workerId: pr.workerId);
      }
      appState.showNotification(
        level: NotificationLevel.success,
        title: 'Pull request opened',
        body: '${result['url'] ?? ''}',
      );
    } catch (e) {
      appState.showNotification(
        level: NotificationLevel.error,
        title: 'Failed to open PR',
        body: '$e',
      );
    }
  }

  /// Small muted chip showing the PR's auto-linked local project so the user
  /// can see which folder assist sessions started from here will run against.
  Widget _projectChip(BuildContext context, String name) {
    final colors = context.appColors;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
      decoration: BoxDecoration(
        color: colors.bgElevated,
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: colors.divider, width: 0.5),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(Icons.folder_outlined, size: 11, color: colors.textMuted),
          const SizedBox(width: 3),
          Text(
            name,
            style: TextStyle(
              color: colors.textMuted,
              fontSize: 10,
              fontWeight: FontWeight.w500,
            ),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
          ),
        ],
      ),
    );
  }

  Widget _stateBadge(BuildContext context, GithubPrInfo pr) {
    final (label, color) = pr.isMerged
        ? ('Merged', const Color(0xFF8B5CF6))
        : pr.state == 'closed'
        ? ('Closed', const Color(0xFFEF4444))
        : pr.draft
        ? ('Draft', const Color(0xFF6B7280))
        : ('Open', const Color(0xFF10B981));
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withAlpha(30),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: color.withAlpha(80), width: 0.5),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontSize: 10,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}

/// Compact unified/split toggle shown in the PR review header.
class _DiffModeToggle extends StatelessWidget {
  final DiffViewMode mode;
  final ValueChanged<DiffViewMode> onChanged;

  const _DiffModeToggle({required this.mode, required this.onChanged});

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: context.appColors.bgElevated,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: context.appColors.divider, width: 0.5),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          _segment(context, DiffViewMode.unified, 'Unified'),
          _segment(context, DiffViewMode.split, 'Split'),
        ],
      ),
    );
  }

  Widget _segment(BuildContext context, DiffViewMode value, String label) {
    final selected = mode == value;
    return GestureDetector(
      onTap: () => onChanged(value),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
        decoration: BoxDecoration(
          color: selected
              ? context.appColors.accent.withAlpha(40)
              : Colors.transparent,
          borderRadius: BorderRadius.circular(5),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: selected
                ? context.appColors.accentLight
                : context.appColors.textMuted,
            fontSize: 10,
            fontWeight: selected ? FontWeight.w600 : FontWeight.w400,
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Directory-tree model for the tree-view file list
// ---------------------------------------------------------------------------

/// File-list layout modes for the PR review left rail.
enum FileListMode {
  /// One row per file, full path tail.
  flat,

  /// Nested, collapsible directory tree.
  tree,

  /// Flat list of only the files that carry at least one review thread or
  /// queued draft comment.
  commented,
}

/// A leaf in the directory tree: a changed file's display name and its index
/// into the pane's `_files` list (used to map selection back).
class _TreeLeaf {
  final String name;
  final int fileIndex;

  const _TreeLeaf({required this.name, required this.fileIndex});
}

/// A directory node: nested subdirectories plus the files directly inside it.
class _TreeNode {
  final Map<String, _TreeNode> dirs = {};
  final List<_TreeLeaf> files = [];

  /// Total number of files under this node (recursively).
  int get fileCount {
    var count = files.length;
    for (final child in dirs.values) {
      count += child.fileCount;
    }
    return count;
  }
}

// ---------------------------------------------------------------------------
// Inline comment composer (rendered under a diff row by the DiffViewer)
// ---------------------------------------------------------------------------

/// A small inline composer card shown directly beneath a diff row, styled after
/// [CommentThread]'s reply composer. Owns its own [TextEditingController] so
/// typing survives parent rebuilds (e.g. when other state changes).
class _InlineCommentComposer extends StatefulWidget {
  final ComposerAnchor anchor;
  final bool submitting;
  final VoidCallback onCancel;
  final ValueChanged<String> onSubmit;

  const _InlineCommentComposer({
    super.key,
    required this.anchor,
    required this.submitting,
    required this.onCancel,
    required this.onSubmit,
  });

  @override
  State<_InlineCommentComposer> createState() => _InlineCommentComposerState();
}

class _InlineCommentComposerState extends State<_InlineCommentComposer> {
  final _controller = TextEditingController();
  final _focusNode = FocusNode();

  @override
  void initState() {
    super.initState();
    // Autofocus once the row is laid out.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _focusNode.requestFocus();
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    _focusNode.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    final anchor = widget.anchor;
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.symmetric(
        horizontal: kSpace2,
        vertical: kSpace1,
      ),
      padding: const EdgeInsets.all(kSpace3),
      decoration: BoxDecoration(
        color: colors.bgElevated,
        borderRadius: BorderRadius.circular(kRadiusMedium),
        border: Border.all(color: colors.accent.withAlpha(80)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(
                Icons.add_comment_outlined,
                size: 13,
                color: colors.accentLight,
              ),
              const SizedBox(width: kGapInline),
              Text(
                anchor.startLine != null && anchor.startLine != anchor.line
                    ? 'Commenting on lines '
                          '${anchor.startLine}–${anchor.line} (${anchor.side})'
                    : 'Comment on line ${anchor.line} (${anchor.side})',
                style: TextStyle(
                  color: colors.accentLight,
                  fontSize: 10,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ],
          ),
          const SizedBox(height: kGapTight),
          TextField(
            controller: _controller,
            focusNode: _focusNode,
            enabled: !widget.submitting,
            minLines: 2,
            maxLines: 6,
            style: TextStyle(color: colors.textPrimary, fontSize: 13),
            decoration: InputDecoration(
              hintText: 'Add a review comment…',
              isDense: true,
              contentPadding: const EdgeInsets.symmetric(
                horizontal: kSpace3,
                vertical: kSpace2,
              ),
              filled: true,
              fillColor: colors.bgOverlay,
              border: OutlineInputBorder(
                borderSide: BorderSide.none,
                borderRadius: BorderRadius.circular(kRadiusSmall),
              ),
            ),
          ),
          const SizedBox(height: kGapTight),
          Row(
            mainAxisAlignment: MainAxisAlignment.end,
            children: [
              TextButton(
                onPressed: widget.submitting ? null : widget.onCancel,
                style: TextButton.styleFrom(
                  padding: const EdgeInsets.symmetric(
                    horizontal: kSpace3,
                    vertical: kSpace1,
                  ),
                  minimumSize: Size.zero,
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
                child: Text(
                  'Cancel',
                  style: TextStyle(color: colors.textMuted, fontSize: 12),
                ),
              ),
              const SizedBox(width: kGapTight),
              FilledButton(
                onPressed: widget.submitting
                    ? null
                    : () => widget.onSubmit(_controller.text),
                style: FilledButton.styleFrom(
                  backgroundColor: colors.accent,
                  padding: const EdgeInsets.symmetric(
                    horizontal: kSpace4,
                    vertical: kSpace2,
                  ),
                  minimumSize: Size.zero,
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(kRadiusSmall),
                  ),
                ),
                child: widget.submitting
                    ? const SizedBox(
                        width: 14,
                        height: 14,
                        child: CircularProgressIndicator(
                          strokeWidth: 2,
                          color: Colors.white,
                        ),
                      )
                    : const Text('Comment', style: TextStyle(fontSize: 12)),
              ),
            ],
          ),
        ],
      ),
    );
  }
}
