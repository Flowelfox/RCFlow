import '../../models/badge_spec.dart';
import '../../models/worker_config.dart';

/// Builds a [BadgeSpec] list for the new-chat (pre-session) context.
///
/// Draft badges use the same [BadgeSpec] shape as live badges so [BadgeBar]
/// can render them identically.  They are constructed entirely client-side
/// and are never sent to or from the server.
class DraftBadgeComposer {
  const DraftBadgeComposer();

  /// Map internal agent_type identifiers to user-facing badge labels.
  /// Mirrors the server-side ``_AGENT_DISPLAY_LABELS`` in ``badges.py``.
  static const _agentDisplayLabels = {
    'claude_code': 'ClaudeCode',
    'codex': 'Codex',
    'opencode': 'OpenCode',
  };

  /// Return draft badges for the given selections.
  ///
  /// - [worker]: The [WorkerConfig] the new session will be sent to.
  /// - [agentType]: Internal agent tool name (e.g. ``"claude_code"``).
  /// - [projectPath]: Absolute path of the pre-selected project, if any.
  /// - [worktreePath]: Absolute path of the pre-selected worktree, if any.
  List<BadgeSpec> compose({
    WorkerConfig? worker,
    String? agentType,
    String? projectPath,
    String? worktreePath,
  }) {
    final badges = <BadgeSpec>[];

    if (worker != null) {
      badges.add(BadgeSpec(
        type: 'worker',
        label: worker.name,
        priority: BadgePriority.worker,
        visible: true,
        // Interactive in draft context — tapping opens the worker picker.
        interactive: true,
        payload: {'worker_id': worker.id},
      ));
    }

    if (agentType != null) {
      badges.add(BadgeSpec(
        type: 'agent',
        label: _agentDisplayLabels[agentType] ?? agentType,
        priority: BadgePriority.agent,
        visible: true,
        interactive: false,
        payload: {'agent_type': agentType},
      ));
    }

    if (projectPath != null) {
      final parts = projectPath.split('/').where((p) => p.isNotEmpty).toList();
      final name = parts.isNotEmpty ? parts.last : projectPath;
      badges.add(BadgeSpec(
        type: 'project',
        label: name,
        priority: BadgePriority.project,
        visible: true,
        interactive: false,
        payload: {'path': projectPath},
      ));
    }

    if (worktreePath != null) {
      final parts =
          worktreePath.split('/').where((p) => p.isNotEmpty).toList();
      final name = parts.isNotEmpty ? parts.last : worktreePath;
      badges.add(BadgeSpec(
        type: 'worktree',
        label: name,
        priority: BadgePriority.worktree,
        visible: true,
        interactive: false,
        payload: {'path': worktreePath},
      ));
    }

    return badges;
  }
}
