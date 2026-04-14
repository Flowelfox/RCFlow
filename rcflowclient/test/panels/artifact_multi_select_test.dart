/// Unit tests for [computeFlatVisibleArtifactList], the pure helper used by
/// [ArtifactListPanel] to build the ordered flat artifact list for Shift+click
/// range selection.
library;

import 'package:flutter_test/flutter_test.dart';
import 'package:rcflowclient/models/artifact_info.dart';
import 'package:rcflowclient/ui/widgets/session_panel/artifact_list_panel.dart';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

final _now = DateTime(2025);

ArtifactInfo _artifact({
  required String id,
  String workerId = 'w1',
  String workerName = 'Alpha',
  String? projectName = 'ProjectA',
  DateTime? discoveredAt,
}) =>
    ArtifactInfo(
      artifactId: id,
      filePath: '/path/$id.txt',
      fileName: '$id.txt',
      fileExtension: '.txt',
      fileSize: 100,
      workerId: workerId,
      workerName: workerName,
      projectName: projectName,
      discoveredAt: discoveredAt ?? _now,
    );

String _projectKey(String workerId, String? projectName) =>
    '$workerId:${projectName ?? '_other'}';

Map<String, Map<String?, List<ArtifactInfo>>> _group(
    List<ArtifactInfo> artifacts) {
  final grouped = <String, Map<String?, List<ArtifactInfo>>>{};
  for (final a in artifacts) {
    grouped
        .putIfAbsent(a.workerId, () => {})
        .putIfAbsent(a.projectName, () => [])
        .add(a);
  }
  return grouped;
}

// ---------------------------------------------------------------------------
// computeFlatVisibleArtifactList — grouped by project
// ---------------------------------------------------------------------------

void main() {
  group('computeFlatVisibleArtifactList (grouped by project)', () {
    test('returns all artifacts when everything is expanded', () {
      final artifacts = [
        _artifact(id: 'a', projectName: 'ProjectA'),
        _artifact(id: 'b', projectName: 'ProjectB'),
        _artifact(id: 'c', projectName: 'ProjectA'),
      ];
      final grouped = _group(artifacts);
      final result = computeFlatVisibleArtifactList(
        filteredArtifacts: artifacts,
        grouped: grouped,
        workerOrder: ['w1'],
        hasMultipleWorkers: false,
        expandedWorkers: {'w1'},
        groupByProject: true,
        expandedProjects: {'w1:ProjectA', 'w1:ProjectB'},
        projectKey: _projectKey,
      );
      expect(result.length, 3);
      expect(result.map((a) => a.artifactId), containsAll(['a', 'b', 'c']));
    });

    test('excludes artifacts in collapsed project groups', () {
      final artifacts = [
        _artifact(id: 'a', projectName: 'ProjectA'),
        _artifact(id: 'b', projectName: 'ProjectB'),
      ];
      final grouped = _group(artifacts);
      final result = computeFlatVisibleArtifactList(
        filteredArtifacts: artifacts,
        grouped: grouped,
        workerOrder: ['w1'],
        hasMultipleWorkers: false,
        expandedWorkers: {'w1'},
        groupByProject: true,
        expandedProjects: {'w1:ProjectA'}, // ProjectB collapsed
        projectKey: _projectKey,
      );
      expect(result.map((a) => a.artifactId), equals(['a']));
    });

    test('excludes artifacts in collapsed worker groups', () {
      final artifacts = [
        _artifact(id: 'a', workerId: 'w1', workerName: 'Alpha'),
        _artifact(id: 'b', workerId: 'w2', workerName: 'Beta'),
      ];
      final grouped = _group(artifacts);
      final result = computeFlatVisibleArtifactList(
        filteredArtifacts: artifacts,
        grouped: grouped,
        workerOrder: ['w1', 'w2'],
        hasMultipleWorkers: true,
        expandedWorkers: {'w1'}, // w2 collapsed
        groupByProject: true,
        expandedProjects: {'w1:ProjectA', 'w2:ProjectA'},
        projectKey: _projectKey,
      );
      expect(result.map((a) => a.artifactId), equals(['a']));
    });

    test('returns empty list when all groups are collapsed', () {
      final artifacts = [
        _artifact(id: 'a', projectName: 'ProjectA'),
        _artifact(id: 'b', projectName: 'ProjectB'),
      ];
      final grouped = _group(artifacts);
      final result = computeFlatVisibleArtifactList(
        filteredArtifacts: artifacts,
        grouped: grouped,
        workerOrder: ['w1'],
        hasMultipleWorkers: false,
        expandedWorkers: {'w1'},
        groupByProject: true,
        expandedProjects: {}, // all collapsed
        projectKey: _projectKey,
      );
      expect(result, isEmpty);
    });

    test('returns empty list when input is empty', () {
      final result = computeFlatVisibleArtifactList(
        filteredArtifacts: [],
        grouped: {},
        workerOrder: ['w1'],
        hasMultipleWorkers: false,
        expandedWorkers: {'w1'},
        groupByProject: true,
        expandedProjects: {},
        projectKey: _projectKey,
      );
      expect(result, isEmpty);
    });

    test('global (null project) artifacts are included when expanded', () {
      final artifacts = [
        _artifact(id: 'a', projectName: null),
        _artifact(id: 'b', projectName: 'ProjectA'),
      ];
      final grouped = _group(artifacts);
      final result = computeFlatVisibleArtifactList(
        filteredArtifacts: artifacts,
        grouped: grouped,
        workerOrder: ['w1'],
        hasMultipleWorkers: false,
        expandedWorkers: {'w1'},
        groupByProject: true,
        expandedProjects: {'w1:ProjectA', 'w1:_other'},
        projectKey: _projectKey,
      );
      expect(result.length, 2);
      expect(result.map((a) => a.artifactId), containsAll(['a', 'b']));
    });
  });

  // ---------------------------------------------------------------------------
  // computeFlatVisibleArtifactList — flat mode (no project grouping)
  // ---------------------------------------------------------------------------

  group('computeFlatVisibleArtifactList (flat mode)', () {
    test('returns all artifacts sorted by discoveredAt descending', () {
      final artifacts = [
        _artifact(
          id: 'a',
          discoveredAt: DateTime(2025, 1, 1),
        ),
        _artifact(
          id: 'b',
          discoveredAt: DateTime(2025, 3, 1),
        ),
        _artifact(
          id: 'c',
          discoveredAt: DateTime(2025, 2, 1),
        ),
      ];
      final grouped = _group(artifacts);
      final result = computeFlatVisibleArtifactList(
        filteredArtifacts: artifacts,
        grouped: grouped,
        workerOrder: ['w1'],
        hasMultipleWorkers: false,
        expandedWorkers: {'w1'},
        groupByProject: false,
        expandedProjects: {},
        projectKey: _projectKey,
      );
      expect(result.map((a) => a.artifactId), equals(['b', 'c', 'a']));
    });

    test('ignores project expansion state in flat mode', () {
      final artifacts = [
        _artifact(id: 'a', projectName: 'ProjectA'),
        _artifact(id: 'b', projectName: 'ProjectB'),
      ];
      final grouped = _group(artifacts);
      final result = computeFlatVisibleArtifactList(
        filteredArtifacts: artifacts,
        grouped: grouped,
        workerOrder: ['w1'],
        hasMultipleWorkers: false,
        expandedWorkers: {'w1'},
        groupByProject: false,
        expandedProjects: {}, // all collapsed — shouldn't matter
        projectKey: _projectKey,
      );
      expect(result.length, 2);
    });

    test('still respects worker collapse in flat mode', () {
      final artifacts = [
        _artifact(id: 'a', workerId: 'w1', workerName: 'Alpha'),
        _artifact(id: 'b', workerId: 'w2', workerName: 'Beta'),
      ];
      final grouped = _group(artifacts);
      final result = computeFlatVisibleArtifactList(
        filteredArtifacts: artifacts,
        grouped: grouped,
        workerOrder: ['w1', 'w2'],
        hasMultipleWorkers: true,
        expandedWorkers: {'w2'}, // w1 collapsed
        groupByProject: false,
        expandedProjects: {},
        projectKey: _projectKey,
      );
      expect(result.map((a) => a.artifactId), equals(['b']));
    });
  });

  // ---------------------------------------------------------------------------
  // Range selection arithmetic (index-based, pure logic)
  // ---------------------------------------------------------------------------

  group('range selection indices', () {
    List<String> rangeSelect(
        List<ArtifactInfo> flatList, int anchor, int target) {
      final lo = anchor < target ? anchor : target;
      final hi = anchor < target ? target : anchor;
      return [
        for (var i = lo; i <= hi; i++)
          if (i < flatList.length) flatList[i].artifactId,
      ];
    }

    test('forward range (anchor < target)', () {
      final flat = [
        _artifact(id: 'a'),
        _artifact(id: 'b'),
        _artifact(id: 'c'),
        _artifact(id: 'd'),
      ];
      expect(rangeSelect(flat, 0, 2), equals(['a', 'b', 'c']));
    });

    test('backward range (anchor > target)', () {
      final flat = [
        _artifact(id: 'a'),
        _artifact(id: 'b'),
        _artifact(id: 'c'),
        _artifact(id: 'd'),
      ];
      expect(rangeSelect(flat, 3, 1), equals(['b', 'c', 'd']));
    });

    test('single-item range (anchor == target)', () {
      final flat = [_artifact(id: 'a'), _artifact(id: 'b')];
      expect(rangeSelect(flat, 1, 1), equals(['b']));
    });

    test('target beyond list length is clamped', () {
      final flat = [_artifact(id: 'a'), _artifact(id: 'b')];
      expect(rangeSelect(flat, 0, 5), equals(['a', 'b']));
    });
  });

  // ---------------------------------------------------------------------------
  // Single-worker mode skips worker expansion check
  // ---------------------------------------------------------------------------

  group('single worker mode', () {
    test('does not require worker in expandedWorkers set', () {
      final artifacts = [
        _artifact(id: 'a', projectName: 'ProjectA'),
      ];
      final grouped = _group(artifacts);
      final result = computeFlatVisibleArtifactList(
        filteredArtifacts: artifacts,
        grouped: grouped,
        workerOrder: ['w1'],
        hasMultipleWorkers: false,
        expandedWorkers: {}, // empty — doesn't matter for single worker
        groupByProject: true,
        expandedProjects: {'w1:ProjectA'},
        projectKey: _projectKey,
      );
      expect(result.map((a) => a.artifactId), equals(['a']));
    });
  });
}
