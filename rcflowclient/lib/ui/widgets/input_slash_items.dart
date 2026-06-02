part of 'input_area.dart';

class _SlashGroupHeader extends StatelessWidget {
  final String label;

  const _SlashGroupHeader({required this.label});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 4),
      child: Text(
        label.toUpperCase(),
        style: TextStyle(
          color: context.appColors.textMuted,
          fontSize: 10,
          fontWeight: FontWeight.w600,
          letterSpacing: 0.8,
        ),
      ),
    );
  }
}

class _SlashCommandItem extends StatelessWidget {
  final String name;
  final String description;
  final String source;
  final String query;
  final bool selected;
  final VoidCallback onTap;

  const _SlashCommandItem({
    required this.name,
    required this.description,
    required this.source,
    required this.query,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    final isRCFlow = source == 'rcflow';
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: kSpace3, vertical: kSpace2),
        color: selected ? context.appColors.bgOverlay : Colors.transparent,
        child: Row(
          children: [
            Icon(
              isRCFlow ? Icons.electric_bolt_rounded : Icons.terminal_rounded,
              size: 15,
              color: isRCFlow
                  ? context.appColors.accentLight
                  : context.appColors.textMuted,
            ),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  _buildHighlightedName(context),
                  if (description.isNotEmpty)
                    Text(
                      description,
                      style: TextStyle(
                        color: context.appColors.textMuted,
                        fontSize: 11,
                      ),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildHighlightedName(BuildContext context) {
    if (query.isEmpty) {
      return Text(
        '/$name',
        style: TextStyle(
          color: context.appColors.textPrimary,
          fontSize: 13,
          fontWeight: FontWeight.w500,
        ),
        overflow: TextOverflow.ellipsis,
      );
    }

    final lowerName = name.toLowerCase();
    final lowerQuery = query.toLowerCase();
    final matchIndex = lowerName.indexOf(lowerQuery);

    if (matchIndex < 0) {
      return Text(
        '/$name',
        style: TextStyle(
          color: context.appColors.textPrimary,
          fontSize: 13,
          fontWeight: FontWeight.w500,
        ),
        overflow: TextOverflow.ellipsis,
      );
    }

    return Text.rich(
      TextSpan(
        children: [
          TextSpan(
            text: '/',
            style: TextStyle(
              color: context.appColors.textSecondary,
              fontSize: 13,
              fontWeight: FontWeight.w500,
            ),
          ),
          if (matchIndex > 0)
            TextSpan(
              text: name.substring(0, matchIndex),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
                fontWeight: FontWeight.w500,
              ),
            ),
          TextSpan(
            text: name.substring(matchIndex, matchIndex + query.length),
            style: TextStyle(
              color: context.appColors.accentLight,
              fontSize: 13,
              fontWeight: FontWeight.w600,
            ),
          ),
          if (matchIndex + query.length < name.length)
            TextSpan(
              text: name.substring(matchIndex + query.length),
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 13,
                fontWeight: FontWeight.w500,
              ),
            ),
        ],
      ),
      overflow: TextOverflow.ellipsis,
    );
  }
}
