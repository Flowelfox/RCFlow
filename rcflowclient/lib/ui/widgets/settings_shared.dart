part of 'settings_menu.dart';

class _SegmentedSelector extends StatelessWidget {
  final List<String> options;
  final List<String> labels;
  final String selected;
  final ValueChanged<String> onChanged;

  const _SegmentedSelector({
    required this.options,
    required this.labels,
    required this.selected,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      decoration: BoxDecoration(
        color: context.appColors.bgElevated,
        borderRadius: BorderRadius.circular(12),
      ),
      padding: const EdgeInsets.all(4),
      child: Row(
        children: [
          for (var i = 0; i < options.length; i++)
            Expanded(
              child: GestureDetector(
                onTap: () => onChanged(options[i]),
                child: AnimatedContainer(
                  duration: Duration(milliseconds: 200),
                  padding: EdgeInsets.symmetric(vertical: 10),
                  decoration: BoxDecoration(
                    color: selected == options[i]
                        ? context.appColors.accent
                        : Colors.transparent,
                    borderRadius: BorderRadius.circular(9),
                  ),
                  alignment: Alignment.center,
                  child: Text(
                    labels[i],
                    style: TextStyle(
                      color: selected == options[i]
                          ? Colors.white
                          : context.appColors.textSecondary,
                      fontSize: 13,
                      fontWeight: selected == options[i]
                          ? FontWeight.w600
                          : FontWeight.normal,
                    ),
                  ),
                ),
              ),
            ),
        ],
      ),
    );
  }
}
