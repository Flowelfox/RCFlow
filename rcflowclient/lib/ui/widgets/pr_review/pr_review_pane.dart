import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher.dart';

import '../../../models/github_pr_info.dart';
import '../../../state/app_state.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';
import '../../../theme/spacing.dart';
import '../diff/diff_viewer.dart';

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

  /// PR id the current [_files] belong to; guards against showing stale files
  /// after the pane is repointed at a different PR.
  String? _loadedPrId;

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
    }
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

    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SizedBox(width: 240, child: _buildFileList(context)),
        Container(width: 1, color: context.appColors.divider),
        Expanded(child: _buildDiffArea(context, selected, patch)),
      ],
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
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(
            kSpace3,
            kSpace2,
            kSpace3,
            kSpace1,
          ),
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
                  child: DiffViewer(diff: patch, mode: _mode),
                ),
        ),
      ],
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
