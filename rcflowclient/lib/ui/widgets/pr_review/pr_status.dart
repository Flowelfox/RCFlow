import 'package:flutter/material.dart';

import '../../../models/github_pr_info.dart';

/// Visual representation of a PR's status: an icon, a colour, and a short label.
typedef PrStatusVisual = ({IconData icon, Color color, String label});

/// Derive a single status indicator for a pull request from its lifecycle
/// state, review decision, and mergeability — mirroring GitHub's badges.
///
/// Priority (most decisive first): merged / closed / draft take precedence;
/// for open PRs a merge conflict ("Can't merge") outranks the review decision,
/// then Changes requested / Approved / Review required, else plain Open.
PrStatusVisual prStatusVisual(GithubPrInfo pr) {
  if (pr.isMerged) {
    return (icon: Icons.merge_type, color: const Color(0xFF8B5CF6), label: 'Merged');
  }
  if (pr.state == 'closed') {
    return (icon: Icons.cancel_outlined, color: const Color(0xFFEF4444), label: 'Closed');
  }
  if (pr.draft) {
    return (icon: Icons.edit_note, color: const Color(0xFF6B7280), label: 'Draft');
  }
  // Open PR — surface the most actionable signal.
  if (pr.mergeStatus == 'CONFLICTING') {
    return (icon: Icons.warning_amber_rounded, color: const Color(0xFFF85149), label: "Can't merge");
  }
  switch (pr.reviewDecision) {
    case 'CHANGES_REQUESTED':
      return (icon: Icons.error_outline, color: const Color(0xFFEF4444), label: 'Changes requested');
    case 'APPROVED':
      return (icon: Icons.check_circle, color: const Color(0xFF2DA44E), label: 'Approved');
    case 'REVIEW_REQUIRED':
      return (icon: Icons.schedule, color: const Color(0xFFD29922), label: 'Review required');
  }
  return (icon: Icons.call_merge, color: const Color(0xFF10B981), label: 'Open');
}
