import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';
import 'package:provider/provider.dart';

import '../../models/artifact_info.dart';
import '../../models/split_tree.dart';
import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';
import '../utils/markdown_copy_menu.dart';
import '../utils/selectable_code_block_builder.dart';

/// Full-pane artifact viewer.
///
/// Displays file content for text/markdown files in monospace font.
/// Shows an "unsupported" message for binary files or files exceeding 5 MB.
class ArtifactPane extends StatelessWidget {
  final String paneId;
  final PaneState pane;

  const ArtifactPane({super.key, required this.paneId, required this.pane});

  @override
  Widget build(BuildContext context) {
    final appState = context.watch<AppState>();
    final artifactId = pane.artifactId;
    if (artifactId == null) {
      return _emptyState(context, appState);
    }
    final artifact = appState.getArtifact(artifactId);
    if (artifact == null) {
      return _emptyState(context, appState);
    }

    final isActive = appState.activePaneId == paneId;
    final multiPane = appState.paneCount > 1;

    return ChangeNotifierProvider<PaneState>.value(
      value: pane,
      child: Column(
        children: [
          _ArtifactPaneHeader(
            paneId: paneId,
            artifact: artifact,
            appState: appState,
            isActive: isActive,
            multiPane: multiPane,
          ),
          Expanded(
            child: _ArtifactContent(artifact: artifact, appState: appState),
          ),
        ],
      ),
    );
  }

  Widget _emptyState(BuildContext context, AppState appState) {
    return Column(
      children: [
        _ArtifactPaneHeader(
          paneId: paneId,
          artifact: null,
          appState: appState,
          isActive: appState.activePaneId == paneId,
          multiPane: appState.paneCount > 1,
        ),
        Expanded(
          child: Center(
            child: Text(
              'Artifact not found',
              style: TextStyle(color: context.appColors.textMuted),
            ),
          ),
        ),
      ],
    );
  }
}

class _ArtifactPaneHeader extends StatelessWidget {
  final String paneId;
  final ArtifactInfo? artifact;
  final AppState appState;
  final bool isActive;
  final bool multiPane;

  const _ArtifactPaneHeader({
    required this.paneId,
    required this.artifact,
    required this.appState,
    required this.isActive,
    required this.multiPane,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 32,
      decoration: BoxDecoration(
        color: isActive
            ? context.appColors.accent.withAlpha(20)
            : context.appColors.bgSurface,
        border: Border(bottom: BorderSide(color: context.appColors.divider)),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 8),
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
          Icon(
            Icons.article_outlined,
            color: context.appColors.textMuted,
            size: 14,
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              artifact?.fileName ?? 'Artifact',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 12,
                fontWeight: FontWeight.w500,
              ),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (artifact != null)
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.delete_outline,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Delete',
                onPressed: () =>
                    _confirmDeleteArtifact(context, artifact!, appState),
              ),
            ),
          if (multiPane) ...[
            SizedBox(
              width: 26,
              height: 26,
              child: IconButton(
                padding: EdgeInsets.zero,
                icon: Icon(
                  Icons.vertical_split_outlined,
                  color: context.appColors.textMuted,
                  size: 14,
                ),
                tooltip: 'Split',
                onPressed: () =>
                    appState.splitPane(paneId, SplitAxis.horizontal),
              ),
            ),
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
          ] else
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
                tooltip: 'Close artifact view',
                onPressed: () => appState.closeArtifactView(paneId),
              ),
            ),
        ],
      ),
    );
  }

  void _confirmDeleteArtifact(
    BuildContext context,
    ArtifactInfo artifact,
    AppState appState,
  ) async {
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: context.appColors.bgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
        title: Text(
          'Delete Artifact',
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 16),
        ),
        content: Text(
          'Remove "${artifact.fileName}" from tracking? The file itself will not be deleted.',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 14,
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: Text(
              'Cancel',
              style: TextStyle(color: context.appColors.textSecondary),
            ),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: context.appColors.errorText,
            ),
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('Delete', style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
    if (confirmed != true) return;
    final worker = appState.getWorker(artifact.workerId);
    if (worker == null) return;
    try {
      await worker.ws.deleteArtifact(artifact.artifactId);
    } catch (e) {
      if (context.mounted) {
        appState.addSystemMessage(
          'Failed to delete artifact: $e',
          isError: true,
        );
      }
    }
  }
}

/// Loads and displays the file content for an artifact.
class _ArtifactContent extends StatefulWidget {
  final ArtifactInfo artifact;
  final AppState appState;

  const _ArtifactContent({required this.artifact, required this.appState});

  @override
  State<_ArtifactContent> createState() => _ArtifactContentState();
}

class _ArtifactContentState extends State<_ArtifactContent> {
  static const _maxFileSize = 5 * 1024 * 1024; // 5 MB

  String? _content;
  bool _loading = false;
  String? _error;
  bool _renderMarkdown = true;

  @override
  void initState() {
    super.initState();
    _renderMarkdown = widget.artifact.isMarkdown;
    _loadContent();
  }

  @override
  void didUpdateWidget(covariant _ArtifactContent oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.artifact.artifactId != widget.artifact.artifactId) {
      _renderMarkdown = widget.artifact.isMarkdown;
      _loadContent();
    }
  }

  Future<void> _loadContent() async {
    if (widget.artifact.fileSize > _maxFileSize) {
      setState(() {
        _content = null;
        _loading = false;
        _error =
            'File too large (${widget.artifact.displaySize}). '
            'Maximum supported size is 5 MB.';
      });
      return;
    }

    if (!widget.artifact.isTextFile && !widget.artifact.isMarkdown) {
      setState(() {
        _content = null;
        _loading = false;
        _error = 'Unsupported file type: ${widget.artifact.fileExtension}';
      });
      return;
    }

    setState(() {
      _loading = true;
      _error = null;
      _content = null;
    });

    final worker = widget.appState.getWorker(widget.artifact.workerId);
    if (worker == null) {
      setState(() {
        _loading = false;
        _error = 'Worker not connected';
      });
      return;
    }

    try {
      final content = await worker.ws.getArtifactContent(
        widget.artifact.artifactId,
      );
      if (mounted) {
        setState(() {
          _content = content;
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = 'Failed to load content: $e';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_loading) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            SizedBox(
              width: 24,
              height: 24,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: context.appColors.accent,
              ),
            ),
            const SizedBox(height: 12),
            Text(
              'Loading content...',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 13,
              ),
            ),
          ],
        ),
      );
    }

    if (_error != null) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                Icons.warning_amber_rounded,
                color: context.appColors.textMuted,
                size: 40,
              ),
              const SizedBox(height: 12),
              Text(
                _error!,
                textAlign: TextAlign.center,
                style: TextStyle(
                  color: context.appColors.textSecondary,
                  fontSize: 14,
                ),
              ),
              const SizedBox(height: 16),
              OutlinedButton(
                onPressed: _loadContent,
                style: OutlinedButton.styleFrom(
                  side: BorderSide(color: context.appColors.divider),
                ),
                child: Text(
                  'Retry',
                  style: TextStyle(color: context.appColors.textSecondary),
                ),
              ),
            ],
          ),
        ),
      );
    }

    if (_content == null) {
      return const SizedBox.shrink();
    }

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        // File info header
        Row(
          children: [
            Icon(
              widget.artifact.isMarkdown
                  ? Icons.description_outlined
                  : Icons.text_snippet_outlined,
              color: context.appColors.textMuted,
              size: 14,
            ),
            const SizedBox(width: 4),
            Expanded(
              child: Text(
                widget.artifact.filePath,
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 11,
                ),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
            if (widget.artifact.isMarkdown)
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: SizedBox(
                  height: 22,
                  child: SegmentedButton<bool>(
                    style: ButtonStyle(
                      visualDensity: VisualDensity.compact,
                      tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                      padding: WidgetStatePropertyAll(
                        EdgeInsets.symmetric(horizontal: 8),
                      ),
                      textStyle: WidgetStatePropertyAll(
                        TextStyle(fontSize: 11),
                      ),
                      iconSize: WidgetStatePropertyAll(14),
                      backgroundColor: WidgetStateProperty.resolveWith((
                        states,
                      ) {
                        if (states.contains(WidgetState.selected)) {
                          return context.appColors.accent.withAlpha(30);
                        }
                        return Colors.transparent;
                      }),
                      foregroundColor: WidgetStateProperty.resolveWith((
                        states,
                      ) {
                        if (states.contains(WidgetState.selected)) {
                          return context.appColors.accent;
                        }
                        return context.appColors.textMuted;
                      }),
                      side: WidgetStatePropertyAll(
                        BorderSide(color: context.appColors.divider),
                      ),
                    ),
                    showSelectedIcon: false,
                    segments: const [
                      ButtonSegment(value: true, label: Text('Rendered')),
                      ButtonSegment(value: false, label: Text('Raw')),
                    ],
                    selected: {_renderMarkdown},
                    onSelectionChanged: (v) =>
                        setState(() => _renderMarkdown = v.first),
                  ),
                ),
              ),
            Text(
              widget.artifact.displaySize,
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 11,
              ),
            ),
          ],
        ),
        const SizedBox(height: 12),
        // Content display
        if (_renderMarkdown && widget.artifact.isMarkdown)
          _buildRenderedMarkdown(context)
        else
          _buildRawContent(context),
      ],
    );
  }

  Widget _buildRenderedMarkdown(BuildContext context) {
    return SelectionScope(
      child: MarkdownCopyMenu(
        rawMarkdown: _content!,
        child: MarkdownBody(
          data: _content!,
          shrinkWrap: true,
          selectable: false,
          checkboxBuilder: (bool checked) => Padding(
            padding: const EdgeInsets.only(right: 6),
            child: Icon(
              checked
                  ? Icons.check_box_rounded
                  : Icons.check_box_outline_blank_rounded,
              size: 16,
              color: checked
                  ? context.appColors.accent
                  : context.appColors.textSecondary,
            ),
          ),
          builders: {
            'pre': SelectableCodeBlockBuilder(
              textStyle: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 12.5,
                fontFamily: 'monospace',
              ),
            ),
          },
          styleSheet: MarkdownStyleSheet(
            p: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 14,
              height: 1.6,
            ),
            code: TextStyle(
              color: context.appColors.textPrimary,
              backgroundColor: context.appColors.bgElevated,
              fontSize: 12.5,
              fontFamily: 'monospace',
            ),
            codeblockDecoration: BoxDecoration(
              color: context.appColors.bgElevated,
              borderRadius: BorderRadius.circular(8),
            ),
            codeblockPadding: const EdgeInsets.all(12),
            a: TextStyle(color: context.appColors.accentLight),
            listBullet: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 14,
            ),
            h1: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 24,
              fontWeight: FontWeight.bold,
            ),
            h2: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 20,
              fontWeight: FontWeight.bold,
            ),
            h3: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 17,
              fontWeight: FontWeight.bold,
            ),
            h4: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 15,
              fontWeight: FontWeight.bold,
            ),
            blockquoteDecoration: BoxDecoration(
              border: Border(
                left: BorderSide(color: context.appColors.accentDim, width: 3),
              ),
              color: context.appColors.bgElevated.withAlpha(80),
            ),
            blockquotePadding: const EdgeInsets.only(
              left: 12,
              top: 4,
              bottom: 4,
            ),
            tableBorder: TableBorder.all(color: context.appColors.divider),
            tableHead: TextStyle(
              color: context.appColors.textPrimary,
              fontWeight: FontWeight.bold,
            ),
            tableBody: TextStyle(color: context.appColors.textPrimary),
            horizontalRuleDecoration: BoxDecoration(
              border: Border(top: BorderSide(color: context.appColors.divider)),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildRawContent(BuildContext context) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: context.appColors.bgElevated,
        borderRadius: BorderRadius.circular(8),
      ),
      child: SelectableText(
        _content!,
        style: TextStyle(
          color: context.appColors.textPrimary,
          fontSize: 13,
          height: 1.5,
          fontFamily: 'monospace',
          fontFamilyFallback: const [
            'Cascadia Code',
            'Fira Code',
            'Consolas',
            'Courier New',
          ],
        ),
      ),
    );
  }
}
