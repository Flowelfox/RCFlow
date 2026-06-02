part of 'settings_menu.dart';

class _AboutSection extends StatelessWidget {
  const _AboutSection();

  @override
  Widget build(BuildContext context) {
    final appState = context.read<AppState>();
    final version = appState.settings.currentVersion ?? '';

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SectionHeader(title: 'About', icon: Icons.info_outline),
        Container(
          width: double.infinity,
          padding: const EdgeInsets.all(kSpace4),
          decoration: BoxDecoration(
            color: context.appColors.bgElevated,
            borderRadius: BorderRadius.circular(kRadiusLarge),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                'RCFlow Client',
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 16,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 4),
              Text(
                version.isEmpty ? 'Unknown version' : 'v$version',
                style: TextStyle(
                  color: context.appColors.accentLight,
                  fontSize: 14,
                ),
              ),
              const SizedBox(height: 12),
              Text(
                'A client for the RCFlow server — execute actions on your '
                'host machine via natural language prompts.',
                style: TextStyle(
                  color: context.appColors.textSecondary,
                  fontSize: 13,
                ),
              ),
              const SizedBox(height: 16),
              // ── Update status ───────────────────────────────────────────
              ListenableBuilder(
                listenable: appState.updateService,
                builder: (ctx, _) {
                  final svc = appState.updateService;
                  if (svc.isChecking) {
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: Row(
                        children: [
                          SizedBox(
                            width: 14,
                            height: 14,
                            child: CircularProgressIndicator(
                              strokeWidth: 2,
                              color: context.appColors.textMuted,
                            ),
                          ),
                          const SizedBox(width: 8),
                          Text(
                            'Checking for updates…',
                            style: TextStyle(
                              color: context.appColors.textMuted,
                              fontSize: 13,
                            ),
                          ),
                        ],
                      ),
                    );
                  }

                  if (svc.hasError) {
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: Row(
                        children: [
                          Icon(
                            Icons.error_outline,
                            size: 16,
                            color: context.appColors.errorText,
                          ),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Text(
                              'Update check failed',
                              style: TextStyle(
                                color: context.appColors.errorText,
                                fontSize: 13,
                              ),
                            ),
                          ),
                          TextButton(
                            onPressed: svc.checkForUpdates,
                            style: TextButton.styleFrom(
                              foregroundColor: context.appColors.textMuted,
                              padding: const EdgeInsets.symmetric(
                                horizontal: kSpace2,
                              ),
                              minimumSize: Size.zero,
                              tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                            ),
                            child: const Text(
                              'Retry',
                              style: TextStyle(fontSize: 12),
                            ),
                          ),
                        ],
                      ),
                    );
                  }

                  if (svc.updateAvailable) {
                    final latest = svc.latestVersion!;
                    final url = svc.latestDownloadUrl ?? svc.latestReleaseUrl;
                    return Padding(
                      padding: const EdgeInsets.only(bottom: 12),
                      child: Row(
                        children: [
                          Icon(
                            Icons.new_releases_outlined,
                            size: 16,
                            color: context.appColors.accent,
                          ),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Text(
                              'v$latest available',
                              style: TextStyle(
                                color: context.appColors.accent,
                                fontSize: 13,
                              ),
                            ),
                          ),
                          if (url != null)
                            TextButton(
                              onPressed: () => launchUrl(
                                Uri.parse(url),
                                mode: LaunchMode.externalApplication,
                              ),
                              style: TextButton.styleFrom(
                                foregroundColor: context.appColors.accent,
                                padding: const EdgeInsets.symmetric(
                                  horizontal: kSpace2,
                                ),
                                minimumSize: Size.zero,
                                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                              ),
                              child: const Text(
                                'Update',
                                style: TextStyle(fontSize: 12),
                              ),
                            ),
                        ],
                      ),
                    );
                  }

                  // No update available (or not yet checked) — show button.
                  final upToDate = svc.latestVersion != null;
                  return Padding(
                    padding: const EdgeInsets.only(bottom: 12),
                    child: Row(
                      children: [
                        if (upToDate) ...[
                          Icon(
                            Icons.check_circle_outline,
                            size: 16,
                            color: context.appColors.successText,
                          ),
                          const SizedBox(width: 6),
                          Text(
                            'Up to date',
                            style: TextStyle(
                              color: context.appColors.successText,
                              fontSize: 13,
                            ),
                          ),
                          const SizedBox(width: 8),
                        ],
                        TextButton(
                          onPressed: svc.checkForUpdates,
                          style: TextButton.styleFrom(
                            foregroundColor: context.appColors.textMuted,
                            padding: const EdgeInsets.symmetric(horizontal: kSpace2),
                            minimumSize: Size.zero,
                            tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                          ),
                          child: Text(
                            upToDate ? 'Check again' : 'Check for updates',
                            style: const TextStyle(fontSize: 12),
                          ),
                        ),
                      ],
                    ),
                  );
                },
              ),
              // ── Action buttons ──────────────────────────────────────────
              Row(
                children: [
                  OutlinedButton.icon(
                    onPressed: () {
                      // Capture navigator before pop — dialog context dies.
                      final nav = Navigator.of(context);
                      nav.pop();
                      Future.delayed(const Duration(milliseconds: 200), () {
                        final ctx = nav.context;
                        if (ctx.mounted) {
                          showSetupWizard(ctx);
                        }
                      });
                    },
                    icon: const Icon(Icons.rocket_launch_outlined, size: 18),
                    label: const Text('Setup Wizard'),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: context.appColors.textSecondary,
                      side: BorderSide(color: context.appColors.divider),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(kRadiusMedium),
                      ),
                      padding: const EdgeInsets.symmetric(
                        horizontal: 14,
                        vertical: 10,
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  OutlinedButton.icon(
                    onPressed: () {
                      // Capture overlay + settings before pop — the dialog
                      // context becomes unmounted once Navigator.pop() runs.
                      final overlay = Overlay.of(context);
                      final settings = context.read<AppState>().settings;
                      settings.onboardingComplete = false;
                      Navigator.of(context).pop();
                      // Delayed so the dialog fully closes first. The
                      // captured overlay & settings stay valid.
                      Future.delayed(const Duration(milliseconds: 200), () {
                        final ctx = overlay.context;
                        if (ctx.mounted) {
                          showOnboardingOverlay(
                            ctx,
                            overlay: overlay,
                            settings: settings,
                          );
                        }
                      });
                    },
                    icon: const Icon(Icons.tour_outlined, size: 18),
                    label: const Text('Replay Tour'),
                    style: OutlinedButton.styleFrom(
                      foregroundColor: context.appColors.textSecondary,
                      side: BorderSide(color: context.appColors.divider),
                      shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(kRadiusMedium),
                      ),
                      padding: const EdgeInsets.symmetric(
                        horizontal: 14,
                        vertical: 10,
                      ),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Segmented selector (reusable for theme / font size)
// ---------------------------------------------------------------------------
