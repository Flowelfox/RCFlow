part of 'server_config_screen.dart';

class _SectionHeader extends StatelessWidget {
  final String title;
  final IconData icon;
  final bool hasModified;

  const _SectionHeader({
    required this.title,
    required this.icon,
    this.hasModified = false,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: EdgeInsets.only(bottom: 16),
      child: Row(
        children: [
          Icon(icon, color: context.appColors.accentLight, size: 20),
          SizedBox(width: 8),
          Text(
            title,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 17,
              fontWeight: FontWeight.w600,
            ),
          ),
          if (hasModified)
            Padding(
              padding: EdgeInsets.only(left: 8),
              child: Text(
                '\u2022 modified',
                style: TextStyle(
                  color: context.appColors.accentLight,
                  fontSize: 10,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

class _ConfigSidebarItem extends StatelessWidget {
  final String label;
  final IconData icon;
  final bool selected;
  final bool hasModified;
  final IconData? trailingIcon;
  final VoidCallback onTap;

  const _ConfigSidebarItem({
    required this.label,
    required this.icon,
    required this.selected,
    this.hasModified = false,
    this.trailingIcon,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(horizontal: kSpace2, vertical: 2),
      child: Material(
        color: selected ? context.appColors.bgElevated : Colors.transparent,
        borderRadius: BorderRadius.circular(kRadiusMedium),
        child: InkWell(
          borderRadius: BorderRadius.circular(kRadiusMedium),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: kSpace3, vertical: 10),
            child: Row(
              children: [
                Icon(
                  icon,
                  size: 18,
                  color: selected
                      ? context.appColors.accentLight
                      : context.appColors.textMuted,
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    label,
                    style: TextStyle(
                      color: selected
                          ? context.appColors.textPrimary
                          : context.appColors.textSecondary,
                      fontSize: 14,
                      fontWeight: selected
                          ? FontWeight.w600
                          : FontWeight.normal,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                if (hasModified)
                  Container(
                    width: 6,
                    height: 6,
                    decoration: BoxDecoration(
                      color: context.appColors.accentLight,
                      shape: BoxShape.circle,
                    ),
                  ),
                if (trailingIcon != null) ...[
                  const SizedBox(width: 4),
                  Icon(
                    trailingIcon,
                    size: 16,
                    color: context.appColors.textMuted,
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Indented sidebar item used for tool sub-entries under the Tools group.
class _ConfigSidebarSubItem extends StatelessWidget {
  final String label;
  final bool selected;
  final VoidCallback onTap;

  const _ConfigSidebarSubItem({
    required this.label,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      // Left indent aligns the sub-item under the parent's label.
      padding: const EdgeInsets.only(left: 24, right: 8, top: 1, bottom: 1),
      child: Material(
        color: selected ? context.appColors.bgElevated : Colors.transparent,
        borderRadius: BorderRadius.circular(8),
        child: InkWell(
          borderRadius: BorderRadius.circular(8),
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: kSpace3, vertical: kSpace2),
            child: Row(
              children: [
                Container(
                  width: 4,
                  height: 4,
                  decoration: BoxDecoration(
                    color: selected
                        ? context.appColors.accentLight
                        : context.appColors.textMuted,
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    label,
                    style: TextStyle(
                      color: selected
                          ? context.appColors.textPrimary
                          : context.appColors.textSecondary,
                      fontSize: 13,
                      fontWeight: selected
                          ? FontWeight.w600
                          : FontWeight.normal,
                    ),
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Field widgets
// ---------------------------------------------------------------------------
