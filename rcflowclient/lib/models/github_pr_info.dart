/// Model for a cached GitHub pull request returned by the RCFlow backend.
class GithubPrInfo {
  final String id; // local UUID
  final String githubId;
  final String repoOwner;
  final String repoName;
  final int number;
  String title;
  String? body;
  String state; // open|closed|merged
  bool draft;
  // GitHub reviewDecision: APPROVED | CHANGES_REQUESTED | REVIEW_REQUIRED | null.
  String? reviewDecision;
  // GitHub mergeable: MERGEABLE | CONFLICTING | UNKNOWN | null.
  String? mergeStatus;
  // Local checkout this worker maps the repo to (null = no clone on this worker).
  String? projectName;
  String? projectPath;
  final String author;
  final String? authorAvatarUrl;
  final String url;
  final String baseRef;
  final String headRef;
  final String headSha;
  int additions;
  int deletions;
  int changedFiles;
  String role; // for_me|created
  final DateTime createdAt;
  DateTime updatedAt;
  DateTime syncedAt;
  String? taskId; // linked local task UUID (nullable)
  final String workerId;
  final String workerName;

  GithubPrInfo({
    required this.id,
    required this.githubId,
    required this.repoOwner,
    required this.repoName,
    required this.number,
    required this.title,
    this.body,
    required this.state,
    required this.draft,
    this.reviewDecision,
    this.mergeStatus,
    this.projectName,
    this.projectPath,
    required this.author,
    this.authorAvatarUrl,
    required this.url,
    required this.baseRef,
    required this.headRef,
    required this.headSha,
    required this.additions,
    required this.deletions,
    required this.changedFiles,
    required this.role,
    required this.createdAt,
    required this.updatedAt,
    required this.syncedAt,
    this.taskId,
    required this.workerId,
    required this.workerName,
  });

  factory GithubPrInfo.fromJson(
    Map<String, dynamic> json, {
    String workerId = '',
    String workerName = '',
  }) {
    return GithubPrInfo(
      id: json['id'] as String,
      githubId: json['github_id']?.toString() ?? '',
      repoOwner: json['repo_owner'] as String? ?? '',
      repoName: json['repo_name'] as String? ?? '',
      number: (json['number'] as num?)?.toInt() ?? 0,
      title: json['title'] as String? ?? '',
      body: json['body'] as String?,
      state: json['state'] as String? ?? 'open',
      draft: json['draft'] as bool? ?? false,
      reviewDecision: json['review_decision'] as String?,
      mergeStatus: json['merge_status'] as String?,
      projectName: json['project_name'] as String?,
      projectPath: json['project_path'] as String?,
      author: json['author'] as String? ?? '',
      authorAvatarUrl: json['author_avatar_url'] as String?,
      url: json['url'] as String? ?? '',
      baseRef: json['base_ref'] as String? ?? '',
      headRef: json['head_ref'] as String? ?? '',
      headSha: json['head_sha'] as String? ?? '',
      additions: (json['additions'] as num?)?.toInt() ?? 0,
      deletions: (json['deletions'] as num?)?.toInt() ?? 0,
      changedFiles: (json['changed_files'] as num?)?.toInt() ?? 0,
      role: json['role'] as String? ?? 'for_me',
      createdAt:
          DateTime.tryParse(json['created_at'] as String? ?? '') ??
          DateTime.now(),
      updatedAt:
          DateTime.tryParse(json['updated_at'] as String? ?? '') ??
          DateTime.now(),
      syncedAt:
          DateTime.tryParse(json['synced_at'] as String? ?? '') ??
          DateTime.now(),
      taskId: json['task_id'] as String?,
      workerId: workerId,
      workerName: workerName,
    );
  }

  GithubPrInfo copyWith({
    String? title,
    String? body,
    String? state,
    bool? draft,
    String? reviewDecision,
    String? mergeStatus,
    String? projectName,
    String? projectPath,
    int? additions,
    int? deletions,
    int? changedFiles,
    String? role,
    DateTime? updatedAt,
    DateTime? syncedAt,
    String? taskId,
    bool clearTaskId = false,
  }) {
    return GithubPrInfo(
      id: id,
      githubId: githubId,
      repoOwner: repoOwner,
      repoName: repoName,
      number: number,
      title: title ?? this.title,
      body: body ?? this.body,
      state: state ?? this.state,
      draft: draft ?? this.draft,
      reviewDecision: reviewDecision ?? this.reviewDecision,
      mergeStatus: mergeStatus ?? this.mergeStatus,
      projectName: projectName ?? this.projectName,
      projectPath: projectPath ?? this.projectPath,
      author: author,
      authorAvatarUrl: authorAvatarUrl,
      url: url,
      baseRef: baseRef,
      headRef: headRef,
      headSha: headSha,
      additions: additions ?? this.additions,
      deletions: deletions ?? this.deletions,
      changedFiles: changedFiles ?? this.changedFiles,
      role: role ?? this.role,
      createdAt: createdAt,
      updatedAt: updatedAt ?? this.updatedAt,
      syncedAt: syncedAt ?? this.syncedAt,
      taskId: clearTaskId ? null : (taskId ?? this.taskId),
      workerId: workerId,
      workerName: workerName,
    );
  }

  /// Convenience slug "owner/repo".
  String get repoSlug => '$repoOwner/$repoName';

  /// Whether the pull request has been merged.
  bool get isMerged => state == 'merged';
}
