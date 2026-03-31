/// Statistics pane — shows telemetry charts and per-session summaries.
///
/// Sits in the right-panel bookmark bar alongside the Todo and Project panels.
library;

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/telemetry.dart';
import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../state/statistics_pane_state.dart';
import '../../theme.dart';
import 'statistics_panel/telemetry_chart.dart';

class StatisticsPane extends StatefulWidget {
  const StatisticsPane({super.key});

  @override
  State<StatisticsPane> createState() => _StatisticsPaneState();
}

class _StatisticsPaneState extends State<StatisticsPane> {
  final StatisticsPaneState _state = StatisticsPaneState();

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
    final pane = context.read<PaneState>();
    final appState = context.read<AppState>();
    final workerId = pane.workerId ?? appState.defaultWorkerId;
    if (workerId == null) return;
    final worker = appState.getWorker(workerId);
    if (worker == null) return;

    _state.setLoading();
    final range = _state.effectiveRange();
    try {
      final data = await worker.fetchTimeSeries(
        zoom: _state.zoomLevel.apiValue,
        start: range.start,
        end: range.end,
        sessionId: _state.sessionFilter,
      );
      final rawSeries = (data['series'] as List<dynamic>?) ?? [];
      final series = rawSeries
          .whereType<Map<String, dynamic>>()
          .map(BucketPoint.fromJson)
          .toList();
      _state.setSeries(series);

      // Load per-session summary when a session is selected
      final sessionId = _state.sessionFilter ?? pane.sessionId;
      if (sessionId != null) {
        final summary = await worker.fetchSessionTelemetry(sessionId);
        _state.setSessionSummary(
          summary != null ? SessionTelemetrySummary.fromJson(summary) : null,
        );
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
              _FilterBar(state: state, onRefresh: _refresh),
              Expanded(
                child: state.loading
                    ? const Center(
                        child: CircularProgressIndicator(strokeWidth: 2),
                      )
                    : state.error != null
                    ? _ErrorView(error: state.error!)
                    : _ChartsBody(state: state),
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

class _FilterBar extends StatelessWidget {
  final StatisticsPaneState state;
  final VoidCallback onRefresh;

  const _FilterBar({required this.state, required this.onRefresh});

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
              'Statistics',
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 12,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          // Zoom level selector
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

class _ChartsBody extends StatelessWidget {
  final StatisticsPaneState state;

  const _ChartsBody({required this.state});

  @override
  Widget build(BuildContext context) {
    if (state.series.isEmpty && state.sessionSummary == null) {
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
        if (state.series.isNotEmpty) ...[
          _ChartSection(
            title: 'Tokens Sent / Received',
            child: Column(
              children: [
                TelemetryChart(
                  series: state.series,
                  metric: MetricType.tokensSent,
                  zoomLevel: state.zoomLevel,
                  showLabel: true,
                ),
                const SizedBox(height: 4),
                TelemetryChart(
                  series: state.series,
                  metric: MetricType.tokensReceived,
                  zoomLevel: state.zoomLevel,
                  showLabel: true,
                ),
              ],
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
            child: Column(
              children: [
                TelemetryChart(
                  series: state.series,
                  metric: MetricType.turnCount,
                  zoomLevel: state.zoomLevel,
                  showLabel: true,
                ),
                const SizedBox(height: 4),
                TelemetryChart(
                  series: state.series,
                  metric: MetricType.toolCallCount,
                  zoomLevel: state.zoomLevel,
                  showLabel: true,
                ),
              ],
            ),
          ),
        ],
        if (state.sessionSummary != null) ...[
          const SizedBox(height: 12),
          _SessionSummaryCard(summary: state.sessionSummary!),
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
// Session summary card
// ------------------------------------------------------------------

class _SessionSummaryCard extends StatelessWidget {
  final SessionTelemetrySummary summary;

  const _SessionSummaryCard({required this.summary});

  @override
  Widget build(BuildContext context) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Session Summary',
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 11,
            fontWeight: FontWeight.w600,
          ),
        ),
        const SizedBox(height: 6),
        // Stat pills
        Wrap(
          spacing: 6,
          runSpacing: 4,
          children: [
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
            if (summary.errorRate > 0)
              _StatPill(
                label: 'Error rate',
                value: '${(summary.errorRate * 100).toStringAsFixed(1)}%',
                isWarning: true,
              ),
          ],
        ),
        const SizedBox(height: 8),
        // Turn table
        if (summary.turns.isNotEmpty) ...[
          Text(
            'Turns',
            style: TextStyle(
              color: context.appColors.textMuted,
              fontSize: 10,
              fontWeight: FontWeight.w500,
            ),
          ),
          const SizedBox(height: 4),
          ...summary.turns.map((t) => _TurnRow(turn: t)),
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

class _TurnRow extends StatelessWidget {
  final TurnSummary turn;

  const _TurnRow({required this.turn});

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: [
          SizedBox(
            width: 24,
            child: Text(
              '#${turn.turnIndex}',
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 10,
                fontWeight: FontWeight.w600,
              ),
            ),
          ),
          Expanded(
            child: Text(
              '${_fmtTokens(turn.inputTokens)}in / ${_fmtTokens(turn.outputTokens)}out'
              ' · ${turn.toolCalls} tools'
              '${turn.llmDurationMs != null ? ' · ${turn.llmDurationMs}ms' : ''}',
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 10,
              ),
              overflow: TextOverflow.ellipsis,
            ),
          ),
          if (turn.interrupted)
            Icon(
              Icons.warning_amber_rounded,
              size: 10,
              color: Colors.orange.withAlpha(180),
            ),
        ],
      ),
    );
  }

  String _fmtTokens(int n) {
    if (n >= 1000) return '${(n / 1000).toStringAsFixed(1)}k';
    return '$n';
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
