import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../state/app_state.dart';
import '../../theme.dart';

/// Shows a dialog to create a new user task.
///
/// Returns `true` if a task was created, `null`/`false` if cancelled.
Future<bool?> showTaskCreateDialog(BuildContext context) {
  return showDialog<bool>(
    context: context,
    builder: (_) => ChangeNotifierProvider<AppState>.value(
      value: context.read<AppState>(),
      child: const _TaskCreateDialog(),
    ),
  );
}

class _TaskCreateDialog extends StatefulWidget {
  const _TaskCreateDialog();

  @override
  State<_TaskCreateDialog> createState() => _TaskCreateDialogState();
}

class _TaskCreateDialogState extends State<_TaskCreateDialog> {
  final _formKey = GlobalKey<FormState>();
  final _titleCtrl = TextEditingController();
  final _descCtrl = TextEditingController();
  final _titleFocus = FocusNode();
  final _descFocus = FocusNode();
  String? _selectedWorkerId;
  bool _submitting = false;

  @override
  void initState() {
    super.initState();
    final appState = context.read<AppState>();
    // Default to the active pane's worker, or the first connected worker.
    final activeWorkerId = appState.hasNoPanes
        ? null
        : appState.activePane.workerId;
    _selectedWorkerId = activeWorkerId ?? _firstConnectedWorkerId(appState);
  }

  @override
  void dispose() {
    _titleCtrl.dispose();
    _descCtrl.dispose();
    _titleFocus.dispose();
    _descFocus.dispose();
    super.dispose();
  }

  String? _firstConnectedWorkerId(AppState appState) {
    for (final config in appState.workerConfigs) {
      final worker = appState.getWorker(config.id);
      if (worker != null && worker.isConnected) return config.id;
    }
    return null;
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    if (_selectedWorkerId == null) return;

    setState(() => _submitting = true);

    final appState = context.read<AppState>();
    final worker = appState.getWorker(_selectedWorkerId!);
    if (worker == null || !worker.isConnected) {
      if (mounted) {
        setState(() => _submitting = false);
      }
      return;
    }

    try {
      await worker.ws.createTask(
        title: _titleCtrl.text.trim(),
        description:
            _descCtrl.text.trim().isEmpty ? null : _descCtrl.text.trim(),
        source: 'user',
      );
      if (mounted) Navigator.of(context).pop(true);
    } catch (e) {
      if (mounted) {
        setState(() => _submitting = false);
        appState.addSystemMessage('Failed to create task: $e', isError: true);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final appState = context.watch<AppState>();
    final connectedWorkers = appState.workerConfigs.where((config) {
      final worker = appState.getWorker(config.id);
      return worker != null && worker.isConnected;
    }).toList();
    final multiWorker = connectedWorkers.length > 1;

    return Dialog(
      backgroundColor: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      child: KeyboardListener(
        focusNode: FocusNode(),
        onKeyEvent: (event) {
          if (event is KeyDownEvent &&
              event.logicalKey == LogicalKeyboardKey.enter &&
              HardwareKeyboard.instance.isControlPressed) {
            _submit();
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
                    'New Task',
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
                        focusNode: _titleFocus,
                        autofocus: true,
                        maxLength: 300,
                        style: TextStyle(
                          color: context.appColors.textPrimary,
                          fontSize: 15,
                        ),
                        decoration: InputDecoration(
                          hintText: 'What needs to be done?',
                          hintStyle: TextStyle(
                            color: context.appColors.textMuted,
                          ),
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

                // Worker picker (only if multiple connected workers)
                if (multiWorker) ...[
                  const SizedBox(height: 16),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 24),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        _buildLabel(context, 'Worker'),
                        const SizedBox(height: 6),
                        DropdownButtonFormField<String>(
                          initialValue: _selectedWorkerId,
                          dropdownColor: context.appColors.bgElevated,
                          style: TextStyle(
                            color: context.appColors.textPrimary,
                            fontSize: 14,
                          ),
                          decoration: InputDecoration(
                            fillColor: context.appColors.bgElevated,
                            filled: true,
                            border: OutlineInputBorder(
                              borderSide: BorderSide.none,
                              borderRadius: BorderRadius.circular(14),
                            ),
                          ),
                          items: connectedWorkers.map((config) {
                            return DropdownMenuItem(
                              value: config.id,
                              child: Text(config.name),
                            );
                          }).toList(),
                          onChanged: (v) =>
                              setState(() => _selectedWorkerId = v),
                        ),
                      ],
                    ),
                  ),
                ],

                const SizedBox(height: 20),
                Divider(height: 1, color: context.appColors.divider),

                // Actions
                Padding(
                  padding: const EdgeInsets.fromLTRB(24, 12, 24, 16),
                  child: Row(
                    children: [
                      Text(
                        'Ctrl+Enter to submit',
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
                        onPressed: _submitting ? null : _submit,
                        child: _submitting
                            ? SizedBox(
                                width: 16,
                                height: 16,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                  color: Colors.white,
                                ),
                              )
                            : const Text(
                                'Create',
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

  Widget _buildLabel(BuildContext context, String text,
      {bool required = false}) {
    return RichText(
      text: TextSpan(
        text: text,
        style: TextStyle(
          color: context.appColors.textSecondary,
          fontSize: 13,
        ),
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
