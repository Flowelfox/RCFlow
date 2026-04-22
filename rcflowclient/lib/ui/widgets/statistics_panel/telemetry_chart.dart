/// Reusable chart widget for a single telemetry metric over time.
///
/// Uses fl_chart's LineChart for latency/count metrics and BarChart for
/// token stacked-area style display (approximated with grouped bars).
library;

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../../../models/telemetry.dart';
import '../../../theme.dart';

/// Which metric to display on the Y axis.
enum MetricType {
  tokensSent,
  tokensReceived,
  avgLlmDurationMs,
  avgToolDurationMs,
  turnCount,
  toolCallCount,
  errorCount,
}

extension MetricTypeExt on MetricType {
  String get label {
    switch (this) {
      case MetricType.tokensSent:
        return 'Tokens Sent';
      case MetricType.tokensReceived:
        return 'Tokens Received';
      case MetricType.avgLlmDurationMs:
        return 'Avg LLM Duration (ms)';
      case MetricType.avgToolDurationMs:
        return 'Avg Tool Duration (ms)';
      case MetricType.turnCount:
        return 'Turns';
      case MetricType.toolCallCount:
        return 'Tool Calls';
      case MetricType.errorCount:
        return 'Errors';
    }
  }

  double? extractValue(BucketPoint point) {
    switch (this) {
      case MetricType.tokensSent:
        return point.tokensSent.toDouble();
      case MetricType.tokensReceived:
        return point.tokensReceived.toDouble();
      case MetricType.avgLlmDurationMs:
        return point.avgLlmDurationMs;
      case MetricType.avgToolDurationMs:
        return point.avgToolDurationMs;
      case MetricType.turnCount:
        return point.turnCount.toDouble();
      case MetricType.toolCallCount:
        return point.toolCallCount.toDouble();
      case MetricType.errorCount:
        return point.errorCount.toDouble();
    }
  }
}

class TelemetryChart extends StatelessWidget {
  final List<BucketPoint> series;
  final MetricType metric;
  final MetricType? secondaryMetric;
  final ZoomLevel zoomLevel;
  final void Function(BucketPoint)? onBucketTapped;

  /// When true, renders the metric name as a small overlay label inside
  /// the chart area — useful when multiple charts are stacked together
  /// under the same section heading. Ignored when [secondaryMetric] is set
  /// (a two-entry legend is shown instead).
  final bool showLabel;

  const TelemetryChart({
    super.key,
    required this.series,
    required this.metric,
    required this.zoomLevel,
    this.secondaryMetric,
    this.onBucketTapped,
    this.showLabel = false,
  });

  @override
  Widget build(BuildContext context) {
    if (series.isEmpty) {
      return SizedBox(
        height: 120,
        child: Center(
          child: Text(
            'No data',
            style: TextStyle(color: context.appColors.textMuted, fontSize: 12),
          ),
        ),
      );
    }

    final spots = <FlSpot>[];
    for (int i = 0; i < series.length; i++) {
      final v = metric.extractValue(series[i]);
      if (v != null) spots.add(FlSpot(i.toDouble(), v));
    }

    final secondarySpots = <FlSpot>[];
    if (secondaryMetric != null) {
      for (int i = 0; i < series.length; i++) {
        final v = secondaryMetric!.extractValue(series[i]);
        if (v != null) secondarySpots.add(FlSpot(i.toDouble(), v));
      }
    }

    if (spots.isEmpty && secondarySpots.isEmpty) {
      return SizedBox(
        height: 120,
        child: Center(
          child: Text(
            'No ${metric.label} data',
            style: TextStyle(color: context.appColors.textMuted, fontSize: 12),
          ),
        ),
      );
    }

    double rawMaxY = 0;
    for (final s in spots) {
      if (s.y > rawMaxY) rawMaxY = s.y;
    }
    for (final s in secondarySpots) {
      if (s.y > rawMaxY) rawMaxY = s.y;
    }
    final maxY = rawMaxY > 0 ? rawMaxY : 1.0;
    final lastIdx = series.length - 1;

    // Format a bucket timestamp for the X-axis based on zoom level.
    String fmtBucket(DateTime t) {
      switch (zoomLevel) {
        case ZoomLevel.minute:
          return '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
        case ZoomLevel.hour:
          return '${t.hour.toString().padLeft(2, '0')}:00';
        case ZoomLevel.day:
          return '${t.month}/${t.day}';
      }
    }

    final chart = LineChart(
      LineChartData(
        minY: 0,
        maxY: maxY * 1.15,
        gridData: FlGridData(
          show: true,
          drawVerticalLine: false,
          horizontalInterval: maxY / 2,
          getDrawingHorizontalLine: (v) =>
              FlLine(color: context.appColors.divider, strokeWidth: 0.5),
        ),
        borderData: FlBorderData(show: false),
        titlesData: FlTitlesData(
          leftTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 44,
              interval: maxY / 2,
              getTitlesWidget: (val, meta) {
                if (val > maxY + (maxY * 0.001)) {
                  return const SizedBox.shrink();
                }
                final label = val >= 1000
                    ? '${(val / 1000).toStringAsFixed(1)}k'
                    : val.toStringAsFixed(0);
                return Text(
                  label,
                  style: TextStyle(
                    color: context.appColors.textMuted,
                    fontSize: 9,
                  ),
                );
              },
            ),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 14,
              interval: lastIdx > 0 ? lastIdx.toDouble() : 1,
              getTitlesWidget: (val, meta) {
                final idx = val.round();
                if (idx != 0 && idx != lastIdx) {
                  return const SizedBox.shrink();
                }
                return Padding(
                  padding: const EdgeInsets.only(top: 2),
                  child: Text(
                    fmtBucket(series[idx].bucket),
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 8,
                    ),
                  ),
                );
              },
            ),
          ),
          rightTitles: const AxisTitles(
            sideTitles: SideTitles(showTitles: false),
          ),
          topTitles: const AxisTitles(
            sideTitles: SideTitles(showTitles: false),
          ),
        ),
        lineTouchData: LineTouchData(
          touchCallback: (event, response) {
            if (event is FlTapUpEvent &&
                response != null &&
                response.lineBarSpots != null &&
                response.lineBarSpots!.isNotEmpty) {
              final idx = response.lineBarSpots!.first.x.round();
              if (idx >= 0 && idx < series.length) {
                onBucketTapped?.call(series[idx]);
              }
            }
          },
          touchTooltipData: LineTouchTooltipData(
            getTooltipColor: (_) => context.appColors.bgElevated,
            tooltipBorder: BorderSide(
              color: context.appColors.divider,
              width: 0.5,
            ),
            getTooltipItems: (spots) => spots.map((s) {
              final val = s.y >= 1000
                  ? '${(s.y / 1000).toStringAsFixed(1)}k'
                  : s.y.toStringAsFixed(s.y < 10 ? 1 : 0);
              return LineTooltipItem(
                val,
                TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 11,
                  fontWeight: FontWeight.w600,
                ),
              );
            }).toList(),
          ),
        ),
        lineBarsData: [
          LineChartBarData(
            spots: spots,
            isCurved: false,
            color: context.appColors.accent,
            barWidth: 2,
            dotData: const FlDotData(show: false),
            belowBarData: BarAreaData(
              show: secondaryMetric == null,
              color: context.appColors.accent.withAlpha(30),
            ),
          ),
          if (secondaryMetric != null)
            LineChartBarData(
              spots: secondarySpots,
              isCurved: false,
              color: context.appColors.toolAccent,
              barWidth: 2,
              dotData: const FlDotData(show: false),
              belowBarData: BarAreaData(show: false),
            ),
        ],
      ),
    );

    final showLegend = secondaryMetric != null;
    if (!showLabel && !showLegend) {
      return SizedBox(height: 120, child: chart);
    }

    return SizedBox(
      height: 120,
      child: Stack(
        children: [
          chart,
          Positioned(
            top: 4,
            left: 46,
            child: showLegend
                ? Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      _LegendEntry(
                        color: context.appColors.accent,
                        label: metric.label,
                      ),
                      const SizedBox(width: 10),
                      _LegendEntry(
                        color: context.appColors.toolAccent,
                        label: secondaryMetric!.label,
                      ),
                    ],
                  )
                : Text(
                    metric.label,
                    style: TextStyle(
                      color: context.appColors.textMuted,
                      fontSize: 9,
                      fontWeight: FontWeight.w500,
                    ),
                  ),
          ),
        ],
      ),
    );
  }
}

class _LegendEntry extends StatelessWidget {
  final Color color;
  final String label;

  const _LegendEntry({required this.color, required this.label});

  @override
  Widget build(BuildContext context) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(
          width: 8,
          height: 2,
          decoration: BoxDecoration(
            color: color,
            borderRadius: BorderRadius.circular(1),
          ),
        ),
        const SizedBox(width: 4),
        Text(
          label,
          style: TextStyle(
            color: context.appColors.textMuted,
            fontSize: 9,
            fontWeight: FontWeight.w500,
          ),
        ),
      ],
    );
  }
}
