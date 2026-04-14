import '../../models/badge_spec.dart';
import '../../models/worker_config.dart';

/// Builds a [BadgeSpec] list for the new-chat (pre-session) context.
///
/// Draft badges use the same [BadgeSpec] shape as live badges so [BadgeBar]
/// can render them identically.  They are constructed entirely client-side
/// and are never sent to or from the server.
class DraftBadgeComposer {
  const DraftBadgeComposer();

  /// Return draft badges for the given selections.
  ///
  /// - [worker]: The [WorkerConfig] the new session will be sent to.
  /// - [projectPath]: Absolute path of the pre-selected project, if any.
  /// - [worktreePath]: Absolute path of the pre-selected worktree, if any.
  List<BadgeSpec> compose({
    WorkerConfig? worker,
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
