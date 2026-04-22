/// Pinned-at-bottom list of queued user messages.
///
/// Rendered between [OutputDisplay] and [InputArea].  Each entry shows the
/// user's text with a clock icon, an edit affordance (pencil), and a cancel
/// button (x).  Entries disappear when the backend drains them; the message
/// then appears in the normal chat history at its delivered position.
///
/// See ``Queued User Messages`` in ``Design.md``.
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/ws_messages.dart';
import '../../state/pane_state.dart';

class QueuedMessagesBar extends StatelessWidget {
  const QueuedMessagesBar({super.key});

  @override
  Widget build(BuildContext context) {
    final pane = context.watch<PaneState>();
    final queue = pane.queuedMessages;
    if (queue.isEmpty) {
      return const SizedBox.shrink();
    }
    final theme = Theme.of(context);
    return Container(
      margin: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerHighest.withValues(alpha: 0.6),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(
          color: theme.colorScheme.outlineVariant.withValues(alpha: 0.5),
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.only(bottom: 4, left: 4),
            child: Text(
              queue.length == 1
                  ? '1 message queued'
                  : '${queue.length} messages queued',
              style: theme.textTheme.labelSmall?.copyWith(
                color: theme.colorScheme.onSurfaceVariant,
              ),
            ),
          ),
          for (final entry in queue)
            _QueuedMessageRow(
              key: ValueKey(entry.queuedId),
              entry: entry,
              onCancel: () => pane.cancelQueuedMessage(entry.queuedId),
              onEdit: (text) => pane.editQueuedMessage(entry.queuedId, text),
            ),
        ],
      ),
    );
  }
}

class _QueuedMessageRow extends StatefulWidget {
  final QueuedMessage entry;
  final VoidCallback onCancel;
  final ValueChanged<String> onEdit;

  const _QueuedMessageRow({
    super.key,
    required this.entry,
    required this.onCancel,
    required this.onEdit,
  });

  @override
  State<_QueuedMessageRow> createState() => _QueuedMessageRowState();
}

class _QueuedMessageRowState extends State<_QueuedMessageRow> {
  bool _editing = false;
  late final TextEditingController _controller;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController(text: widget.entry.displayContent);
  }

  @override
  void didUpdateWidget(covariant _QueuedMessageRow oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!_editing &&
        widget.entry.displayContent != oldWidget.entry.displayContent) {
      _controller.text = widget.entry.displayContent;
    }
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  void _commit() {
    final text = _controller.text.trim();
    if (text.isEmpty) {
      setState(() => _editing = false);
      _controller.text = widget.entry.displayContent;
      return;
    }
    widget.onEdit(text);
    setState(() => _editing = false);
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 4, right: 6, left: 4),
            child: Icon(
              Icons.schedule,
              size: 14,
              color: theme.colorScheme.onSurfaceVariant,
            ),
          ),
          Expanded(
            child: _editing
                ? TextField(
                    controller: _controller,
                    autofocus: true,
                    maxLines: null,
                    style: theme.textTheme.bodyMedium,
                    decoration: const InputDecoration(
                      isDense: true,
                      border: OutlineInputBorder(),
                      contentPadding: EdgeInsets.symmetric(
                        horizontal: 6,
                        vertical: 6,
                      ),
                    ),
                    onSubmitted: (_) => _commit(),
                  )
                : Padding(
                    padding: const EdgeInsets.only(top: 3),
                    child: SelectableText(
                      widget.entry.displayContent,
                      style: theme.textTheme.bodyMedium,
                    ),
                  ),
          ),
          if (_editing) ...[
            IconButton(
              icon: const Icon(Icons.check, size: 18),
              tooltip: 'Save',
              onPressed: _commit,
              visualDensity: VisualDensity.compact,
            ),
            IconButton(
              icon: const Icon(Icons.close, size: 18),
              tooltip: 'Discard',
              onPressed: () {
                _controller.text = widget.entry.displayContent;
                setState(() => _editing = false);
              },
              visualDensity: VisualDensity.compact,
            ),
          ] else ...[
            IconButton(
              icon: const Icon(Icons.edit, size: 16),
              tooltip: 'Edit',
              onPressed: () => setState(() => _editing = true),
              visualDensity: VisualDensity.compact,
            ),
            IconButton(
              icon: const Icon(Icons.close, size: 16),
              tooltip: 'Cancel',
              onPressed: widget.onCancel,
              visualDensity: VisualDensity.compact,
            ),
          ],
        ],
      ),
    );
  }
}
