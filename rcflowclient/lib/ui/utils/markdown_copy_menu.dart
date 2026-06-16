import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:html2md/html2md.dart' as html2md;
import 'package:markdown/markdown.dart' as md;
import 'package:super_clipboard/super_clipboard.dart';

/// Convert the Markdown source for [plainSelection] (recovered via
/// [extractMarkdownForSelection]) into an HTML fragment suitable for
/// the system clipboard's `text/html` slot.  Pastes into Word, Google
/// Docs, Slack, etc. then preserve native formatting (bold, lists,
/// fenced code, tables) instead of showing Markdown syntax.
/// Write both an HTML and a plain-text payload to the system clipboard via
/// [super_clipboard], so rich destinations (Word, Slack, browser contenteditable)
/// get formatting while plain destinations get the newline-preserving text.
/// Falls back to Flutter's built-in plain clipboard when the rich writer isn't
/// available on the host platform (or when [html] is empty).
Future<void> writeRichClipboard({
  required String html,
  required String plain,
}) async {
  final clipboard = SystemClipboard.instance;
  if (clipboard == null || html.isEmpty) {
    await Clipboard.setData(ClipboardData(text: plain));
    return;
  }
  final item = DataWriterItem();
  item.add(Formats.htmlText(html));
  item.add(Formats.plainText(plain));
  await clipboard.write([item]);
}

String markdownToHtmlForSelection(String rawMarkdown, String plainSelection) {
  final source = extractMarkdownForSelection(rawMarkdown, plainSelection);
  if (source.isEmpty) return '';
  return markdownSourceToHtml(source);
}

/// Convert raw Markdown to HTML using the same extension set
/// ([md.ExtensionSet.gitHubWeb]) as the renderer and the plain-text
/// helper — tables, fenced code, strike-through, autolinks all carry
/// over.  Wrapped in a `<div>` so paste targets that expect a root
/// element behave consistently.
String markdownSourceToHtml(String source) {
  final body = md.markdownToHtml(
    source,
    extensionSet: md.ExtensionSet.gitHubWeb,
  );
  if (body.isEmpty) return '';
  return '<div>$body</div>';
}

/// Convert pasted HTML to Markdown source so the chat input field
/// accepts rich text from external apps (Word, Google Docs, web
/// pages, Slack, etc.) and preserves the formatting on send.
///
/// `<script>`, `<style>`, `<meta>` blocks are stripped first — web
/// pages drop them into copy payloads and `html2md` would otherwise
/// emit them as literal text.  `&nbsp;` is collapsed to a regular
/// space.  Trailing whitespace is trimmed.
String htmlToMarkdown(String html) {
  if (html.trim().isEmpty) return '';
  final cleaned = html
      .replaceAll(RegExp(r'<script\b[^>]*>.*?</script>', dotAll: true), '')
      .replaceAll(RegExp(r'<style\b[^>]*>.*?</style>', dotAll: true), '')
      .replaceAll(RegExp(r'<meta\b[^>]*/?>', caseSensitive: false), '')
      .replaceAll(' ', ' ')
      .replaceAll('&nbsp;', ' ');
  final out = html2md.convert(
    cleaned,
    styleOptions: const {
      // Force ATX (`#`) headings rather than the Setext (=== / ---)
      // variant so multi-line pastes stay compact.
      'headingStyle': 'atx',
      // GitHub-style fenced code blocks.
      'codeBlockStyle': 'fenced',
      // Hyphen bullets so the output matches what our own
      // `markdownToPlainText` emits.
      'bulletListMarker': '-',
    },
  );
  // html2md emits bullets and ordered list markers with three trailing
  // spaces (`-   item`, `1.   item`).  Collapse to a single space so
  // the pasted Markdown reads like the rest of the message.
  return out
      .replaceAllMapped(
        RegExp(r'^(\s*[-*+])\s{2,}', multiLine: true),
        (m) => '${m[1]} ',
      )
      .replaceAllMapped(
        RegExp(r'^(\s*\d+\.)\s{2,}', multiLine: true),
        (m) => '${m[1]} ',
      )
      .trim();
}

/// Read the system clipboard and return its contents as Markdown
/// source.  Prefers the `text/html` slot via [super_clipboard]; falls
/// back to `text/plain` (and finally Flutter's plain-text clipboard)
/// so any text-only environment keeps working.
Future<String> readClipboardAsMarkdown() async {
  final clipboard = SystemClipboard.instance;
  if (clipboard != null) {
    try {
      final reader = await clipboard.read();
      if (reader.canProvide(Formats.htmlText)) {
        final html = await reader.readValue(Formats.htmlText);
        if (html != null && html.trim().isNotEmpty) {
          final md = htmlToMarkdown(html);
          if (md.isNotEmpty) return md;
        }
      }
      if (reader.canProvide(Formats.plainText)) {
        final plain = await reader.readValue(Formats.plainText);
        if (plain != null) return plain;
      }
    } catch (_) {
      // Fall through to the plain-text fallback below.  Reading the
      // super_clipboard reader can throw if the host doesn't grant
      // access (e.g. an iOS WKWebView sandbox) — Flutter's built-in
      // path is broader.
    }
  }
  final fallback = await Clipboard.getData(Clipboard.kTextPlain);
  return fallback?.text ?? '';
}

/// Recover the Markdown source for a plain-text [plainSelection] taken
/// out of [rawMarkdown] — best effort.
///
/// The chat renders Markdown via flutter_markdown which only exposes the
/// rendered visual tree to [SelectableRegion], so a copy of the user's
/// selection arrives as plain text.  This helper tries to invert that
/// rendering so the user can paste preserved formatting into a Markdown
/// editor / PR description / etc.
///
/// Strategy, in order:
///
/// 1. Direct substring match against [rawMarkdown].  Handles paragraphs
///    that contain no Markdown syntax in the selected region — the
///    common case.
/// 2. Line-by-line mapping.  Each line of [rawMarkdown] is reduced to
///    [markdownToPlainText] and matched against the selection's lines;
///    matching lines emit their Markdown source instead of the plain
///    one.  Recovers list items, headings, and emphasis when whole
///    lines are selected.
/// 3. Fallback: return [plainSelection] unchanged.  Mid-line emphasis
///    or links cannot be reliably mapped; preserving the user's text is
///    still better than dropping the copy.
String extractMarkdownForSelection(String rawMarkdown, String plainSelection) {
  final needle = plainSelection.trim();
  if (needle.isEmpty) return '';

  // 1. Line-level mapping — tried first so block syntax (`## …`,
  //    `- …`, blockquotes, etc.) is recovered whole instead of being
  //    accidentally sliced down to its plain-text rendering by the
  //    substring fast path.
  final mdLines = rawMarkdown.split('\n');
  final plainToMd = <String, String>{};
  for (final mdLine in mdLines) {
    final trimmed = mdLine.trim();
    if (trimmed.isEmpty) continue;
    final plain = markdownToPlainText(mdLine).trim();
    if (plain.isEmpty) continue;
    // First occurrence wins so duplicate plain renderings (rare) don't
    // overwrite earlier — and corresponding — Markdown sources.
    plainToMd.putIfAbsent(plain, () => mdLine);
  }

  final selectedLines = needle
      .split('\n')
      .map((l) => l.trim())
      .where((l) => l.isNotEmpty)
      .toList();

  if (selectedLines.isNotEmpty) {
    final mappedIndices = <int>[];
    var allMatched = true;
    for (final line in selectedLines) {
      final source = plainToMd[line];
      if (source == null) {
        allMatched = false;
        break;
      }
      // Look up the index of this source line in mdLines so the
      // fence-range expansion (below) has somewhere to anchor.
      mappedIndices.add(mdLines.indexOf(source));
    }
    if (allMatched && mappedIndices.isNotEmpty &&
        mappedIndices.every((i) => i >= 0)) {
      var s = mappedIndices.first;
      var e = mappedIndices.last;
      // If the matched range sits inside a fenced code block, snap
      // the slice to the surrounding ``` … ``` so paste targets see a
      // proper fenced block (and `markdownToHtmlForSelection` emits
      // `<pre><code>`).  Without this, copying the code lines alone
      // would render as paragraphs on paste.
      for (final r in _findFenceRanges(mdLines)) {
        if (r.start <= e && r.end >= s) {
          if (r.start < s) s = r.start;
          if (r.end > e) e = r.end;
        }
      }
      return mdLines.sublist(s, e + 1).join('\n');
    }
  }

  // 2. Contiguous-block match — handles structures whose plain
  //    rendering doesn't line-align with the Markdown source:
  //    tables (one rendered cell per visual row vs. one `| … | … |`
  //    Markdown line), fenced code blocks, multi-paragraph spans.
  //    For each starting line in [rawMarkdown], extend a window until
  //    the normalised plain rendering of that window contains the
  //    normalised selection.  Return the corresponding Markdown
  //    source slice (lines joined with `\n`).
  final blockHit = _findContiguousBlockMatch(rawMarkdown, mdLines, needle);
  if (blockHit != null) return blockHit;

  // 3. Substring fast path — but only when the matched range sits on a
  //    line that has no Markdown syntax of its own (plain rendering of
  //    the line equals its trimmed source).  Without this guard the
  //    selection "Section" would slice "## Section" down to "Section"
  //    and silently drop the heading marker.
  final direct = rawMarkdown.indexOf(needle);
  if (direct >= 0) {
    final lineStart = rawMarkdown.lastIndexOf('\n', direct - 1) + 1;
    var lineEnd = rawMarkdown.indexOf('\n', direct + needle.length);
    if (lineEnd < 0) lineEnd = rawMarkdown.length;
    final hostLine = rawMarkdown.substring(lineStart, lineEnd);
    if (markdownToPlainText(hostLine).trim() == hostLine.trim()) {
      return rawMarkdown.substring(direct, direct + needle.length);
    }
  }

  // 4. Fallback: plain selection unchanged.
  return plainSelection;
}

/// Returns the Markdown source for a contiguous range of lines in
/// [mdLines] whose combined plain rendering contains [needle], or null
/// if no such range exists.
///
/// Each candidate window is re-rendered through [markdownToPlainText]
/// **as a whole** rather than line-by-line, because multi-line
/// constructs (fenced code blocks, tables) only parse correctly when
/// the parser sees the surrounding fence / pipe context.
String? _findContiguousBlockMatch(
  String rawMarkdown,
  List<String> mdLines,
  String needle,
) {
  final normNeedle = _normaliseForMatch(needle);
  if (normNeedle.isEmpty) return null;
  // Whitespace-stripped needle: SelectionArea.plainText flattens blocks with
  // NO separator at the joints (e.g. "HeadingPara one.Para two."), so the
  // space-normalised match misses. Comparing with all whitespace removed makes
  // the recovery tolerant of those missing/extra joint spaces.
  final wsNeedle = _stripWhitespace(needle);

  // Pre-scan fenced code blocks so a code-line match can be expanded
  // back out to its enclosing ``` … ``` pair.
  final fenceRanges = _findFenceRanges(mdLines);

  for (var start = 0; start < mdLines.length; start++) {
    // Skip starting on a blank line — those add no rendered text and
    // just inflate the search range.
    if (mdLines[start].trim().isEmpty) continue;
    // Only return contiguous-block matches that actually span multiple
    // Markdown lines.  A single-line containment ("code and emphasis"
    // inside one paragraph) belongs to the substring fast path or the
    // plain-text fallback — over-capturing the surrounding paragraph
    // would silently widen the user's selection.
    for (var end = start + 1; end < mdLines.length; end++) {
      final candidate = mdLines.sublist(start, end + 1).join('\n');
      final plainCand = markdownToPlainText(candidate);
      final normCand = _normaliseForMatch(plainCand);
      if (normCand.contains(normNeedle) ||
          _stripWhitespace(plainCand).contains(wsNeedle)) {
        var s = start;
        var e = end;
        // Trim trailing blank lines from the slice.
        while (e > s && mdLines[e].trim().isEmpty) {
          e--;
        }
        // Pull in adjacent Markdown structure that the selection sits
        // inside but would otherwise be cut off:
        //  * a fenced-code opener / closer (``` … ```) — if any line
        //    in the match falls inside a pre-detected fence range,
        //    snap the slice to the full fence;
        //  * adjacent `|`-prefixed table rows / separator, so a
        //    body-row selection still carries the header.
        for (final r in fenceRanges) {
          if (r.start <= e && r.end >= s) {
            if (r.start < s) s = r.start;
            if (r.end > e) e = r.end;
          }
        }
        while (s > 0 && _isTableLine(mdLines[s - 1])) {
          s--;
        }
        while (e < mdLines.length - 1 && _isTableLine(mdLines[e + 1])) {
          e++;
        }
        return mdLines.sublist(s, e + 1).join('\n');
      }
      // Cheap bail: if the accumulator already vastly exceeds the
      // needle, extending further only adds more text and won't help
      // find a tighter match.
      if (_stripWhitespace(plainCand).length > wsNeedle.length * 6) break;
    }
  }
  return null;
}

/// Collapse all whitespace to a single space and lowercase, so the
/// contiguous-block match is tolerant of how the renderer or the
/// selection-area squeeze cell separators / list bullet spacing.
String _normaliseForMatch(String s) =>
    s.toLowerCase().replaceAll(RegExp(r'\s+'), ' ').trim();

/// Lowercase with **all** whitespace removed — used as a whitespace-insensitive
/// fallback so a fully flattened selection (no separators at block joints,
/// which is what SelectionArea.plainText produces) still matches its source.
String _stripWhitespace(String s) =>
    s.toLowerCase().replaceAll(RegExp(r'\s+'), '');

bool _isFenceLine(String line) => line.trimLeft().startsWith('```');

bool _isTableLine(String line) {
  final t = line.trimLeft();
  return t.startsWith('|');
}

/// Inclusive line index range, used by the fence pre-scan.
class _LineRange {
  final int start;
  final int end;
  const _LineRange(this.start, this.end);
}

/// Locate every pair of ``` … ``` fence lines in [mdLines].  A trailing
/// unclosed fence is treated as extending to the end of the document
/// so a match inside it can still be expanded out.
List<_LineRange> _findFenceRanges(List<String> mdLines) {
  final out = <_LineRange>[];
  var open = -1;
  for (var i = 0; i < mdLines.length; i++) {
    if (!_isFenceLine(mdLines[i])) continue;
    if (open < 0) {
      open = i;
    } else {
      out.add(_LineRange(open, i));
      open = -1;
    }
  }
  if (open >= 0) out.add(_LineRange(open, mdLines.length - 1));
  return out;
}

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
    case 'th':
    case 'td':
      if (children != null) writeNodes(children);
      buffer.write(' ');
      return;
    case 'tr':
      if (children != null) writeNodes(children);
      buffer.write('\n');
      return;
  }
  if (children != null) writeNodes(children);
}

/// Wraps a single rendered Markdown message so the user's "copy"
/// actions land on the system clipboard as **rich text** — HTML
/// converted from the source Markdown, with the plain rendering as a
/// fallback for paste targets that only accept plain text.
///
/// Right-click is handled by [_EagerSecondaryTapRecognizer], which
/// accepts the secondary pointer-down eagerly so the gesture arena
/// resolves in our favour *before* [SelectableRegion]'s built-in
/// secondary handler can collapse / reposition the user's selection.
/// The visible highlight stays under the popup while the menu is open.
///
/// Ctrl+C / Cmd+C is captured globally by a [HardwareKeyboard]
/// listener.  Flutter's default `CopySelectionAction` runs first and
/// writes the plain text; ~30 ms later we re-write the clipboard with
/// both the HTML payload (for rich destinations) and the plain
/// payload (for plain destinations) so any pasteable app sees the
/// best representation it supports.
class MessageSelectionArea extends StatefulWidget {
  final String rawMarkdown;
  final Widget child;

  const MessageSelectionArea({
    super.key,
    required this.rawMarkdown,
    required this.child,
  });

  @override
  State<MessageSelectionArea> createState() => _MessageSelectionAreaState();
}

class _MessageSelectionAreaState extends State<MessageSelectionArea> {
  // Live selection cache, updated by [SelectionArea.onSelectionChanged].
  String _selectionPlain = '';

  // Last *non-empty* selection.  Kept so a copy action still operates
  // on what the user had highlighted even if Flutter clears the live
  // value during menu open / focus transitions.
  String _lastNonEmpty = '';

  // The right-click copy menu, shown as an Overlay entry (NOT a route) so the
  // SelectionArea keeps focus and the highlight stays visible while it's open.
  OverlayEntry? _menuEntry;

  @override
  void initState() {
    super.initState();
    HardwareKeyboard.instance.addHandler(_onHardwareKey);
  }

  @override
  void dispose() {
    _menuEntry?.remove();
    _menuEntry = null;
    HardwareKeyboard.instance.removeHandler(_onHardwareKey);
    super.dispose();
  }

  void _dismissMenu() {
    _menuEntry?.remove();
    _menuEntry = null;
  }

  String get _activeSelection => _selectionPlain.trim().isNotEmpty
      ? _selectionPlain
      : _lastNonEmpty;

  /// Post-process the clipboard after Flutter's built-in copy.
  ///
  /// Flutter writes the plain selection to `text/plain` via the
  /// default `CopySelectionAction`.  We upgrade the same clipboard
  /// entry by adding a `text/html` payload via [super_clipboard] and
  /// rewriting the plain payload to the canonical rendered plain
  /// (otherwise pasting into a plain editor receives the raw
  /// SelectableRegion concatenation which often drops table cell
  /// separators / list bullet spacing).
  bool _onHardwareKey(KeyEvent event) {
    if (event is! KeyDownEvent) return false;
    final isCopy =
        event.logicalKey == LogicalKeyboardKey.keyC &&
        (HardwareKeyboard.instance.isControlPressed ||
            HardwareKeyboard.instance.isMetaPressed);
    if (!isCopy) return false;
    final plain = _activeSelection;
    if (plain.trim().isEmpty) return false;
    Future<void>.delayed(const Duration(milliseconds: 30), () async {
      final current = await Clipboard.getData(Clipboard.kTextPlain);
      // Guard: don't trample the clipboard if the user copied
      // something else between Flutter's write and our delayed
      // rewrite.
      final currentText = current?.text;
      final stillOurs =
          currentText != null &&
          (currentText == plain ||
              currentText.trim() == plain.trim() ||
              plain.contains(currentText));
      if (!stillOurs) return;
      // Recover the Markdown source for the selection so the *plain* payload
      // keeps its newlines/structure too — SelectionArea.plainText flattens
      // blocks into a run-on string, which is what most paste targets receive.
      final md = extractMarkdownForSelection(widget.rawMarkdown, plain);
      await _writeRich(html: markdownSourceToHtml(md), plain: md);
    });
    return false; // don't consume — Flutter's default still runs first.
  }

  /// Write both an HTML and a plain-text payload to the system
  /// clipboard via [super_clipboard].  Falls back to Flutter's
  /// built-in plain-text clipboard if the rich writer isn't available
  /// on the host platform.
  Future<void> _writeRich({required String html, required String plain}) =>
      writeRichClipboard(html: html, plain: plain);

  Future<void> _copyRich(String plain) async {
    // Use the recovered Markdown (newlines preserved) for both payloads.
    final md = extractMarkdownForSelection(widget.rawMarkdown, plain);
    await _writeRich(html: markdownSourceToHtml(md), plain: md);
  }

  Future<void> _copyMessageRich() async {
    await _writeRich(
      html: markdownSourceToHtml(widget.rawMarkdown),
      plain: markdownToPlainText(widget.rawMarkdown),
    );
  }

  Future<void> _copyPlain(String text) =>
      Clipboard.setData(ClipboardData(text: text));

  Future<void> _onMenuCopy(String selection, bool hasSelection) async {
    if (hasSelection) {
      await _copyRich(selection);
    } else {
      await _copyMessageRich();
    }
  }

  Future<void> _onMenuCopyPlain(String selection, bool hasSelection) async {
    if (hasSelection) {
      // Strip Markdown syntax but keep the newline/structure by recovering
      // the source first, then rendering it to plain text.
      final md = extractMarkdownForSelection(widget.rawMarkdown, selection);
      await _copyPlain(markdownToPlainText(md));
    } else {
      await _copyPlain(markdownToPlainText(widget.rawMarkdown));
    }
  }

  void _showRightClickMenu(BuildContext context, Offset globalPos) {
    final selection = _activeSelection;
    final hasSelection = selection.trim().isNotEmpty;
    final overlayState = Overlay.of(context);
    _dismissMenu();

    // Clamp into the screen so the menu never spills off the right / bottom.
    const menuWidth = 190.0;
    const menuHeight = 84.0;
    final screen = MediaQuery.of(context).size;
    final left = (globalPos.dx + menuWidth > screen.width)
        ? (screen.width - menuWidth - 8).clamp(0.0, screen.width)
        : globalPos.dx;
    final top = (globalPos.dy + menuHeight > screen.height)
        ? (screen.height - menuHeight - 8).clamp(0.0, screen.height)
        : globalPos.dy;

    _menuEntry = OverlayEntry(
      builder: (_) => Stack(
        children: [
          // Outside-tap dismiss layer — translucent so the chat stays visible.
          // Crucially this is an Overlay entry, not a route, so it does not
          // defocus the SelectionArea (which would wipe the highlight).
          Positioned.fill(
            child: GestureDetector(
              behavior: HitTestBehavior.translucent,
              onTap: _dismissMenu,
              onSecondaryTap: _dismissMenu,
            ),
          ),
          Positioned(
            left: left,
            top: top,
            width: menuWidth,
            child: _CopyMenu(
              onRich: () {
                _dismissMenu();
                _onMenuCopy(selection, hasSelection);
              },
              onPlain: () {
                _dismissMenu();
                _onMenuCopyPlain(selection, hasSelection);
              },
            ),
          ),
        ],
      ),
    );
    overlayState.insert(_menuEntry!);
  }

  @override
  Widget build(BuildContext context) {
    return RawGestureDetector(
      // The eager recognizer accepts the secondary pointer-down
      // immediately, winning the gesture arena over
      // SelectableRegion's TapGestureRecognizer.  SelectableRegion's
      // secondary handler — which otherwise re-positions the
      // selection to the word under the cursor on Linux/macOS, or
      // collapses it elsewhere — never fires, so the user's drag
      // selection stays visible behind our popup menu.
      behavior: HitTestBehavior.deferToChild,
      gestures: <Type, GestureRecognizerFactory>{
        _EagerSecondaryTapRecognizer:
            GestureRecognizerFactoryWithHandlers<_EagerSecondaryTapRecognizer>(
          () => _EagerSecondaryTapRecognizer(debugOwner: this),
          (instance) {
            instance.onSecondaryTapDown = (details) =>
                _showRightClickMenu(context, details.globalPosition);
          },
        ),
      },
      child: SelectionArea(
        onSelectionChanged: (content) {
          final next = content?.plainText ?? '';
          _selectionPlain = next;
          if (next.trim().isNotEmpty) {
            _lastNonEmpty = next;
          }
        },
        // Drop Flutter's default selection toolbar — our right-click
        // menu (above) replaces it for the chat use case.  Long-press
        // / drag-end selection on touch platforms still falls back to
        // Flutter's default copy if the user goes through the system
        // toolbar.
        contextMenuBuilder: (_, _) => const SizedBox.shrink(),
        child: widget.child,
      ),
    );
  }
}

/// The right-click copy popup body (rendered inside an Overlay entry).
class _CopyMenu extends StatelessWidget {
  final VoidCallback onRich;
  final VoidCallback onPlain;
  const _CopyMenu({required this.onRich, required this.onPlain});

  @override
  Widget build(BuildContext context) {
    return Material(
      elevation: 6,
      borderRadius: BorderRadius.circular(8),
      clipBehavior: Clip.antiAlias,
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          _MenuRow(
            icon: Icons.content_copy_rounded,
            label: 'Copy',
            onTap: onRich,
          ),
          _MenuRow(
            icon: Icons.text_fields_rounded,
            label: 'Copy as plain text',
            onTap: onPlain,
          ),
        ],
      ),
    );
  }
}

class _MenuRow extends StatelessWidget {
  final IconData icon;
  final String label;
  final VoidCallback onTap;
  const _MenuRow({required this.icon, required this.label, required this.onTap});

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(icon, size: 16),
            const SizedBox(width: 10),
            Text(label, style: const TextStyle(fontSize: 13)),
          ],
        ),
      ),
    );
  }
}

/// Eagerly claims secondary-button pointer events for context-menu
/// purposes, beating Flutter's [SelectableRegion] secondary-tap
/// recognizer to the gesture arena.
///
/// Standard tap recognizers wait until pointer-up before declaring
/// victory; whichever recognizer was added to the arena *first* wins
/// ties.  SelectableRegion's recognizer is always added first because
/// hit-testing walks child-to-parent and the rendered Markdown is a
/// descendant of `SelectableRegion`.  Calling
/// [resolve(GestureDisposition.accepted)] on pointer-down jumps the
/// queue: the arena resolves immediately and SelectableRegion's
/// recognizer is told to reject before its tap-down handler runs.
class _EagerSecondaryTapRecognizer extends OneSequenceGestureRecognizer {
  _EagerSecondaryTapRecognizer({super.debugOwner});

  GestureTapDownCallback? onSecondaryTapDown;

  @override
  void addAllowedPointer(PointerDownEvent event) {
    if (event.buttons != kSecondaryMouseButton) return;
    startTrackingPointer(event.pointer);
    resolve(GestureDisposition.accepted);
    final cb = onSecondaryTapDown;
    if (cb != null) {
      invokeCallback<void>(
        'onSecondaryTapDown',
        () => cb(
          TapDownDetails(
            globalPosition: event.position,
            localPosition: event.localPosition,
            kind: event.kind,
          ),
        ),
      );
    }
    stopTrackingPointer(event.pointer);
  }

  @override
  void handleEvent(PointerEvent event) {}

  @override
  void didStopTrackingLastPointer(int pointer) {}

  @override
  String get debugDescription => 'eager secondary tap';
}

