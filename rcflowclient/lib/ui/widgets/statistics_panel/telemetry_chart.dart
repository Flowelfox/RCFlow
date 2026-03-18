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

  const TelemetryChart({
    super.key,
    required this.series,
    required this.metric,
    required this.zoomLevel,
    this.onBucketTapped,
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

    return SizedBox(
      height: 120,
      child: LineChart(
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
                  if (val == meta.max || val == 0) {
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
            bottomTitles: const AxisTitles(
              sideTitles: SideTitles(showTitles: false),
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
      ),
    );
  }
}
