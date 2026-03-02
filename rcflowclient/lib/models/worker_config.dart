import 'dart:math';

class WorkerConfig {
  final String id;
  String name;
  String host;
  String apiKey;
  bool useSSL;
  bool allowSelfSigned;
  bool autoConnect;
  int sortOrder;

  WorkerConfig({
    required this.id,
    required this.name,
    required this.host,
    required this.apiKey,
    this.useSSL = false,
    this.allowSelfSigned = true,
    this.autoConnect = true,
    this.sortOrder = 0,
  });

  factory WorkerConfig.fromJson(Map<String, dynamic> json) {
    return WorkerConfig(
      id: json['id'] as String,
      name: json['name'] as String,
      host: json['host'] as String,
      apiKey: json['api_key'] as String,
      useSSL: json['use_ssl'] as bool? ?? false,
      allowSelfSigned: json['allow_self_signed'] as bool? ?? true,
      autoConnect: json['auto_connect'] as bool? ?? true,
      sortOrder: json['sort_order'] as int? ?? 0,
    );
  }

  Map<String, dynamic> toJson() => {
        'id': id,
        'name': name,
        'host': host,
        'api_key': apiKey,
        'use_ssl': useSSL,
        'allow_self_signed': allowSelfSigned,
        'auto_connect': autoConnect,
        'sort_order': sortOrder,
      };

  WorkerConfig copyWith({
    String? id,
    String? name,
    String? host,
    String? apiKey,
    bool? useSSL,
    bool? allowSelfSigned,
    bool? autoConnect,
    int? sortOrder,
  }) {
    return WorkerConfig(
      id: id ?? this.id,
      name: name ?? this.name,
      host: host ?? this.host,
      apiKey: apiKey ?? this.apiKey,
      useSSL: useSSL ?? this.useSSL,
      allowSelfSigned: allowSelfSigned ?? this.allowSelfSigned,
      autoConnect: autoConnect ?? this.autoConnect,
      sortOrder: sortOrder ?? this.sortOrder,
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
