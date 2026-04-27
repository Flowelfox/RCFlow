import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:markdown/markdown.dart' as md;

/// Converts a Markdown document to its rendered plain-text equivalent —
/// stripping syntax like `##`, `**`, `` ` ``, list markers, link targets, etc.
String markdownToPlainText(String source) {
  final doc = md.Document(
    extensionSet: md.ExtensionSet.gitHubWeb,
    encodeHtml: false,
  );
  final nodes = doc.parseLines(source.split('\n'));
  final buffer = StringBuffer();

  void writeNodes(List<md.Node> list) {
    for (var i = 0; i < list.length; i++) {
      final node = list[i];
      _writeNode(node, buffer, writeNodes);
      if (i < list.length - 1 && _isBlock(node)) buffer.write('\n');
    }
  }

  writeNodes(nodes);
  return buffer.toString().replaceAll(RegExp(r'\n{3,}'), '\n\n').trim();
}

bool _isBlock(md.Node node) {
  if (node is! md.Element) return false;
  const blockTags = {
    'p',
    'h1',
    'h2',
    'h3',
    'h4',
    'h5',
    'h6',
    'blockquote',
    'pre',
    'ul',
    'ol',
    'hr',
    'table',
  };
  return blockTags.contains(node.tag);
}

void _writeNode(
  md.Node node,
  StringBuffer buffer,
  void Function(List<md.Node>) writeNodes,
) {
  if (node is md.Text) {
    buffer.write(node.text);
    return;
  }
  if (node is! md.Element) return;
  final children = node.children;
  switch (node.tag) {
    case 'hr':
      buffer.write('---');
      return;
    case 'br':
      buffer.write('\n');
      return;
    case 'li':
      buffer.write('- ');
      if (children != null) writeNodes(children);
      buffer.write('\n');
      return;
    case 'code':
    case 'pre':
      if (children != null) writeNodes(children);
      return;
  }
  if (children != null) writeNodes(children);
}

/// Wraps a [SelectionArea] and tracks its current plain-text selection so
/// descendants (e.g. [MarkdownCopyMenu]) can read it when the user right-clicks.
///
/// [SelectableRegionState] keeps its `selectedContent` private, so we cache the
/// latest selection via the public `onSelectionChanged` callback instead. The
/// last non-empty value is also retained — a right-click inside the selection
/// makes [SelectableRegion] fire `onSelectionChanged(null)` before our menu
/// handler runs, so reading the live value would lose the user's selection.
class SelectionScope extends StatefulWidget {
  final Widget child;

  const SelectionScope({super.key, required this.child});

  /// Returns the most recent non-empty plain-text selection tracked by the
  /// nearest ancestor [SelectionScope], or an empty string if none has been
  /// made. Used by right-click handlers that need the user's selection even
  /// after [SelectableRegion] has just cleared it as a side effect of the
  /// pointer-down or focus loss when the menu opens.
  static String currentSelection(BuildContext context) {
    final scope = context
        .getInheritedWidgetOfExactType<_SelectionScopeProvider>();
    return scope?.state._lastNonEmpty ?? '';
  }

  @override
  State<SelectionScope> createState() => _SelectionScopeState();
}

class _SelectionScopeState extends State<SelectionScope> {
  String _lastNonEmpty = '';

  @override
  Widget build(BuildContext context) {
    return _SelectionScopeProvider(
      state: this,
      child: SelectionArea(
        onSelectionChanged: (content) {
          final text = content?.plainText ?? '';
          if (text.trim().isNotEmpty) _lastNonEmpty = text;
        },
        child: widget.child,
      ),
    );
  }
}

class _SelectionScopeProvider extends InheritedWidget {
  final _SelectionScopeState state;

  const _SelectionScopeProvider({required this.state, required super.child});

  // Subscribers read state on demand; no rebuild-on-change.
  @override
  bool updateShouldNotify(_SelectionScopeProvider oldWidget) => false;
}

/// Wraps [child] with a right-click context menu offering both a plain-text
/// and a Markdown-source copy of [rawMarkdown].
///
/// When placed under a [SelectionScope] and the user has an active text
/// selection at the moment of the right-click, the copy actions operate on
/// that selection instead of on the full [rawMarkdown].
class MarkdownCopyMenu extends StatelessWidget {
  final String rawMarkdown;
  final Widget child;

  const MarkdownCopyMenu({
    super.key,
    required this.rawMarkdown,
    required this.child,
  });

  Future<void> _showMenu(BuildContext context, Offset globalPos) async {
    // Snapshot the selection before the menu overlay steals focus and
    // triggers `SelectionArea` to clear it.
    final selection = SelectionScope.currentSelection(context);
    final hasSelection = selection.trim().isNotEmpty;

    final overlay =
        Overlay.of(context).context.findRenderObject() as RenderBox?;
    if (overlay == null) return;
    final position = RelativeRect.fromRect(
      Rect.fromPoints(globalPos, globalPos),
      Offset.zero & overlay.size,
    );
    final choice = await showMenu<_CopyMode>(
      context: context,
      position: position,
      items: [
        PopupMenuItem(
          value: _CopyMode.plain,
          height: 36,
          child: Row(
            children: [
              const Icon(Icons.content_copy_rounded, size: 16),
              const SizedBox(width: 10),
              Text(
                hasSelection ? 'Copy selection' : 'Copy',
                style: const TextStyle(fontSize: 13),
              ),
            ],
          ),
        ),
        PopupMenuItem(
          value: _CopyMode.markdown,
          height: 36,
          child: Row(
            children: [
              const Icon(Icons.code_rounded, size: 16),
              const SizedBox(width: 10),
              Text(
                hasSelection
                    ? 'Copy selection as Markdown'
                    : 'Copy as Markdown',
                style: const TextStyle(fontSize: 13),
              ),
            ],
          ),
        ),
      ],
    );
    if (choice == null) return;
    final text = switch (choice) {
      _CopyMode.plain =>
        hasSelection ? selection : markdownToPlainText(rawMarkdown),
      _CopyMode.markdown => hasSelection ? selection : rawMarkdown,
    };
    await Clipboard.setData(ClipboardData(text: text));
  }

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      behavior: HitTestBehavior.deferToChild,
      onSecondaryTapDown: (details) =>
          _showMenu(context, details.globalPosition),
      child: child,
    );
  }
}

enum _CopyMode { plain, markdown }
