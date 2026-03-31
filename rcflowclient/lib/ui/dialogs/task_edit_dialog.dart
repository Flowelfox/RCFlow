import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../models/task_info.dart';
import '../../theme.dart';

/// Data class returned from the edit dialog with only changed fields.
class TaskEditResult {
  final String? title;
  final String? description;

  const TaskEditResult({this.title, this.description});

  bool get hasChanges => title != null || description != null;
}

/// Shows a dialog to edit a task's title and description.
///
/// Returns a [TaskEditResult] with only the changed fields, or `null` if
/// cancelled.
Future<TaskEditResult?> showTaskEditDialog(
  BuildContext context, {
  required TaskInfo task,
}) {
  return showDialog<TaskEditResult>(
    context: context,
    builder: (_) => _TaskEditDialog(task: task),
  );
}

class _TaskEditDialog extends StatefulWidget {
  final TaskInfo task;

  const _TaskEditDialog({required this.task});

  @override
  State<_TaskEditDialog> createState() => _TaskEditDialogState();
}

class _TaskEditDialogState extends State<_TaskEditDialog> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _titleCtrl;
  late final TextEditingController _descCtrl;
  final _descFocus = FocusNode();

  @override
  void initState() {
    super.initState();
    _titleCtrl = TextEditingController(text: widget.task.title);
    _descCtrl = TextEditingController(text: widget.task.description ?? '');
  }

  @override
  void dispose() {
    _titleCtrl.dispose();
    _descCtrl.dispose();
    _descFocus.dispose();
    super.dispose();
  }

  void _save() {
    if (!_formKey.currentState!.validate()) return;

    final newTitle = _titleCtrl.text.trim();
    final newDesc = _descCtrl.text.trim();

    final titleChanged = newTitle != widget.task.title;
    final descChanged = newDesc != (widget.task.description ?? '');

    final result = TaskEditResult(
      title: titleChanged ? newTitle : null,
      description: descChanged ? newDesc : null,
    );

    if (!result.hasChanges) {
      Navigator.of(context).pop();
      return;
    }

    Navigator.of(context).pop(result);
  }

  @override
  Widget build(BuildContext context) {
    return Dialog(
      backgroundColor: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      child: KeyboardListener(
        focusNode: FocusNode(),
        onKeyEvent: (event) {
          if (event is KeyDownEvent &&
              event.logicalKey == LogicalKeyboardKey.enter &&
              HardwareKeyboard.instance.isControlPressed) {
            _save();
          }
        },
        child: SizedBox(
          width: 480,
          child: Form(
            key: _formKey,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                // Header
                Padding(
                  padding: const EdgeInsets.fromLTRB(24, 24, 24, 0),
                  child: Text(
                    'Edit Task',
                    style: TextStyle(
                      color: context.appColors.textPrimary,
                      fontSize: 18,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ),
                const SizedBox(height: 20),

                // Title
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 24),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      _buildLabel(context, 'Title', required: true),
                      const SizedBox(height: 6),
                      TextFormField(
                        controller: _titleCtrl,
                        autofocus: true,
                        maxLength: 300,
                        style: TextStyle(
                          color: context.appColors.textPrimary,
                          fontSize: 15,
                        ),
                        decoration: InputDecoration(
                          fillColor: context.appColors.bgElevated,
                          filled: true,
                          counterText: '',
                          border: OutlineInputBorder(
                            borderSide: BorderSide.none,
                            borderRadius: BorderRadius.circular(14),
                          ),
                        ),
                        validator: (v) {
                          if (v == null || v.trim().isEmpty) {
                            return 'Title is required';
                          }
                          return null;
                        },
                        onFieldSubmitted: (_) =>
                            FocusScope.of(context).requestFocus(_descFocus),
                        textInputAction: TextInputAction.next,
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 16),

                // Description
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 24),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      _buildLabel(context, 'Description'),
                      const SizedBox(height: 6),
                      TextFormField(
                        controller: _descCtrl,
                        focusNode: _descFocus,
                        minLines: 3,
                        maxLines: 5,
                        style: TextStyle(
                          color: context.appColors.textPrimary,
                          fontSize: 14,
                        ),
                        decoration: InputDecoration(
                          hintText: 'Optional details...',
                          hintStyle: TextStyle(
                            color: context.appColors.textMuted,
                          ),
                          fillColor: context.appColors.bgElevated,
                          filled: true,
                          border: OutlineInputBorder(
                            borderSide: BorderSide.none,
                            borderRadius: BorderRadius.circular(14),
                          ),
                        ),
                      ),
                    ],
                  ),
                ),

                const SizedBox(height: 20),
                Divider(height: 1, color: context.appColors.divider),

                // Actions
                Padding(
                  padding: const EdgeInsets.fromLTRB(24, 12, 24, 16),
                  child: Row(
                    children: [
                      Text(
                        'Ctrl+Enter to save',
                        style: TextStyle(
                          color: context.appColors.textMuted,
                          fontSize: 11,
                        ),
                      ),
                      const Spacer(),
                      TextButton(
                        onPressed: () => Navigator.of(context).pop(),
                        child: Text(
                          'Cancel',
                          style: TextStyle(
                            color: context.appColors.textSecondary,
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      FilledButton(
                        style: FilledButton.styleFrom(
                          backgroundColor: context.appColors.accent,
                        ),
                        onPressed: _save,
                        child: const Text(
                          'Save',
                          style: TextStyle(color: Colors.white),
                        ),
                      ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildLabel(
    BuildContext context,
    String text, {
    bool required = false,
  }) {
    return RichText(
      text: TextSpan(
        text: text,
        style: TextStyle(color: context.appColors.textSecondary, fontSize: 13),
        children: [
          if (required)
            TextSpan(
              text: ' *',
              style: TextStyle(
                color: context.appColors.accentLight,
                fontSize: 13,
              ),
            ),
        ],
      ),
    );
  }
}
