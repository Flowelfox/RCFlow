import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../services/settings_service.dart';
import '../../state/app_state.dart';
import '../../theme.dart';
import '../onboarding_keys.dart' as keys;

/// Title bar height — the overlay leaves this area interactive so the window
/// remains draggable and the traffic-light / window buttons stay clickable.
const _titleBarHeight = 40.0;

/// Launches the onboarding tour overlay.
///
/// When called from a context that is about to be unmounted (e.g. a dialog
/// that will be popped), pass [overlay] and [settings] explicitly so the
/// overlay can be inserted and settings updated after the caller is gone.
void showOnboardingOverlay(
  BuildContext context, {
  OverlayState? overlay,
  SettingsService? settings,
}) {
  final ov = overlay ?? Overlay.of(context);
  final svc = settings ?? context.read<AppState>().settings;
  late OverlayEntry entry;
  entry = OverlayEntry(
    builder: (ctx) => _OnboardingOverlay(
      onDone: () {
        entry.remove();
        svc.onboardingComplete = true;
      },
    ),
  );
  ov.insert(entry);
}

// ---------------------------------------------------------------------------
// Tour step definitions
// ---------------------------------------------------------------------------

class _TourStep {
  final GlobalKey targetKey;
  final String title;
  final String description;
  /// Preferred side to show the tooltip relative to the target.
  final _TooltipSide preferredSide;

  const _TourStep({
    required this.targetKey,
    required this.title,
    required this.description,
    this.preferredSide = _TooltipSide.right,
  });
}

enum _TooltipSide { left, right, top, bottom }

final _tourSteps = [
  _TourStep(
    targetKey: keys.sidebarKey,
    title: 'Session Sidebar',
    description:
        'Your command center \u2014 all sessions, tasks, and artifacts live here.',
    preferredSide: _TooltipSide.right,
  ),
  _TourStep(
    targetKey: keys.sidebarTabBarKey,
    title: 'Sidebar Tabs',
    description:
        'Workers shows AI sessions grouped by server. '
        'Tasks tracks work items. Artifacts shows generated files.',
    preferredSide: _TooltipSide.bottom,
  ),
  _TourStep(
    targetKey: keys.mainContentKey,
    title: 'Workspace',
    description:
        'Chat with AI agents here. Split into multiple panes by '
        'dragging sessions from the sidebar.',
    preferredSide: _TooltipSide.left,
  ),
  _TourStep(
    targetKey: keys.rightBookmarksKey,
    title: 'Side Panels',
    description:
        'Todo shows the agent\u2019s checklist. Project manages git '
        'worktrees. Stats shows usage metrics.',
    preferredSide: _TooltipSide.left,
  ),
  _TourStep(
    targetKey: keys.inputAreaKey,
    title: 'Chat Input',
    description:
        'Type messages here. Use @ to mention projects and # to '
        'select an agent. Press Enter to send.',
    preferredSide: _TooltipSide.top,
  ),
  _TourStep(
    targetKey: keys.settingsButtonKey,
    title: 'Settings',
    description:
        'Manage workers, appearance, notifications, and more.',
    preferredSide: _TooltipSide.right,
  ),
];

// ---------------------------------------------------------------------------
// Overlay widget
// ---------------------------------------------------------------------------

class _OnboardingOverlay extends StatefulWidget {
  final VoidCallback onDone;

  const _OnboardingOverlay({required this.onDone});

  @override
  State<_OnboardingOverlay> createState() => _OnboardingOverlayState();
}

class _OnboardingOverlayState extends State<_OnboardingOverlay>
    with SingleTickerProviderStateMixin {
  int _stepIndex = 0;
  late AnimationController _animCtrl;
  late Animation<double> _fadeAnim;

  /// Steps filtered to only those whose target widget is currently mounted.
  List<_TourStep> get _activeSteps {
    return _tourSteps
        .where((s) => s.targetKey.currentContext != null)
        .toList();
  }

  @override
  void initState() {
    super.initState();
    _animCtrl = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 300),
    );
    _fadeAnim = CurvedAnimation(parent: _animCtrl, curve: Curves.easeOut);
    _animCtrl.forward();
  }

  @override
  void dispose() {
    _animCtrl.dispose();
    super.dispose();
  }

  void _next() {
    final steps = _activeSteps;
    if (_stepIndex < steps.length - 1) {
      setState(() => _stepIndex++);
    } else {
      _done();
    }
  }

  void _done() {
    _animCtrl.reverse().then((_) => widget.onDone());
  }

  @override
  Widget build(BuildContext context) {
    // LayoutBuilder triggers a rebuild whenever the window is resized,
    // so the spotlight cutout and tooltip reposition to follow their targets.
    return LayoutBuilder(
      builder: (context, constraints) {
        final steps = _activeSteps;

        // If no targetable steps are available, finish immediately.
        if (steps.isEmpty) {
          WidgetsBinding.instance.addPostFrameCallback((_) => widget.onDone());
          return const SizedBox.shrink();
        }

        // Clamp index in case a step became unavailable.
        final idx = _stepIndex.clamp(0, steps.length - 1);
        final step = steps[idx];

        // Get target rect
        final renderBox =
            step.targetKey.currentContext?.findRenderObject() as RenderBox?;
        if (renderBox == null || !renderBox.attached) {
          // Target not yet laid out — skip to next or finish.
          WidgetsBinding.instance.addPostFrameCallback((_) {
            if (mounted) _next();
          });
          return const SizedBox.shrink();
        }

        final targetOffset = renderBox.localToGlobal(Offset.zero);
        final targetSize = renderBox.size;
        final targetRect = targetOffset & targetSize;

        return FadeTransition(
          opacity: _fadeAnim,
          child: Stack(
            children: [
              // Scrim with cutout — painted over the full window but the
              // CustomPaint itself does not absorb pointer events.
              Positioned.fill(
                child: IgnorePointer(
                  child: CustomPaint(
                    painter: _SpotlightPainter(
                      targetRect: targetRect,
                      borderRadius: 12,
                    ),
                  ),
                ),
              ),
              // Tap absorber — starts below the title bar so the window stays
              // draggable and the traffic-light / close buttons remain clickable.
              Positioned(
                left: 0,
                right: 0,
                top: _titleBarHeight,
                bottom: 0,
                child: GestureDetector(
                  onTap: _next,
                  behavior: HitTestBehavior.translucent,
                ),
              ),
              // Tooltip card
              _buildTooltip(context, step, targetRect, idx, steps.length),
            ],
          ),
        );
      },
    );
  }

  Widget _buildTooltip(
    BuildContext context,
    _TourStep step,
    Rect targetRect,
    int idx,
    int total,
  ) {
    const tooltipWidth = 280.0;
    const tooltipPadding = 16.0;
    final screenSize = MediaQuery.of(context).size;

    // Calculate tooltip position based on preferred side.
    double left;
    double top;

    switch (step.preferredSide) {
      case _TooltipSide.right:
        left = (targetRect.right + tooltipPadding)
            .clamp(0, screenSize.width - tooltipWidth - 16);
        top = targetRect.center.dy - 60;
      case _TooltipSide.left:
        left = (targetRect.left - tooltipWidth - tooltipPadding)
            .clamp(16, screenSize.width - tooltipWidth);
        top = targetRect.center.dy - 60;
      case _TooltipSide.bottom:
        left = (targetRect.center.dx - tooltipWidth / 2)
            .clamp(16, screenSize.width - tooltipWidth - 16);
        top = targetRect.bottom + tooltipPadding;
      case _TooltipSide.top:
        left = (targetRect.center.dx - tooltipWidth / 2)
            .clamp(16, screenSize.width - tooltipWidth - 16);
        top = targetRect.top - tooltipPadding - 160;
    }

    top = top.clamp(16, screenSize.height - 200);

    return Positioned(
      left: left,
      top: top,
      child: Material(
        color: Colors.transparent,
        child: Container(
          width: tooltipWidth,
          padding: const EdgeInsets.all(20),
          decoration: BoxDecoration(
            color: context.appColors.bgSurface,
            borderRadius: BorderRadius.circular(16),
            border: Border.all(
              color: context.appColors.accent.withAlpha(80),
            ),
            boxShadow: [
              BoxShadow(
                color: Colors.black.withAlpha(80),
                blurRadius: 24,
                offset: const Offset(0, 8),
              ),
            ],
          ),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                step.title,
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 16,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 8),
              Text(
                step.description,
                style: TextStyle(
                  color: context.appColors.textSecondary,
                  fontSize: 13,
                  height: 1.5,
                ),
              ),
              const SizedBox(height: 16),
              Row(
                children: [
                  // Step counter
                  Text(
                    '${idx + 1} / $total',
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 12,
                    ),
                  ),
                  const Spacer(),
                  TextButton(
                    onPressed: _done,
                    child: Text('Skip',
                        style: TextStyle(
                            color: context.appColors.textMuted, fontSize: 13)),
                  ),
                  const SizedBox(width: 4),
                  FilledButton(
                    style: FilledButton.styleFrom(
                      backgroundColor: context.appColors.accent,
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(10)),
                      padding: const EdgeInsets.symmetric(
                          horizontal: 16, vertical: 10),
                    ),
                    onPressed: _next,
                    child: Text(
                      idx < total - 1 ? 'Next' : 'Done',
                      style: const TextStyle(color: Colors.white, fontSize: 13),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Spotlight painter — semi-transparent scrim with a rounded-rect cutout
// ---------------------------------------------------------------------------

class _SpotlightPainter extends CustomPainter {
  final Rect targetRect;
  final double borderRadius;
  static const _padding = 8.0;

  _SpotlightPainter({required this.targetRect, this.borderRadius = 12});

  @override
  void paint(Canvas canvas, Size size) {
    final scrimPaint = Paint()..color = Colors.black.withAlpha(160);

    // Inflate the target rect slightly for breathing room.
    final inflated = targetRect.inflate(_padding);
    final rrect = RRect.fromRectAndRadius(
      inflated,
      Radius.circular(borderRadius),
    );

    // Use path difference to cut out the target area from the full scrim.
    final outerPath = Path()..addRect(Offset.zero & size);
    final innerPath = Path()..addRRect(rrect);
    final combinedPath =
        Path.combine(PathOperation.difference, outerPath, innerPath);

    canvas.drawPath(combinedPath, scrimPaint);

    // Draw a subtle accent border around the cutout.
    final borderPaint = Paint()
      ..color = const Color(0xFF6366F1).withAlpha(100)
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2;
    canvas.drawRRect(rrect, borderPaint);
  }

  @override
  bool shouldRepaint(_SpotlightPainter old) => old.targetRect != targetRect;
}
