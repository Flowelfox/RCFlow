import 'package:flutter/material.dart';

/// Parameters returned by [showCreateWorktreeDialog] on success.
class CreateWorktreeParams {
  final String branch;
  final String base;
  const CreateWorktreeParams({required this.branch, required this.base});
}

/// Shows a dialog for creating a new git worktree.
///
/// Returns [CreateWorktreeParams] when the user confirms, or `null` if
/// cancelled. The [repoPath] is informational only — it is not sent to the
/// server by this dialog.
Future<CreateWorktreeParams?> showCreateWorktreeDialog(
  BuildContext context,
) async {
  final branchCtrl = TextEditingController();
  final baseCtrl = TextEditingController(text: 'main');
  final formKey = GlobalKey<FormState>();

  return showDialog<CreateWorktreeParams>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: const Text('New Worktree'),
      content: Form(
        key: formKey,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextFormField(
              controller: branchCtrl,
              decoration: const InputDecoration(
                labelText: 'Branch',
                hintText: 'feature/PROJ-123/description',
              ),
              validator: (v) =>
                  (v == null || v.trim().isEmpty) ? 'Required' : null,
              autofocus: true,
            ),
            const SizedBox(height: 8),
            TextFormField(
              controller: baseCtrl,
              decoration: const InputDecoration(labelText: 'Base branch'),
              validator: (v) =>
                  (v == null || v.trim().isEmpty) ? 'Required' : null,
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(ctx),
          child: const Text('Cancel'),
        ),
        TextButton(
          onPressed: () {
            if (formKey.currentState!.validate()) {
              Navigator.pop(
                ctx,
                CreateWorktreeParams(
                  branch: branchCtrl.text.trim(),
                  base: baseCtrl.text.trim(),
                ),
              );
            }
          },
          child: const Text('Create'),
        ),
      ],
    ),
  );
}
