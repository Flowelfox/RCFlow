/// Worker statistics pane — shows aggregated telemetry charts and totals
/// across all sessions belonging to a single worker.
///
/// Unlike [StatisticsPane] (which lives inside a session pane right-panel),
/// this widget is self-contained and does not require a [PaneState] context.
/// It is opened as a dialog from the worker context menu.
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/telemetry.dart';
import '../../services/worker_connection.dart';
import '../../state/statistics_pane_state.dart';
import '../../theme.dart';
import 'statistics_panel/telemetry_chart.dart';

/// Full-screen dialog body showing worker-level telemetry.
///
/// Call via [showWorkerStatsDialog].
class WorkerStatsPane extends StatefulWidget {
  final WorkerConnection worker;

  const WorkerStatsPane({super.key, required this.worker});

  @override
  State<WorkerStatsPane> createState() => _WorkerStatsPaneState();
}

class _WorkerStatsPaneState extends State<WorkerStatsPane> {
  final StatisticsPaneState _state = StatisticsPaneState();
  WorkerTelemetrySummary? _workerSummary;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _refresh());
  }

  @override
  void dispose() {
    _state.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    _state.setLoading();
    final range = _state.effectiveRange();
    try {
      // Time-series: global rollup (no session_id) = aggregated across all sessions
      final data = await widget.worker.fetchTimeSeries(
        zoom: _state.zoomLevel.apiValue,
        start: range.start,
        end: range.end,
      );
      final rawSeries = (data['series'] as List<dynamic>?) ?? [];
      final series = rawSeries
          .whereType<Map<String, dynamic>>()
          .map(BucketPoint.fromJson)
          .toList();
      _state.setSeries(series);

      // Worker summary (aggregated across all sessions)
      final summaryData = await widget.worker.fetchWorkerTelemetry();
      if (mounted) {
        setState(() {
          _workerSummary = WorkerTelemetrySummary.fromJson(summaryData);
        });
      }
    } catch (e) {
      _state.setError(e.toString());
    }
  }

  @override
  Widget build(BuildContext context) {
    return ChangeNotifierProvider<StatisticsPaneState>.value(
      value: _state,
      child: Consumer<StatisticsPaneState>(
        builder: (context, state, _) {
          return Column(
            children: [
              _WorkerFilterBar(state: state, onRefresh: _refresh),
              Expanded(
                child: state.loading
                    ? const Center(
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : state.error != null
                    ? _ErrorView(error: state.error!)
                    : _WorkerChartsBody(
                        state: state,
                        workerSummary: _workerSummary,
                      ),
              ),
            ],
          );
        },
      ),
    );
  }
}

// ------------------------------------------------------------------
// Filter bar
// ------------------------------------------------------------------

class _WorkerFilterBar extends StatelessWidget {
  final StatisticsPaneState state;
  final VoidCallback onRefresh;

  const _WorkerFilterBar({required this.state, required this.onRefresh});

  @override
  Widget build(BuildContext context) {
    return Container(
      height: 36,
      decoration: BoxDecoration(
        color: context.appColors.bgSurface,
        border: Border(bottom: BorderSide(color: context.appColors.divider)),
      ),
      padding: const EdgeInsets.symmetric(horizontal: 8),
      child: Row(
        children: [
          Expanded(
            child: Text(
              'Worker Statistics',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 12,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          for (final zoom in ZoomLevel.values)
            Padding(
              padding: const EdgeInsets.only(left: 4),
              child: _ZoomChip(
                label: zoom.label,
                active: state.zoomLevel == zoom,
                onTap: () {
                  state.setZoomLevel(zoom);
                  onRefresh();
                },
              ),
            ),
          const SizedBox(width: 4),
          SizedBox(
            width: 24,
            height: 24,
            child: IconButton(
              padding: EdgeInsets.zero,
              icon: Icon(
                Icons.refresh_rounded,
                size: 14,
                color: context.appColors.textMuted,
              ),
              onPressed: onRefresh,
              constraints: const BoxConstraints(maxWidth: 24, maxHeight: 24),
            ),
          ),
        ],
      ),
    );
  }
}

class _ZoomChip extends StatelessWidget {
  final String label;
  final bool active;
  final VoidCallback onTap;

  const _ZoomChip({
    required this.label,
    required this.active,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 120),
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
        decoration: BoxDecoration(
          color: active
              ? context.appColors.accent.withAlpha(40)
              : Colors.transparent,
          borderRadius: BorderRadius.circular(4),
          border: Border.all(
            color: active
                ? context.appColors.accent.withAlpha(120)
                : context.appColors.divider,
          ),
        ),
        child: Text(
          label,
          style: TextStyle(
            fontSize: 10,
            color: active
                ? context.appColors.accent
                : context.appColors.textMuted,
            fontWeight: active ? FontWeight.w600 : FontWeight.w400,
          ),
        ),
      ),
    );
  }
}

// ------------------------------------------------------------------
// Charts body
// ------------------------------------------------------------------

class _WorkerChartsBody extends StatelessWidget {
  final StatisticsPaneState state;
  final WorkerTelemetrySummary? workerSummary;

  const _WorkerChartsBody({required this.state, required this.workerSummary});

  @override
  Widget build(BuildContext context) {
    if (state.series.isEmpty && workerSummary == null) {
      return Center(
        child: Text(
          'No telemetry data for this window.',
          style: TextStyle(color: context.appColors.textMuted, fontSize: 12),
          textAlign: TextAlign.center,
        ),
      );
    }

    return ListView(
      padding: const EdgeInsets.all(12),
      children: [
        if (workerSummary != null) ...[
          _WorkerSummaryCard(summary: workerSummary!),
          const SizedBox(height: 12),
        ],
        if (state.series.isNotEmpty) ...[
          _ChartSection(
            title: 'Tokens Sent / Received',
            child: TelemetryChart(
              series: state.series,
              metric: MetricType.tokensSent,
              secondaryMetric: MetricType.tokensReceived,
              zoomLevel: state.zoomLevel,
            ),
          ),
          const SizedBox(height: 12),
          _ChartSection(
            title: 'Avg LLM Duration',
            child: TelemetryChart(
              series: state.series,
              metric: MetricType.avgLlmDurationMs,
              zoomLevel: state.zoomLevel,
            ),
          ),
          const SizedBox(height: 12),
          _ChartSection(
            title: 'Avg Tool Duration',
            child: TelemetryChart(
              series: state.series,
              metric: MetricType.avgToolDurationMs,
              zoomLevel: state.zoomLevel,
            ),
          ),
          const SizedBox(height: 12),
          _ChartSection(
            title: 'Turns / Tool Calls',
            child: TelemetryChart(
              series: state.series,
              metric: MetricType.turnCount,
              secondaryMetric: MetricType.toolCallCount,
              zoomLevel: state.zoomLevel,
            ),
          ),
        ],
      ],
    );
  }
}

class _ChartSection extends StatelessWidget {
  final String title;
  final Widget child;

  const _ChartSection({required this.title, required this.child});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          title,
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 11,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 6),
        child,
      ],
    );
  }
}

// ------------------------------------------------------------------
// Worker summary card
// ------------------------------------------------------------------

class _WorkerSummaryCard extends StatelessWidget {
  final WorkerTelemetrySummary summary;

  const _WorkerSummaryCard({required this.summary});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Worker Summary',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 11,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 6),
        Wrap(
          spacing: 6,
          runSpacing: 4,
          children: [
            _StatPill(label: 'Sessions', value: '${summary.sessionCount}'),
            _StatPill(label: 'Turns', value: '${summary.turnCount}'),
            _StatPill(
              label: 'In tokens',
              value: _fmt(summary.totalInputTokens),
            ),
            _StatPill(
              label: 'Out tokens',
              value: _fmt(summary.totalOutputTokens),
            ),
            _StatPill(label: 'Tool calls', value: '${summary.totalToolCalls}'),
            if (summary.avgLlmDurationMs != null)
              _StatPill(
                label: 'Avg LLM',
                value: '${summary.avgLlmDurationMs!.round()}ms',
              ),
            if (summary.p95LlmDurationMs != null)
              _StatPill(
                label: 'p95 LLM',
                value: '${summary.p95LlmDurationMs!.round()}ms',
              ),
            if (summary.avgToolDurationMs != null)
              _StatPill(
                label: 'Avg tool',
                value: '${summary.avgToolDurationMs!.round()}ms',
              ),
            if (summary.errorRate > 0)
              _StatPill(
                label: 'Error rate',
                value: '${(summary.errorRate * 100).toStringAsFixed(1)}%',
                isWarning: true,
              ),
          ],
        ),
        if (summary.topTools.isNotEmpty) ...[
          const SizedBox(height: 8),
          Text(
            'Top tools',
            style: TextStyle(
              color: context.appColors.textMuted,
              fontSize: 10,
              fontWeight: FontWeight.w500,
            ),
          ),
          const SizedBox(height: 4),
          ...summary.topTools.map((t) => _ToolRow(tool: t)),
        ],
      ],
    );
  }

  String _fmt(int n) {
    if (n >= 1000000) return '${(n / 1000000).toStringAsFixed(1)}M';
    if (n >= 1000) return '${(n / 1000).toStringAsFixed(1)}k';
    return '$n';
  }
}

class _ToolRow extends StatelessWidget {
  final Map<String, dynamic> tool;

  const _ToolRow({required this.tool});

  @override
  Widget build(BuildContext context) {
    final name = tool['tool_name'] as String? ?? '';
    final count = tool['call_count'] as int? ?? 0;
    final avgMs = tool['avg_duration_ms'];
    final avgStr = avgMs != null ? ' · ${(avgMs as num).round()}ms avg' : '';

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: [
          Expanded(
            child: Text(
              '$name — $count calls$avgStr',
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 10,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ),
        ],
      ),
    );
  }
}

class _StatPill extends StatelessWidget {
  final String label;
  final String value;
  final bool isWarning;

  const _StatPill({
    required this.label,
    required this.value,
    this.isWarning = false,
  });

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 3),
      decoration: BoxDecoration(
        color: isWarning
            ? Colors.orange.withAlpha(30)
            : context.appColors.bgSurface,
        borderRadius: BorderRadius.circular(4),
        border: Border.all(
          color: isWarning
              ? Colors.orange.withAlpha(80)
              : context.appColors.divider,
        ),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            label,
            style: TextStyle(color: context.appColors.textMuted, fontSize: 10),
          ),
          const SizedBox(width: 4),
          Text(
            value,
            style: TextStyle(
              color: isWarning ? Colors.orange : context.appColors.textPrimary,
              fontSize: 10,
              fontWeight: FontWeight.w600,
            ),
          ),
        ],
      ),
    );
  }
}

class _ErrorView extends StatelessWidget {
  final String error;

  const _ErrorView({required this.error});

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Text(
          error,
          style: TextStyle(color: context.appColors.textMuted, fontSize: 11),
          textAlign: TextAlign.center,
        ),
      ),
    );
  }
}

// ------------------------------------------------------------------
// Dialog helper
// ------------------------------------------------------------------

/// Show [WorkerStatsPane] in a full-screen dialog.
Future<void> showWorkerStatsDialog(
  BuildContext context, {
  required WorkerConnection worker,
  required String workerName,
}) {
  return showDialog<void>(
    context: context,
    builder: (ctx) => Dialog(
      backgroundColor: ctx.appColors.bgBase,
      insetPadding: const EdgeInsets.all(24),
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      child: SizedBox(
        width: 640,
        height: 600,
        child: Column(
          children: [
            // Dialog header
            Container(
              height: 48,
              padding: const EdgeInsets.symmetric(horizontal: 16),
              decoration: BoxDecoration(
                color: ctx.appColors.bgSurface,
                borderRadius: const BorderRadius.vertical(
                  top: Radius.circular(16),
                ),
                border: Border(
                  bottom: BorderSide(color: ctx.appColors.divider),
                ),
              ),
              child: Row(
                children: [
                  Icon(Icons.bar_chart_rounded, size: 16, color: Colors.teal),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      workerName,
                      style: TextStyle(
                        color: ctx.appColors.textPrimary,
                        fontSize: 13,
                        fontWeight: FontWeight.w600,
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
                        Icons.close_rounded,
                        size: 16,
                        color: ctx.appColors.textMuted,
                      ),
                      onPressed: () => Navigator.of(ctx).pop(),
                      constraints: const BoxConstraints(
                        maxWidth: 28,
                        maxHeight: 28,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            // Stats content
            Expanded(child: WorkerStatsPane(worker: worker)),
          ],
        ),
      ),
    ),
  );
}
