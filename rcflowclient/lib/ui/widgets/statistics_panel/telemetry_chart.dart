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
  final ZoomLevel zoomLevel;
  final void Function(BucketPoint)? onBucketTapped;

  /// When true, renders the metric name as a small overlay label inside
  /// the chart area — useful when multiple charts are stacked together
  /// under the same section heading.
  final bool showLabel;

  const TelemetryChart({
    super.key,
    required this.series,
    required this.metric,
    required this.zoomLevel,
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

    if (spots.isEmpty) {
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

    final maxY = spots.map((s) => s.y).reduce((a, b) => a > b ? a : b);
    final midY = maxY / 2;
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
          getDrawingHorizontalLine: (v) => FlLine(
            color: context.appColors.divider,
            strokeWidth: 0.5,
          ),
        ),
        borderData: FlBorderData(show: false),
        titlesData: FlTitlesData(
          leftTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 44,
              getTitlesWidget: (val, meta) {
                // Show labels at 0, midpoint, and max.
                final isMax = (val - meta.max).abs() < meta.max * 0.05;
                final isMid = midY > 0 && (val - midY).abs() < meta.max * 0.05;
                final isZero = val == 0;
                if (isMax || isMid || isZero) {
                  final label = val >= 1000
                      ? '${(val / 1000).toStringAsFixed(1)}k'
                      : val.toStringAsFixed(0);
                  return Text(
                    label,
                    style: TextStyle(
                        color: context.appColors.textMuted, fontSize: 9),
                  );
                }
                return const SizedBox.shrink();
              },
            ),
          ),
          bottomTitles: AxisTitles(
            sideTitles: SideTitles(
              showTitles: true,
              reservedSize: 14,
              getTitlesWidget: (val, meta) {
                final idx = val.round();
                // Show labels only at first and last data points.
                if (idx == 0 || idx == lastIdx) {
                  return Padding(
                    padding: const EdgeInsets.only(top: 2),
                    child: Text(
                      fmtBucket(series[idx].bucket),
                      style: TextStyle(
                          color: context.appColors.textMuted, fontSize: 8),
                    ),
                  );
                }
                return const SizedBox.shrink();
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
            getTooltipItems: (spots) => spots.map((s) {
              final val = s.y >= 1000
                  ? '${(s.y / 1000).toStringAsFixed(1)}k'
                  : s.y.toStringAsFixed(s.y < 10 ? 1 : 0);
              return LineTooltipItem(
                val,
                TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 11,
                    fontWeight: FontWeight.w600),
              );
            }).toList(),
          ),
        ),
        lineBarsData: [
          LineChartBarData(
            spots: spots,
            isCurved: true,
            curveSmoothness: 0.3,
            color: context.appColors.accent,
            barWidth: 2,
            dotData: const FlDotData(show: false),
            belowBarData: BarAreaData(
              show: true,
              color: context.appColors.accent.withAlpha(30),
            ),
          ),
        ],
      ),
    );

    if (!showLabel) {
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
            child: Text(
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
