class ArtifactInfo {
  final String artifactId;
  final String filePath;
  final String fileName;
  final String fileExtension;
  final int fileSize;
  final String? mimeType;
  final DateTime? discoveredAt;
  final DateTime? modifiedAt;
  final String? sessionId;
  final String workerId;
  final String workerName;

  ArtifactInfo({
    required this.artifactId,
    required this.filePath,
    required this.fileName,
    required this.fileExtension,
    required this.fileSize,
    this.mimeType,
    this.discoveredAt,
    this.modifiedAt,
    this.sessionId,
    required this.workerId,
    required this.workerName,
  });

  factory ArtifactInfo.fromJson(Map<String, dynamic> json, {
    String workerId = '',
    String workerName = '',
  }) {
    DateTime? discoveredAt;
    final discRaw = json['discovered_at'] as String?;
    if (discRaw != null && discRaw.isNotEmpty) {
      discoveredAt = DateTime.tryParse(discRaw);
    }

    DateTime? modifiedAt;
    final modRaw = json['modified_at'] as String?;
    if (modRaw != null && modRaw.isNotEmpty) {
      modifiedAt = DateTime.tryParse(modRaw);
    }

    return ArtifactInfo(
      artifactId: json['artifact_id'] as String,
      filePath: json['file_path'] as String? ?? '',
      fileName: json['file_name'] as String? ?? '',
      fileExtension: json['file_extension'] as String? ?? '',
      fileSize: json['file_size'] as int? ?? 0,
      mimeType: json['mime_type'] as String?,
      discoveredAt: discoveredAt,
      modifiedAt: modifiedAt,
      sessionId: json['session_id'] as String?,
      workerId: workerId,
      workerName: workerName,
    );
  }

  ArtifactInfo copyWith({
    String? fileName,
    int? fileSize,
    DateTime? modifiedAt,
    String? sessionId,
  }) {
    return ArtifactInfo(
      artifactId: artifactId,
      filePath: filePath,
      fileName: fileName ?? this.fileName,
      fileExtension: fileExtension,
      fileSize: fileSize ?? this.fileSize,
      mimeType: mimeType,
      discoveredAt: discoveredAt,
      modifiedAt: modifiedAt ?? this.modifiedAt,
      sessionId: sessionId ?? this.sessionId,
      workerId: workerId,
      workerName: workerName,
    );
  }

  String get displaySize {
    if (fileSize < 1024) {
      return '$fileSize B';
    } else if (fileSize < 1024 * 1024) {
      return '${(fileSize / 1024).toStringAsFixed(1)} KB';
    } else if (fileSize < 1024 * 1024 * 1024) {
      return '${(fileSize / (1024 * 1024)).toStringAsFixed(1)} MB';
    } else {
      return '${(fileSize / (1024 * 1024 * 1024)).toStringAsFixed(1)} GB';
    }
  }

  bool get isMarkdown => fileExtension.toLowerCase() == '.md' ||
                         fileExtension.toLowerCase() == '.markdown';

  bool get isTextFile {
    final ext = fileExtension.toLowerCase();
    return [
      '.txt', '.log', '.json', '.yaml', '.yml', '.toml', '.xml',
      '.py', '.js', '.ts', '.jsx', '.tsx', '.dart', '.java', '.cpp', '.c',
      '.h', '.hpp', '.cs', '.go', '.rs', '.rb', '.php', '.swift', '.kt',
      '.sh', '.bash', '.zsh', '.fish', '.ps1', '.bat', '.cmd',
      '.html', '.css', '.scss', '.sass', '.less',
      '.md', '.markdown', '.rst', '.tex',
      '.ini', '.cfg', '.conf', '.env', '.gitignore', '.dockerignore',
    ].contains(ext);
  }
}