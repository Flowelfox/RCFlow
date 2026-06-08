import 'package:flutter/material.dart';

import '../../../models/app_notification.dart';
import '../../../models/deduped_pr.dart';
import '../../../models/github_pr_info.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import '../../../theme/spacing.dart';

/// Decide which worker should run a writable action (resolve-conflicts / fix)
/// on a PR backed by several workers.
///
/// Only workers with a local clone are candidates. With one candidate it's used
/// silently. With several, each worker's per-repo "default" flag is polled:
/// exactly one default → use it; none → ask; more than one → a misconfiguration,
/// so all defaults are cleared and we ask. The picker can persist the choice as
/// the new default (set on the chosen worker, cleared on the others).
///
/// Returns the chosen source, or null when there's no clone / the user cancels.
Future<GithubPrInfo?> resolvePrActionWorker(
  BuildContext context,
  AppState appState,
  DedupedPr dpr,
) async {
  final clones = dpr.cloneSources;
  if (clones.isEmpty) return null;
  if (clones.length == 1) return clones.first;

  final owner = dpr.canonical.repoOwner;
  final repo = dpr.canonical.repoName;

  // Poll each candidate worker: does it claim to be the default for this repo?
  final defaults = <GithubPrInfo>[];
  for (final s in clones) {
    final ws = appState.getWorker(s.workerId)?.ws;
    if (ws == null) continue;
    try {
      final res = await ws.getGithubRepoDefaults();
      final list = (res['defaults'] as List<dynamic>? ?? [])
          .cast<Map<String, dynamic>>();
      final isDefault = list.any(
        (d) => d['owner'] == owner && d['repo'] == repo,
      );
      if (isDefault) defaults.add(s);
    } catch (_) {
      // Treat an unreachable/erroring worker as "no vote".
    }
  }

  if (defaults.length == 1) return defaults.first;

  if (defaults.length > 1) {
    // Conflicting defaults — clear them all and ask again.
    for (final s in defaults) {
      try {
        await appState.getWorker(s.workerId)?.ws.setGithubRepoDefault(
          owner,
          repo,
          false,
        );
      } catch (_) {}
    }
    appState.showNotification(
      level: NotificationLevel.warning,
      title: 'Conflicting default workers reset',
      body: 'More than one worker was set as default for $owner/$repo.',
    );
  }

  if (!context.mounted) return null;
  return _showWorkerPicker(context, appState, dpr, clones, owner, repo);
}

Future<GithubPrInfo?> _showWorkerPicker(
  BuildContext context,
  AppState appState,
  DedupedPr dpr,
  List<GithubPrInfo> clones,
  String owner,
  String repo,
) async {
  var selected = clones.first;
  var setDefault = false;

  final chosen = await showDialog<GithubPrInfo>(
    context: context,
    builder: (ctx) {
      final colors = ctx.appColors;
      return StatefulBuilder(
        builder: (ctx, setLocal) => AlertDialog(
          backgroundColor: colors.bgElevated,
          title: Text(
            'Which worker?',
            style: TextStyle(color: colors.textPrimary, fontSize: 16),
          ),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'Run this on the checkout of $owner/$repo from:',
                style: TextStyle(color: colors.textSecondary, fontSize: 13),
              ),
              const SizedBox(height: kSpace2),
              for (final s in clones)
                InkWell(
                  onTap: () => setLocal(() => selected = s),
                  borderRadius: BorderRadius.circular(kRadiusSmall),
                  child: Padding(
                    padding: const EdgeInsets.symmetric(vertical: kSpace1),
                    child: Row(
                      children: [
                        Icon(
                          s == selected
                              ? Icons.radio_button_checked
                              : Icons.radio_button_unchecked,
                          size: 18,
                          color: s == selected ? colors.accent : colors.textMuted,
                        ),
                        const SizedBox(width: kSpace2),
                        Expanded(
                          child: Text(
                            '${s.workerName.isNotEmpty ? s.workerName : 'Worker'} / '
                            '${(s.projectName ?? '').isNotEmpty ? s.projectName : '—'}',
                            style: TextStyle(
                              color: colors.textPrimary,
                              fontSize: 13,
                            ),
                          ),
                        ),
                      ],
                    ),
                  ),
                ),
              CheckboxListTile(
                value: setDefault,
                onChanged: (v) => setLocal(() => setDefault = v ?? false),
                dense: true,
                contentPadding: EdgeInsets.zero,
                controlAffinity: ListTileControlAffinity.leading,
                activeColor: colors.accent,
                title: Text(
                  "Remember — don't ask again for this repo",
                  style: TextStyle(color: colors.textSecondary, fontSize: 12),
                ),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(),
              child: Text('Cancel', style: TextStyle(color: colors.textMuted)),
            ),
            FilledButton(
              onPressed: () => Navigator.of(ctx).pop(selected),
              style: FilledButton.styleFrom(backgroundColor: colors.accent),
              child: const Text('Run here'),
            ),
          ],
        ),
      );
    },
  );

  if (chosen == null) return null;

  if (setDefault) {
    // Set the chosen worker as default; clear the others for this repo.
    for (final s in clones) {
      final ws = appState.getWorker(s.workerId)?.ws;
      if (ws == null) continue;
      try {
        await ws.setGithubRepoDefault(owner, repo, s.workerId == chosen.workerId);
      } catch (_) {}
    }
  }
  return chosen;
}
