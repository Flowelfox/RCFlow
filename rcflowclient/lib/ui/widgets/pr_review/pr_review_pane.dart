import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../models/github_pr_info.dart';
import '../../../services/websocket_service.dart';
import '../../../state/app_state.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';
import '../../../theme/spacing.dart';
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
    });

    // Check the GitHub integration status first so we can show a helpful hint
    // when no token is set instead of a generic error.
    try {
      final status = await worker.ws.fetchGithubStatus();
      final configured =
          status['configured'] as bool? ??
          (status['token_set'] as bool? ?? true);
      if (!mounted) return;
      setState(() => _tokenConfigured = configured);
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

  /// Open a composer for a new inline comment anchored at ([line], [side]) of
  /// the currently-selected file, POST it to the draft, then refresh.
  Future<void> _addCommentFor(String path, int line, String side) async {
    final ws = _ws;
    if (ws == null) return;
    final messenger = ScaffoldMessenger.maybeOf(context);
    final body = await _promptForComment(context, path, line, side);
    if (body == null || body.trim().isEmpty) return;
    try {
      await ws.addGithubPrDraftComment(
        widget.pr.id,
        path: path,
        line: line,
        side: side,
        body: body.trim(),
      );
      await _refreshDraft();
    } catch (e) {
      messenger?.showSnackBar(
        SnackBar(content: Text('Failed to queue comment: $e')),
      );
    }
  }

  Future<String?> _promptForComment(
    BuildContext context,
    String path,
    int line,
    String side,
  ) {
    final controller = TextEditingController();
    return showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: ctx.appColors.bgElevated,
        title: Text(
          'Comment on line $line',
          style: TextStyle(color: ctx.appColors.textPrimary, fontSize: 15),
        ),
        content: TextField(
          controller: controller,
          autofocus: true,
          minLines: 2,
          maxLines: 6,
          style: TextStyle(color: ctx.appColors.textPrimary, fontSize: 13),
          decoration: InputDecoration(
            hintText: 'Add a review comment…',
            filled: true,
            fillColor: ctx.appColors.bgOverlay,
            border: OutlineInputBorder(
              borderSide: BorderSide.none,
              borderRadius: BorderRadius.circular(kRadiusSmall),
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child: Text(
              'Cancel',
              style: TextStyle(color: ctx.appColors.textMuted),
            ),
          ),
          FilledButton(
            onPressed: () => Navigator.of(ctx).pop(controller.text),
            style: FilledButton.styleFrom(
              backgroundColor: ctx.appColors.accent,
            ),
            child: const Text('Queue comment'),
          ),
        ],
      ),
    );
  }

  Widget _buildFileList(BuildContext context) {
    return ListView.builder(
      padding: const EdgeInsets.symmetric(vertical: kSpace1),
      itemCount: _files.length,
      itemBuilder: (context, index) {
        final file = _files[index];
        final filename = file['filename'] as String? ?? '';
        final additions = (file['additions'] as num?)?.toInt() ?? 0;
        final deletions = (file['deletions'] as num?)?.toInt() ?? 0;
        final isSelected = index == _selectedIndex;
        return InkWell(
          onTap: () => setState(() => _selectedIndex = index),
          child: Container(
            color: isSelected ? context.appColors.accent.withAlpha(25) : null,
            padding: const EdgeInsets.symmetric(
              horizontal: kSpace3,
              vertical: 6,
            ),
            child: Row(
              children: [
                Icon(
                  _statusIcon(file['status'] as String?),
                  size: 13,
                  color: context.appColors.textMuted,
                ),
                const SizedBox(width: kGapInline),
                Expanded(
                  child: Text(
                    filename.split('/').last,
                    style: TextStyle(
                      color: isSelected
                          ? context.appColors.accentLight
                          : context.appColors.textPrimary,
                      fontSize: 11,
                      fontWeight: isSelected
                          ? FontWeight.w600
                          : FontWeight.w400,
                    ),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                const SizedBox(width: kGapInline),
                Text(
                  '+$additions',
                  style: const TextStyle(
                    color: Color(0xFF56D364),
                    fontSize: 10,
                  ),
                ),
                const SizedBox(width: 2),
                Text(
                  '-$deletions',
                  style: const TextStyle(
                    color: Color(0xFFF85149),
                    fontSize: 10,
                  ),
                ),
              ],
            ),
          ),
        );
      },
    );
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
                  child: DiffViewer(
                    diff: patch,
                    mode: _mode,
                    threads: fileThreads,
                    draftComments: fileDrafts,
                    onAddComment: ws == null
                        ? null
                        : (line, side) => _addCommentFor(filename, line, side),
                    threadBuilder: ws == null
                        ? null
                        : (t) => CommentThread(
                            ws: ws,
                            prId: widget.pr.id,
                            thread: t,
                            onChanged: _refreshThreads,
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

  const _PrReviewHeader({
    required this.paneId,
    required this.pr,
    required this.appState,
    required this.mode,
    required this.onModeChanged,
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
                onPressed: () => appState.startPrAssist(paneId, pr, 'summary'),
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
          if (appState.paneCount > 1)
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
                tooltip: 'Close',
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
