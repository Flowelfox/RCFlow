/// State management for the Statistics pane.
library;

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart' show DateTimeRange;

import '../models/telemetry.dart';

class StatisticsPaneState extends ChangeNotifier {
  ZoomLevel _zoomLevel = ZoomLevel.hour;
  ZoomLevel get zoomLevel => _zoomLevel;

  DateTimeRange? _timeRange;
  DateTimeRange? get timeRange => _timeRange;

  String? _sessionFilter;
  String? get sessionFilter => _sessionFilter;

  List<BucketPoint> _series = [];
  List<BucketPoint> get series => _series;

  SessionTelemetrySummary? _sessionSummary;
  SessionTelemetrySummary? get sessionSummary => _sessionSummary;

  bool _loading = false;
  bool get loading => _loading;

  String? _error;
  String? get error => _error;

  void setZoomLevel(ZoomLevel level) {
    if (_zoomLevel == level) return;
    _zoomLevel = level;
    _timeRange = null; // reset to default window for the new zoom level
    notifyListeners();
  }

  void setSessionFilter(String? sessionId) {
    _sessionFilter = sessionId;
    notifyListeners();
  }

  void setTimeRange(DateTimeRange? range) {
    _timeRange = range;
    notifyListeners();
  }

  void setSeries(List<BucketPoint> series) {
    _series = series;
    _loading = false;
    _error = null;
    notifyListeners();
  }

  void setSessionSummary(SessionTelemetrySummary? summary) {
    _sessionSummary = summary;
    notifyListeners();
  }

  void setLoading() {
    _loading = true;
    _error = null;
    notifyListeners();
  }

  void setError(String message) {
    _loading = false;
    _error = message;
    notifyListeners();
  }

  /// Compute the effective time range using zoom-level defaults when not overridden.
  DateTimeRange effectiveRange() {
    if (_timeRange != null) return _timeRange!;
    final now = DateTime.now().toUtc();
    final start = now.subtract(_zoomLevel.defaultWindowDuration);
    return DateTimeRange(start: start, end: now);
  }
}
