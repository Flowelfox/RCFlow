import 'badge_spec.dart';

/// Synthesises a [BadgeSpec] list from the legacy flat-field ``session_update``
/// message format for servers that pre-date the unified badge system (< 0.39.0).
///
/// Called in [WorkerConnection._handleSessionUpdate] when the incoming message
/// does not contain a ``badges`` key.
abstract final class LegacyBadgeAdapter {
  /// Build the badge list from flat [msg] fields.
  ///
  /// [workerLabel] is the client-side worker name (from [WorkerConfig.name])
  /// which the server never sends.
  static List<BadgeSpec> adapt(
    Map<String, dynamic> msg, {
    String workerLabel = '',
  }) {
    final badges = <BadgeSpec>[];

    // Status badge — always present.
    final status = msg['status'] as String? ?? '';
    final activityState = msg['activity_state'] as String? ?? 'idle';
    badges.add(BadgeSpec(
      type: 'status',
      label: status,
      priority: BadgePriority.status,
      visible: true,
      interactive: false,
      payload: {'activity_state': activityState},
    ));

    // Worker badge — only when we know the worker name.
    if (workerLabel.isNotEmpty) {
      badges.add(BadgeSpec(
        type: 'worker',
        label: workerLabel,
        priority: BadgePriority.worker,
        visible: true,
        interactive: false,
        payload: {'worker_id': ''},
      ));
    }

    // Agent badge.
    final agent = msg['agent_type'] as String?;
    if (agent != null) {
      badges.add(BadgeSpec(
        type: 'agent',
        label: agent,
        priority: BadgePriority.agent,
        visible: true,
        interactive: false,
        payload: {'agent_type': agent},
      ));
    }

    // Project badge.
    final path = msg['main_project_path'] as String?;
    final error = msg['project_name_error'] as String?;
    if (path != null || error != null) {
      final parts =
          (path ?? '').split('/').where((p) => p.isNotEmpty).toList();
      final name = parts.isNotEmpty ? parts.last : (path ?? 'unknown');
      badges.add(BadgeSpec(
        type: 'project',
        label: name,
        priority: BadgePriority.project,
        visible: true,
        interactive: false,
        payload: {'path': path, 'error': error},
      ));
    }

    // Worktree badge.
    final wt = msg['worktree'] as Map<String, dynamic>?;
    if (wt != null) {
      final branch = wt['branch'] as String?;
      final repoPath = wt['repo_path'] as String?;
      final label = branch ?? repoPath ?? 'worktree';
      badges.add(BadgeSpec(
        type: 'worktree',
        label: label,
        priority: BadgePriority.worktree,
        visible: true,
        interactive: false,
        payload: Map<String, dynamic>.from(wt),
      ));
    }

    // Caveman badge.
    if (msg['caveman_mode'] == true) {
      badges.add(BadgeSpec(
        type: 'caveman',
        label: 'Caveman',
        priority: BadgePriority.caveman,
        visible: true,
        interactive: false,
      ));
    }

    return badges;
  }
}
