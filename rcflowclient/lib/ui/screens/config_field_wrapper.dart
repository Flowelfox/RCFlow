part of 'server_config_screen.dart';

class _SourceBadge extends StatelessWidget {
  final String label;
  final bool accent;

  const _SourceBadge({required this.label, required this.accent});

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: accent
            ? context.appColors.accentDim
            : context.appColors.bgOverlay,
        borderRadius: BorderRadius.circular(4),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: accent
              ? context.appColors.accentLight
              : context.appColors.textMuted,
          fontSize: 9,
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Field wrapper
// ---------------------------------------------------------------------------

class _FieldWrapper extends StatelessWidget {
  final ConfigOption option;
  final bool isModified;
  final Widget child;

  const _FieldWrapper({
    required this.option,
    required this.isModified,
    required this.child,
  });

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (option.type != 'boolean')
          Row(
            children: [
              Text(
                option.label,
                style: TextStyle(
                  color: isModified
                      ? context.appColors.accentLight
                      : context.appColors.textSecondary,
                  fontSize: 12,
                  fontWeight: isModified ? FontWeight.w600 : FontWeight.normal,
                ),
              ),
              if (option.restartRequired)
                Padding(
                  padding: EdgeInsets.only(left: 6),
                  child: Text(
                    'restart required',
                    style: TextStyle(
                      color: context.appColors.toolAccent,
                      fontSize: 10,
                    ),
                  ),
                ),
              if (isModified)
                Padding(
                  padding: EdgeInsets.only(left: 6),
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
        if (option.type != 'boolean') SizedBox(height: 4),
        child,
        if (option.description.isNotEmpty)
          Padding(
            padding: EdgeInsets.only(top: 4),
            child: Text(
              option.description,
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 11,
              ),
            ),
          ),
      ],
    );
  }
}
