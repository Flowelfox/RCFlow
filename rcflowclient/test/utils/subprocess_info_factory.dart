/// Test factory helpers for [SubprocessInfo].
library;

import 'package:rcflowclient/models/subprocess_info.dart';

/// Returns a [SubprocessInfo] with sensible defaults.
///
/// All parameters are optional — override only the fields relevant to each test.
SubprocessInfo makeSubprocessInfo({
  String subprocessType = 'claude_code',
  String displayName = 'Claude Code',
  String workingDirectory = '/home/user/project',
  String? currentTool,
  DateTime? startedAt,
}) {
  return SubprocessInfo(
    subprocessType: subprocessType,
    displayName: displayName,
    workingDirectory: workingDirectory,
    currentTool: currentTool,
    startedAt: startedAt ?? DateTime.utc(2026, 3, 20, 12, 0, 0),
  );
}

/// Returns a [SubprocessInfo] for a Codex subprocess.
SubprocessInfo makeCodexSubprocessInfo({
  String workingDirectory = '/home/user/project',
  String? currentTool,
}) {
  return makeSubprocessInfo(
    subprocessType: 'codex',
    displayName: 'Codex',
    workingDirectory: workingDirectory,
    currentTool: currentTool,
  );
}
