class SelectOption {
  final String value;
  final String label;

  const SelectOption({required this.value, required this.label});

  factory SelectOption.fromJson(Map<String, dynamic> json) {
    return SelectOption(
      value: json['value'] as String,
      label: json['label'] as String,
    );
  }
}

class VisibleWhen {
  final String key;
  final String? value;
  final String? valueNot;
  final List<String>? valueIn;

  const VisibleWhen({
    required this.key,
    this.value,
    this.valueNot,
    this.valueIn,
  });

  factory VisibleWhen.fromJson(Map<String, dynamic> json) {
    List<String>? valueIn;
    final rawValueIn = json['value_in'] as List<dynamic>?;
    if (rawValueIn != null) {
      valueIn = rawValueIn.map((e) => e.toString()).toList();
    }
    return VisibleWhen(
      key: json['key'] as String,
      value: json['value'] as String?,
      valueNot: json['value_not'] as String?,
      valueIn: valueIn,
    );
  }

  bool evaluate(Map<String, dynamic> currentValues) {
    final current = currentValues[key]?.toString() ?? '';
    if (valueIn != null) return valueIn!.contains(current);
    if (value != null) return current == value;
    if (valueNot != null) return current != valueNot;
    return true;
  }
}

class ConfigOption {
  final String key;
  final String label;
  final String type;
  final dynamic value;
  final List<SelectOption>? options;
  final String group;
  final String description;
  final bool required;
  final bool restartRequired;
  final VisibleWhen? visibleWhen;
  final String? providerKey;
  final Map<String, dynamic>? models;

  const ConfigOption({
    required this.key,
    required this.label,
    required this.type,
    required this.value,
    this.options,
    required this.group,
    required this.description,
    required this.required,
    required this.restartRequired,
    this.visibleWhen,
    this.providerKey,
    this.models,
  });

  factory ConfigOption.fromJson(Map<String, dynamic> json) {
    List<SelectOption>? options;
    final rawOptions = json['options'] as List<dynamic>?;
    if (rawOptions != null) {
      options = rawOptions
          .map((o) => SelectOption.fromJson(o as Map<String, dynamic>))
          .toList();
    }

    VisibleWhen? visibleWhen;
    final rawVw = json['visible_when'] as Map<String, dynamic>?;
    if (rawVw != null) {
      visibleWhen = VisibleWhen.fromJson(rawVw);
    }

    return ConfigOption(
      key: json['key'] as String,
      label: json['label'] as String,
      type: json['type'] as String,
      value: json['value'],
      options: options,
      group: json['group'] as String,
      description: json['description'] as String,
      required: json['required'] as bool? ?? false,
      restartRequired: json['restart_required'] as bool? ?? false,
      visibleWhen: visibleWhen,
      providerKey: json['provider_key'] as String?,
      models: json['models'] as Map<String, dynamic>?,
    );
  }
}
