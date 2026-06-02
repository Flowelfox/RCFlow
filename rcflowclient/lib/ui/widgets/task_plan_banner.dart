part of 'task_pane.dart';

class _PlanBanner extends StatelessWidget {
  final TaskInfo task;
  final AppState appState;
  final String paneId;

  const _PlanBanner({
    required this.task,
    required this.appState,
    required this.paneId,
  });

  static const _green = Color(0xFF10B981);

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: kSpace3, vertical: 10),
      decoration: BoxDecoration(
        color: _green.withAlpha(18),
        borderRadius: BorderRadius.circular(kRadiusMedium),
        border: Border.all(color: _green.withAlpha(60)),
      ),
      child: Row(
        children: [
          const Icon(Icons.description_outlined, color: _green, size: 16),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              'A plan has been generated for this task.',
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 12,
              ),
            ),
          ),
          const SizedBox(width: 8),
          TextButton(
            onPressed: () => appState.openArtifactInPane(task.planArtifactId!),
            style: TextButton.styleFrom(
              foregroundColor: _green,
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
              minimumSize: Size.zero,
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
              textStyle: const TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w600,
              ),
            ),
            child: const Text('Open plan'),
          ),
          const SizedBox(width: 4),
          TextButton(
            onPressed: () => appState.startPlanSession(paneId, task),
            style: TextButton.styleFrom(
              foregroundColor: context.appColors.textSecondary,
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
              minimumSize: Size.zero,
              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
              textStyle: const TextStyle(fontSize: 12),
            ),
            child: const Text('Regenerate'),
          ),
        ],
      ),
    );
  }
}
