part of 'settings_menu.dart';

class _WorkersSection extends StatelessWidget {
  final VoidCallback onClose;

  const _WorkersSection({required this.onClose});

  @override
  Widget build(BuildContext context) {
    return Consumer<AppState>(
      builder: (ctx, state, _) {
        final total = state.totalWorkerCount;
        final conn = state.connectedWorkerCount;
        final summary = total == 0
            ? 'No workers configured'
            : '$conn of $total connected';

        return Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            _SectionHeader(title: 'Workers', icon: Icons.dns_outlined),
            Container(
              width: double.infinity,
              padding: EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: context.appColors.bgElevated,
                borderRadius: BorderRadius.circular(14),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Container(
                        width: 10,
                        height: 10,
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: conn > 0
                              ? context.appColors.successText
                              : context.appColors.textMuted,
                        ),
                      ),
                      SizedBox(width: 10),
                      Text(
                        summary,
                        style: TextStyle(
                          color: context.appColors.textPrimary,
                          fontSize: 15,
                        ),
                      ),
                    ],
                  ),
                  if (_isDesktop) ...[
                    // Desktop: inline worker list + add button
                    if (state.workerConfigs.isNotEmpty) ...[
                      const SizedBox(height: 12),
                      for (final config in state.workerConfigs)
                        _WorkerRow(
                          config: config,
                          worker: state.getWorker(config.id),
                          onEdit: () async {
                            final updated = await showWorkerEditDialog(
                              ctx,
                              existing: config,
                              worker: state.getWorker(config.id),
                            );
                            if (updated != null && ctx.mounted) {
                              state.updateWorker(updated);
                            }
                          },
                        ),
                    ],
                    const SizedBox(height: 8),
                    SizedBox(
                      width: double.infinity,
                      height: 38,
                      child: OutlinedButton.icon(
                        onPressed: () async {
                          final config = await showWorkerEditDialog(
                            ctx,
                            sortOrder: state.workerConfigs.length,
                          );
                          if (config != null && ctx.mounted) {
                            state.addWorker(config);
                          }
                        },
                        icon: Icon(Icons.add_rounded, size: 18),
                        label: Text(
                          'Add Worker',
                          style: TextStyle(fontSize: 13),
                        ),
                        style: OutlinedButton.styleFrom(
                          foregroundColor: context.appColors.textSecondary,
                          side: BorderSide(color: context.appColors.divider),
                          shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(10),
                          ),
                        ),
                      ),
                    ),
                  ] else ...[
                    // Mobile: button to open full workers screen
                    const SizedBox(height: 12),
                    SizedBox(
                      width: double.infinity,
                      height: 44,
                      child: Builder(
                        builder: (btnContext) => FilledButton.icon(
                          onPressed: () {
                            onClose();
                            Future.microtask(() {
                              if (btnContext.mounted) {
                                showWorkersScreen(btnContext);
                              }
                            });
                          },
                          icon: Icon(Icons.settings_outlined, size: 18),
                          label: Text(
                            'Manage Workers',
                            style: TextStyle(
                              fontSize: 14,
                              fontWeight: FontWeight.w600,
                            ),
                          ),
                          style: FilledButton.styleFrom(
                            backgroundColor: context.appColors.accent,
                            foregroundColor: Colors.white,
                            shape: RoundedRectangleBorder(
                              borderRadius: BorderRadius.circular(12),
                            ),
                          ),
                        ),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ],
        );
      },
    );
  }
}

class _WorkerRow extends StatelessWidget {
  final WorkerConfig config;
  final WorkerConnection? worker;
  final VoidCallback onEdit;

  const _WorkerRow({
    required this.config,
    required this.worker,
    required this.onEdit,
  });

  @override
  Widget build(BuildContext context) {
    final status = worker?.status ?? WorkerConnectionStatus.disconnected;
    final statusColor = switch (status) {
      WorkerConnectionStatus.connected => context.appColors.successText,
      WorkerConnectionStatus.connecting => context.appColors.toolAccent,
      WorkerConnectionStatus.reconnecting => context.appColors.toolAccent,
      WorkerConnectionStatus.disconnected => context.appColors.textMuted,
    };

    return Padding(
      padding: const EdgeInsets.only(bottom: 4),
      child: Row(
        children: [
          Container(
            width: 7,
            height: 7,
            decoration: BoxDecoration(
              shape: BoxShape.circle,
              color: statusColor,
            ),
          ),
          SizedBox(width: 8),
          Expanded(
            child: Text(
              config.name,
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 13,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          SizedBox(
            width: 28,
            height: 28,
            child: IconButton(
              padding: EdgeInsets.zero,
              icon: Icon(
                Icons.edit_outlined,
                color: context.appColors.textMuted,
                size: 16,
              ),
              onPressed: onEdit,
              tooltip: 'Edit worker',
              constraints: const BoxConstraints(maxWidth: 28, maxHeight: 28),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Appearance section
// ---------------------------------------------------------------------------
