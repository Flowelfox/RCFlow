import 'package:flutter/material.dart';

import '../../theme.dart';

// Fallback neutral colors, used when the [AppColors] theme extension is not
// installed (e.g. bare widget tests). Production code runs under [AppTheme]
// and picks up the real palette via the extension below.
const _fallbackBg = Color(0xFF2A2F3A);
const _fallbackBorder = Color(0xFF3A404C);
const _fallbackText = Color(0xFFB4BCD0);
const _fallbackIcon = Color(0xFF858DA0);

/// Shared visual chip for all session badges.
///
/// Every badge renderer (`worker`, `caveman`, `agent`, `project`, `worktree`,
/// `status`, the generic fallback) and every non-registry badge widget
/// (`WorkerBadge`, `CavemanPreviewBadge`, input-area composer chips) renders
/// its chip through this widget so they share identical padding, corner
/// radius, border, and text metrics.
///
/// Two visual variants exist:
///
/// * **Neutral** (`color == null`, the default for most badges) — uses the
///   theme's elevated surface / divider / text colors. Reserve for metadata
///   chips where color would not add meaning (worker, project, worktree, etc).
/// * **Colored** (`color != null`) — tints background, border, icon, and text
///   from [color]. Reserve for chips where the color carries a signal
///   (`status` state, `caveman` warning mode).
class BadgeChip extends StatelessWidget {
  /// Accent color for the chip. `null` selects the neutral theme variant.
  final Color? color;

  /// Human-readable text shown on the chip.
  final String label;

  /// Optional leading glyph; omitted for text-only chips.
  final IconData? icon;

  /// Optional trailing widget (drop-down caret, dismiss button, spinner, …).
  final Widget? trailing;

  const BadgeChip({
    super.key,
    this.color,
    required this.label,
    this.icon,
    this.trailing,
  });

  /// Builds a standard drop-down caret for colored chips.
  static Widget dropdownCaret(Color color) => Icon(
        Icons.arrow_drop_down,
        size: 16,
        color: color.withAlpha(180),
      );

  /// Builds a standard drop-down caret for neutral chips.
  static Widget neutralDropdownCaret(BuildContext context) => Icon(
        Icons.arrow_drop_down,
        size: 16,
        color: Theme.of(context).extension<AppColors>()?.textMuted ??
            _fallbackIcon,
      );

  @override
  Widget build(BuildContext context) {
    final appColors = Theme.of(context).extension<AppColors>();
    final bg = color?.withAlpha(25) ?? appColors?.bgElevated ?? _fallbackBg;
    final borderColor =
        color?.withAlpha(70) ?? appColors?.divider ?? _fallbackBorder;
    final textColor = color ?? appColors?.textSecondary ?? _fallbackText;
    final iconColor =
        color?.withAlpha(180) ?? appColors?.textMuted ?? _fallbackIcon;

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: borderColor),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (icon != null) ...[
            Icon(icon, size: 14, color: iconColor),
            const SizedBox(width: 6),
          ],
          Text(
            label,
            style: TextStyle(
              color: textColor,
              fontSize: 12,
              fontWeight: FontWeight.w500,
            ),
          ),
          if (trailing != null) ...[
            const SizedBox(width: 4),
            trailing!,
          ],
        ],
      ),
    );
  }
}
