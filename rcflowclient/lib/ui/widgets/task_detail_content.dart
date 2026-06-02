part of 'task_pane.dart';

class _TaskDetailContent extends StatefulWidget {
  final String paneId;
  final TaskInfo task;
  final AppState appState;

  const _TaskDetailContent({
    super.key,
    required this.paneId,
    required this.task,
    required this.appState,
  });

  @override
  State<_TaskDetailContent> createState() => _TaskDetailContentState();
}

class _TaskDetailContentState extends State<_TaskDetailContent> {
  bool _editingTitle = false;
  bool _editingDescription = false;
  late TextEditingController _titleCtrl;
  late TextEditingController _descCtrl;
  final _titleFocus = FocusNode();
  final _descFocus = FocusNode();

  static const _statusLabels = {
    'todo': 'To Do',
    'in_progress': 'In Progress',
    'review': 'Review',
    'done': 'Done',
  };

  static const _statusColors = {
    'todo': Color(0xFF6B7280),
    'in_progress': Color(0xFF3B82F6),
    'review': Color(0xFFF59E0B),
    'done': Color(0xFF10B981),
  };

  @override
  void initState() {
    super.initState();
    _titleCtrl = TextEditingController(text: widget.task.title);
    _descCtrl = TextEditingController(text: widget.task.description ?? '');
  }

  @override
  void didUpdateWidget(covariant _TaskDetailContent oldWidget) {
    super.didUpdateWidget(oldWidget);
    // Sync controllers if task data changed externally and we're not editing
    if (oldWidget.task.taskId != widget.task.taskId) {
      _exitEditMode();
      _titleCtrl.text = widget.task.title;
      _descCtrl.text = widget.task.description ?? '';
    } else {
      if (!_editingTitle) _titleCtrl.text = widget.task.title;
      if (!_editingDescription) {
        _descCtrl.text = widget.task.description ?? '';
      }
    }
  }

  @override
  void dispose() {
    _titleCtrl.dispose();
    _descCtrl.dispose();
    _titleFocus.dispose();
    _descFocus.dispose();
    super.dispose();
  }

  /// Called from the header edit button via GlobalKey.
  void enterEditMode() {
    setState(() {
      _editingTitle = true;
      _editingDescription = true;
      _titleCtrl.text = widget.task.title;
      _descCtrl.text = widget.task.description ?? '';
    });
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _titleFocus.requestFocus();
    });
  }

  void _exitEditMode() {
    setState(() {
      _editingTitle = false;
      _editingDescription = false;
    });
  }

  void _cancelEditing() {
    _titleCtrl.text = widget.task.title;
    _descCtrl.text = widget.task.description ?? '';
    _exitEditMode();
  }

  Future<void> _saveChanges() async {
    final newTitle = _titleCtrl.text.trim();
    final newDesc = _descCtrl.text.trim();

    if (newTitle.isEmpty) return; // title is required

    final titleChanged = newTitle != widget.task.title;
    final descChanged = newDesc != (widget.task.description ?? '');

    if (!titleChanged && !descChanged) {
      _exitEditMode();
      return;
    }

    final worker = widget.appState.getWorker(widget.task.workerId);
    if (worker == null) return;

    try {
      await worker.ws.updateTask(
        widget.task.taskId,
        title: titleChanged ? newTitle : null,
        description: descChanged ? newDesc : null,
      );
    } catch (e) {
      if (mounted) {
        widget.appState.addSystemMessage(
          'Failed to update task: $e',
          isError: true,
        );
      }
    }
    if (mounted) _exitEditMode();
  }

  bool get _isEditing => _editingTitle || _editingDescription;

  @override
  Widget build(BuildContext context) {
    final task = widget.task;
    final appState = widget.appState;
    final statusColor =
        _statusColors[task.status] ?? context.appColors.textMuted;

    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        // Title — inline editable on double-click
        _editingTitle
            ? _buildTitleEditor(context)
            : SelectableText(
                task.title,
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 20,
                  fontWeight: FontWeight.w700,
                ),
              ),
        const SizedBox(height: 12),

        // Status chip row
        Row(
          children: [
            _StatusChip(
              label: _statusLabels[task.status] ?? task.status,
              color: statusColor,
              current: true,
            ),
            const SizedBox(width: 8),
            ..._buildTransitionChips(context),
          ],
        ),
        const SizedBox(height: 8),

        // Metadata
        Row(
          children: [
            Icon(
              task.source == 'ai'
                  ? Icons.smart_toy_outlined
                  : Icons.person_outline,
              color: context.appColors.textMuted,
              size: 14,
            ),
            const SizedBox(width: 4),
            Text(
              'Created by ${task.source == 'ai' ? 'AI' : 'User'} \u00B7 ${_formatDate(task.createdAt)}',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 12,
              ),
            ),
          ],
        ),
        const SizedBox(height: 16),

        // Description — inline editable on double-click
        _editingDescription
            ? _buildDescriptionEditor(context)
            : (task.description != null && task.description!.isNotEmpty)
            ? Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    'Description',
                    style: TextStyle(
                      color: context.appColors.textSecondary,
                      fontSize: 12,
                      fontWeight: FontWeight.w600,
                      letterSpacing: 0.5,
                    ),
                  ),
                  const SizedBox(height: 6),
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: context.appColors.bgElevated,
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: MessageSelectionArea(
                      rawMarkdown: task.description!,
                      child: MarkdownBody(
                          data: task.description!,
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
                              fontSize: 13,
                              height: 1.5,
                            ),
                            code: TextStyle(
                              color: context.appColors.textPrimary,
                              backgroundColor: context.appColors.toolBg
                                  .withValues(alpha: 0.6),
                              fontSize: 12.5,
                              fontFamily: 'monospace',
                            ),
                            codeblockDecoration: BoxDecoration(
                              color: context.appColors.toolBg,
                              borderRadius: BorderRadius.circular(8),
                            ),
                            codeblockPadding: EdgeInsets.all(12),
                            a: TextStyle(color: context.appColors.accentLight),
                            listBullet: TextStyle(
                              color: context.appColors.textPrimary,
                              fontSize: 13,
                            ),
                            h1: TextStyle(
                              color: context.appColors.textPrimary,
                              fontSize: 20,
                              fontWeight: FontWeight.bold,
                            ),
                            h2: TextStyle(
                              color: context.appColors.textPrimary,
                              fontSize: 18,
                              fontWeight: FontWeight.bold,
                            ),
                            h3: TextStyle(
                              color: context.appColors.textPrimary,
                              fontSize: 16,
                              fontWeight: FontWeight.bold,
                            ),
                            blockquoteDecoration: BoxDecoration(
                              border: Border(
                                left: BorderSide(
                                  color: context.appColors.accentDim,
                                  width: 3,
                                ),
                              ),
                              color: context.appColors.toolBg.withValues(
                                alpha: 0.3,
                              ),
                            ),
                            blockquotePadding: EdgeInsets.only(
                              left: 12,
                              top: 4,
                              bottom: 4,
                            ),
                            tableBorder: TableBorder.all(
                              color: context.appColors.divider,
                            ),
                            tableHead: TextStyle(
                              color: context.appColors.textPrimary,
                              fontWeight: FontWeight.bold,
                            ),
                            tableBody: TextStyle(
                              color: context.appColors.textPrimary,
                            ),
                            horizontalRuleDecoration: BoxDecoration(
                              border: Border(
                                top: BorderSide(
                                  color: context.appColors.divider,
                                ),
                              ),
                            ),
                          ),
                        ),
                      ),
                    ),
                  const SizedBox(height: 16),
                ],
              )
            : Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  GestureDetector(
                    onDoubleTap: () {
                      setState(() {
                        _editingDescription = true;
                        _descCtrl.text = '';
                      });
                      WidgetsBinding.instance.addPostFrameCallback((_) {
                        _descFocus.requestFocus();
                      });
                    },
                    child: Tooltip(
                      message: 'Double-click to add description',
                      waitDuration: const Duration(milliseconds: 600),
                      child: Container(
                        width: double.infinity,
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: context.appColors.bgElevated,
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(
                            color: context.appColors.divider,
                            style: BorderStyle.solid,
                          ),
                        ),
                        child: Text(
                          'Add description...',
                          style: TextStyle(
                            color: context.appColors.textMuted,
                            fontSize: 13,
                            fontStyle: FontStyle.italic,
                          ),
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                ],
              ),

        // Save / Cancel row when editing
        if (_isEditing) ...[
          const SizedBox(height: 8),
          Row(
            children: [
              Text(
                'Ctrl+Enter to save \u00B7 Esc to cancel',
                style: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 11,
                ),
              ),
              const Spacer(),
              TextButton(
                onPressed: _cancelEditing,
                child: Text(
                  'Cancel',
                  style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 13,
                  ),
                ),
              ),
              const SizedBox(width: 6),
              FilledButton(
                style: FilledButton.styleFrom(
                  backgroundColor: context.appColors.accent,
                  padding: const EdgeInsets.symmetric(
                    horizontal: 16,
                    vertical: 8,
                  ),
                ),
                onPressed: _saveChanges,
                child: const Text(
                  'Save',
                  style: TextStyle(color: Colors.white, fontSize: 13),
                ),
              ),
            ],
          ),
          const SizedBox(height: 16),
        ],

        // Action buttons
        Row(
          children: [
            FilledButton.icon(
              onPressed: () =>
                  appState.startSessionFromTask(widget.paneId, task),
              icon: const Icon(Icons.play_arrow_rounded, size: 18),
              label: const Text('Start Session'),
              style: FilledButton.styleFrom(
                backgroundColor: context.appColors.accent,
                foregroundColor: Colors.white,
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(kRadiusMedium),
                ),
                padding: const EdgeInsets.symmetric(
                  horizontal: 14,
                  vertical: 10,
                ),
                textStyle: const TextStyle(fontSize: 13),
              ),
            ),
            const SizedBox(width: 8),
            if (task.planArtifactId == null)
              OutlinedButton.icon(
                onPressed: () => appState.startPlanSession(widget.paneId, task),
                icon: const Icon(Icons.auto_awesome_outlined, size: 18),
                label: const Text('Make Plan'),
                style: OutlinedButton.styleFrom(
                  side: BorderSide(color: context.appColors.divider),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(kRadiusMedium),
                  ),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 14,
                    vertical: 10,
                  ),
                  textStyle: const TextStyle(fontSize: 13),
                ),
              ),
            const SizedBox(width: 8),
            if (task.status != 'done')
              OutlinedButton.icon(
                onPressed: () => _updateStatus(context, 'done'),
                icon: Icon(
                  Icons.check_circle_outline,
                  size: 18,
                  color: const Color(0xFF10B981),
                ),
                label: Text(
                  'Mark Complete',
                  style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 13,
                  ),
                ),
                style: OutlinedButton.styleFrom(
                  side: BorderSide(color: context.appColors.divider),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(kRadiusMedium),
                  ),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 14,
                    vertical: 10,
                  ),
                ),
              )
            else
              OutlinedButton.icon(
                onPressed: () => _updateStatus(context, 'todo'),
                icon: Icon(
                  Icons.replay_rounded,
                  size: 18,
                  color: context.appColors.textSecondary,
                ),
                label: Text(
                  'Reopen',
                  style: TextStyle(
                    color: context.appColors.textSecondary,
                    fontSize: 13,
                  ),
                ),
                style: OutlinedButton.styleFrom(
                  side: BorderSide(color: context.appColors.divider),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(kRadiusMedium),
                  ),
                  padding: const EdgeInsets.symmetric(
                    horizontal: 14,
                    vertical: 10,
                  ),
                ),
              ),
          ],
        ),
        const SizedBox(height: 16),

        // Plan banner
        if (task.planArtifactId != null) ...[
          _PlanBanner(task: task, appState: appState, paneId: widget.paneId),
          const SizedBox(height: 16),
        ],

        // Linked sessions
        Text(
          'Linked Sessions (${task.sessions.length})',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 12,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.5,
          ),
        ),
        const SizedBox(height: 8),
        if (task.sessions.isEmpty)
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: context.appColors.bgElevated,
              borderRadius: BorderRadius.circular(8),
            ),
            child: Text(
              'No sessions linked yet',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 13,
              ),
            ),
          )
        else
          ...task.sessions.map(
            (ref) => _SessionRefTile(
              ref: ref,
              appState: appState,
              taskId: task.taskId,
              workerId: task.workerId,
            ),
          ),
        const SizedBox(height: 20),

        // Linked Linear issues
        _buildLinkedIssuesSection(context, appState, task),
      ],
    );
  }

  Widget _buildTitleEditor(BuildContext context) {
    return KeyboardListener(
      focusNode: FocusNode(),
      onKeyEvent: (event) {
        if (event is KeyDownEvent) {
          if (event.logicalKey == LogicalKeyboardKey.escape) {
            _cancelEditing();
          } else if (event.logicalKey == LogicalKeyboardKey.enter &&
              !HardwareKeyboard.instance.isShiftPressed) {
            if (HardwareKeyboard.instance.isControlPressed) {
              _saveChanges();
            } else {
              // Enter in title field → save
              _saveChanges();
            }
          }
        }
      },
      child: TextField(
        controller: _titleCtrl,
        focusNode: _titleFocus,
        maxLength: 300,
        style: TextStyle(
          color: context.appColors.textPrimary,
          fontSize: 20,
          fontWeight: FontWeight.w700,
        ),
        decoration: InputDecoration(
          isDense: true,
          counterText: '',
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 8,
            vertical: 6,
          ),
          fillColor: context.appColors.bgElevated,
          filled: true,
          border: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: BorderSide(color: context.appColors.accent),
          ),
          focusedBorder: OutlineInputBorder(
            borderRadius: BorderRadius.circular(8),
            borderSide: BorderSide(color: context.appColors.accent, width: 1.5),
          ),
        ),
      ),
    );
  }

  Widget _buildDescriptionEditor(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Description',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 12,
            fontWeight: FontWeight.w600,
            letterSpacing: 0.5,
          ),
        ),
        const SizedBox(height: 6),
        KeyboardListener(
          focusNode: FocusNode(),
          onKeyEvent: (event) {
            if (event is KeyDownEvent) {
              if (event.logicalKey == LogicalKeyboardKey.escape) {
                _cancelEditing();
              } else if (event.logicalKey == LogicalKeyboardKey.enter &&
                  HardwareKeyboard.instance.isControlPressed) {
                _saveChanges();
              }
            }
          },
          child: TextField(
            controller: _descCtrl,
            focusNode: _descFocus,
            minLines: 3,
            maxLines: 8,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 13,
              height: 1.5,
            ),
            decoration: InputDecoration(
              hintText: 'Add description...',
              hintStyle: TextStyle(color: context.appColors.textMuted),
              contentPadding: const EdgeInsets.all(12),
              fillColor: context.appColors.bgElevated,
              filled: true,
              border: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: BorderSide(color: context.appColors.accent),
              ),
              focusedBorder: OutlineInputBorder(
                borderRadius: BorderRadius.circular(8),
                borderSide: BorderSide(
                  color: context.appColors.accent,
                  width: 1.5,
                ),
              ),
            ),
          ),
        ),
        const SizedBox(height: 16),
      ],
    );
  }

  List<Widget> _buildTransitionChips(BuildContext context) {
    final transitions = <String, String>{
      'todo': 'To Do',
      'in_progress': 'In Progress',
      'review': 'Review',
      'done': 'Done',
    };
    // Only show valid transitions
    final valid = _validTransitions[widget.task.status] ?? {};
    return valid.map((status) {
      return Padding(
        padding: const EdgeInsets.only(right: 4),
        child: _StatusChip(
          label: transitions[status] ?? status,
          color: _statusColors[status] ?? context.appColors.textMuted,
          current: false,
          onTap: () => _updateStatus(context, status),
        ),
      );
    }).toList();
  }

  static const _validTransitions = {
    'todo': {'in_progress', 'done'},
    'in_progress': {'todo', 'review', 'done'},
    'review': {'in_progress', 'done'},
    'done': {'todo', 'in_progress'},
  };

  void _updateStatus(BuildContext context, String newStatus) async {
    final worker = widget.appState.getWorker(widget.task.workerId);
    if (worker == null) return;
    try {
      await worker.ws.updateTask(widget.task.taskId, status: newStatus);
    } catch (e) {
      if (context.mounted) {
        widget.appState.addSystemMessage(
          'Failed to update task: $e',
          isError: true,
        );
      }
    }
  }

  String _formatDate(DateTime dt) {
    final local = dt.toLocal();
    final months = [
      'Jan',
      'Feb',
      'Mar',
      'Apr',
      'May',
      'Jun',
      'Jul',
      'Aug',
      'Sep',
      'Oct',
      'Nov',
      'Dec',
    ];
    return '${months[local.month - 1]} ${local.day}, '
        '${local.hour.toString().padLeft(2, '0')}:'
        '${local.minute.toString().padLeft(2, '0')}';
  }

  Widget _buildLinkedIssuesSection(
    BuildContext context,
    AppState appState,
    TaskInfo task,
  ) {
    final linkedIssues = appState.linearIssuesForTask(task.taskId);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Text(
              'Linked Issues (${linkedIssues.length})',
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 12,
                fontWeight: FontWeight.w600,
                letterSpacing: 0.5,
              ),
            ),
            const Spacer(),
            SizedBox(
              height: 24,
              child: TextButton.icon(
                onPressed: () => _showLinkIssuePicker(context, appState, task),
                icon: Icon(
                  Icons.add_link_rounded,
                  size: 14,
                  color: context.appColors.accent,
                ),
                label: Text(
                  'Link Issue',
                  style: TextStyle(
                    color: context.appColors.accent,
                    fontSize: 11,
                  ),
                ),
                style: TextButton.styleFrom(
                  padding: const EdgeInsets.symmetric(horizontal: 6),
                  minimumSize: Size.zero,
                  tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                ),
              ),
            ),
          ],
        ),
        const SizedBox(height: 8),
        if (linkedIssues.isEmpty)
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: context.appColors.bgElevated,
              borderRadius: BorderRadius.circular(8),
            ),
            child: Text(
              'No Linear issues linked',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 13,
              ),
            ),
          )
        else
          ...linkedIssues.map(
            (issue) => _LinkedIssueTile(
              issue: issue,
              appState: appState,
              taskId: task.taskId,
            ),
          ),
      ],
    );
  }

  Future<void> _showLinkIssuePicker(
    BuildContext context,
    AppState appState,
    TaskInfo task,
  ) async {
    await showDialog<void>(
      context: context,
      builder: (ctx) => _LinkIssueDialog(appState: appState, task: task),
    );
  }
}

class _StatusChip extends StatelessWidget {
  final String label;
  final Color color;
  final bool current;
  final VoidCallback? onTap;

  const _StatusChip({
    required this.label,
    required this.color,
    required this.current,
    this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(12),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: current ? color.withAlpha(30) : Colors.transparent,
          borderRadius: BorderRadius.circular(12),
          border: Border.all(
            color: current ? color : color.withAlpha(60),
            width: current ? 1.5 : 1,
          ),
        ),
        child: Text(
          label,
          style: TextStyle(
            color: color,
            fontSize: 11,
            fontWeight: current ? FontWeight.w600 : FontWeight.w500,
          ),
        ),
      ),
    );
  }
}
