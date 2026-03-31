import 'dart:math';

const _kValidAgents = {'codex', 'opencode', 'claude_code'};

/// Maps internal agent names to their mention-form (PascalCase) equivalents,
/// matching the `mention_name` the backend derives from `display_name`.
const kAgentMentionNames = <String, String>{
  'codex': 'Codex',
  'opencode': 'OpenCode',
  'claude_code': 'ClaudeCode',
};

class WorkerConfig {
  final String id;
  String name;
  String host;
  int port;
  String apiKey;
  bool useSSL;
  bool allowSelfSigned;
  bool autoConnect;
  int sortOrder;

  /// Default coding agent for new sessions on this worker.
  /// One of ``'codex'``, ``'opencode'``, ``'claude_code'``, or null (no default).
  String? defaultAgent;

  WorkerConfig({
    required this.id,
    required this.name,
    required this.host,
    this.port = 53890,
    required this.apiKey,
    this.useSSL = false,
    this.allowSelfSigned = true,
    this.autoConnect = true,
    this.sortOrder = 0,
    this.defaultAgent,
  });

  /// Combined host:port string for use in URLs.
  String get hostWithPort => '$host:$port';

  factory WorkerConfig.fromJson(Map<String, dynamic> json) {
    // Support legacy format where host included the port (e.g. "192.168.1.100:8765")
    final rawHost = json['host'] as String;
    final legacyPort = json['port'];
    String host;
    int port;
    if (legacyPort != null) {
      host = rawHost;
      port = legacyPort is int
          ? legacyPort
          : int.tryParse(legacyPort.toString()) ?? 53890;
    } else if (rawHost.contains(':')) {
      final parts = rawHost.split(':');
      host = parts[0];
      port = int.tryParse(parts[1]) ?? 53890;
    } else {
      host = rawHost;
      port = 53890;
    }

    final rawAgent = json['default_agent'] as String?;

    return WorkerConfig(
      id: json['id'] as String,
      name: json['name'] as String,
      host: host,
      port: port,
      apiKey: json['api_key'] as String,
      useSSL: json['use_ssl'] as bool? ?? false,
      allowSelfSigned: json['allow_self_signed'] as bool? ?? true,
      autoConnect: json['auto_connect'] as bool? ?? true,
      sortOrder: json['sort_order'] as int? ?? 0,
      defaultAgent: (rawAgent != null && _kValidAgents.contains(rawAgent))
          ? rawAgent
          : null,
    );
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'name': name,
    'host': host,
    'port': port,
    'api_key': apiKey,
    'use_ssl': useSSL,
    'allow_self_signed': allowSelfSigned,
    'auto_connect': autoConnect,
    'sort_order': sortOrder,
    if (defaultAgent != null) 'default_agent': defaultAgent,
  };

  WorkerConfig copyWith({
    String? id,
    String? name,
    String? host,
    int? port,
    String? apiKey,
    bool? useSSL,
    bool? allowSelfSigned,
    bool? autoConnect,
    int? sortOrder,
    Object? defaultAgent = _sentinel,
  }) {
    return WorkerConfig(
      id: id ?? this.id,
      name: name ?? this.name,
      host: host ?? this.host,
      port: port ?? this.port,
      apiKey: apiKey ?? this.apiKey,
      useSSL: useSSL ?? this.useSSL,
      allowSelfSigned: allowSelfSigned ?? this.allowSelfSigned,
      autoConnect: autoConnect ?? this.autoConnect,
      sortOrder: sortOrder ?? this.sortOrder,
      defaultAgent: defaultAgent == _sentinel
          ? this.defaultAgent
          : defaultAgent as String?,
    );
  }

  static String generateId() {
    final random = Random.secure();
    final bytes = List.generate(16, (_) => random.nextInt(256));
    // Format as UUID v4-like string
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    final hex = bytes.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
    return '${hex.substring(0, 8)}-${hex.substring(8, 12)}-'
        '${hex.substring(12, 16)}-${hex.substring(16, 20)}-'
        '${hex.substring(20, 32)}';
  }
}

const _sentinel = Object();
