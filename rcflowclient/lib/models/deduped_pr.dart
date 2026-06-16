import 'github_pr_info.dart';

/// A single logical pull request aggregated across the workers that back it
/// (same GitHub node id). Pointing several workers at one account otherwise
/// shows the same PR once per worker; this collapses them into one entry whose
/// [sources] record each backing worker (and the local project on it).
class DedupedPr {
  /// The grouping key — the PR's GitHub node id (or the row id as a fallback
  /// when a node id is missing).
  final String key;

  /// One source per backing worker, freshest first.
  final List<GithubPrInfo> sources;

  const DedupedPr(this.key, this.sources);

  /// Freshest source — used for the displayed fields (title, state, status…).
  GithubPrInfo get canonical => sources.first;

  /// Sources whose worker has a local clone of the repo — the only valid
  /// targets for writable actions (resolve-conflicts / fix / assist).
  List<GithubPrInfo> get cloneSources => sources
      .where((s) => (s.projectPath ?? '').isNotEmpty)
      .toList(growable: false);

  /// Whether this PR is backed by more than one worker.
  bool get hasMultipleSources => sources.length > 1;
}
