part of 'server_config_screen.dart';

class _ModelSelectField extends StatelessWidget {
  final ConfigOption option;
  final String value;
  final bool isModified;
  final List<Map<String, dynamic>> modelOptions;
  final bool allowCustom;
  final ValueChanged<String> onChanged;
  final TextEditingController? controller;
  final WebSocketService ws;
  final WorkerConnection? connection;
  final String? providerSelection;

  const _ModelSelectField({
    required this.option,
    required this.value,
    required this.isModified,
    required this.modelOptions,
    required this.allowCustom,
    required this.onChanged,
    required this.ws,
    this.controller,
    this.connection,
    this.providerSelection,
  });

  @override
  Widget build(BuildContext context) {
    return _FieldWrapper(
      option: option,
      isModified: isModified,
      child: _DynamicModelInput(
        value: value,
        seedOptions: modelOptions,
        allowCustom: allowCustom,
        onChanged: onChanged,
        controller: controller,
        ws: ws,
        connection: connection,
        dynamicEnabled: option.isDynamic,
        fetchScope: option.fetchScope,
        fetchProvider: option.fetchProvider,
        providerSelection: providerSelection,
        hintText: option.label,
        compact: false,
      ),
    );
  }
}

/// Shared dropdown / text-field widget that knows how to fetch its own
/// option list from ``GET /api/models`` when the schema marks the field
/// as ``dynamic``. Used by both server-config and tool-settings render
/// paths so they stay in sync on cache/refresh behaviour.
class _DynamicModelInput extends StatefulWidget {
  final String value;
  final List<Map<String, dynamic>> seedOptions;
  final bool allowCustom;
  final ValueChanged<String> onChanged;
  final TextEditingController? controller;
  final WebSocketService ws;

  /// Optional [WorkerConnection]; when supplied, the widget subscribes to
  /// its [WorkerConnection.modelCatalogGeneration] map and re-fetches
  /// whenever the host bumps the matching scope (e.g. after the user
  /// saves new credentials in this same dialog or completes a login).
  final WorkerConnection? connection;
  final bool dynamicEnabled;
  final String? fetchScope;
  final String? fetchProvider;
  final String? providerSelection;
  final String hintText;
  final bool compact;

  const _DynamicModelInput({
    required this.value,
    required this.seedOptions,
    required this.allowCustom,
    required this.onChanged,
    required this.ws,
    required this.dynamicEnabled,
    required this.hintText,
    required this.compact,
    this.controller,
    this.connection,
    this.fetchScope,
    this.fetchProvider,
    this.providerSelection,
  });

  @override
  State<_DynamicModelInput> createState() => _DynamicModelInputState();
}

class _DynamicModelInputState extends State<_DynamicModelInput> {
  List<Map<String, dynamic>> _liveOptions = const [];
  bool _loading = false;
  String _source = 'fallback';
  String? _error;
  DateTime? _fetchedAt;
  String? _lastFetchProvider;

  /// Controller passed to the inner DropdownMenu / TextField. We own it
  /// when the parent didn't supply one. Listening to it lets typed text
  /// (custom values not in the catalog) propagate to ``widget.onChanged``
  /// — without the listener, DropdownMenu only fires ``onSelected`` on
  /// entry pick and custom strings would silently disappear on save.
  late final TextEditingController _textController;
  bool _ownsController = false;
  bool _suppressTextListener = false;

  /// Last observed value of the host connection's catalog generation for
  /// ``widget.fetchScope``. When the listener fires and the current
  /// generation differs, we know credentials changed elsewhere in the
  /// form (key save, tool save, login flow) and re-fetch the catalog.
  int _seenCatalogGeneration = 0;

  @override
  void initState() {
    super.initState();
    if (widget.controller != null) {
      _textController = widget.controller!;
    } else {
      _textController = TextEditingController(text: widget.value);
      _ownsController = true;
    }
    _textController.addListener(_onTextChanged);
    _seenCatalogGeneration = _currentCatalogGeneration();
    widget.connection?.addListener(_onConnectionChanged);
    _maybeFetch();
  }

  @override
  void dispose() {
    widget.connection?.removeListener(_onConnectionChanged);
    _textController.removeListener(_onTextChanged);
    if (_ownsController) {
      _textController.dispose();
    }
    super.dispose();
  }

  @override
  void didUpdateWidget(covariant _DynamicModelInput oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.connection != widget.connection) {
      oldWidget.connection?.removeListener(_onConnectionChanged);
      widget.connection?.addListener(_onConnectionChanged);
      _seenCatalogGeneration = _currentCatalogGeneration();
    }
    if (oldWidget.providerSelection != widget.providerSelection ||
        oldWidget.fetchScope != widget.fetchScope ||
        oldWidget.fetchProvider != widget.fetchProvider) {
      _maybeFetch();
    }
  }

  int _currentCatalogGeneration() {
    final scope = widget.fetchScope;
    if (scope == null) return 0;
    return widget.connection?.modelCatalogGeneration[scope] ?? 0;
  }

  void _onConnectionChanged() {
    final current = _currentCatalogGeneration();
    if (current == _seenCatalogGeneration) return;
    _seenCatalogGeneration = current;
    _maybeFetch(refresh: true);
  }

  void _onTextChanged() {
    if (_suppressTextListener) return;
    final text = _textController.text;
    if (text == widget.value) return;
    // DropdownMenu writes the selected entry's *label* into the
    // controller, but the saved value should be the entry's raw id.
    // Map label → id when we can; otherwise treat the text as a custom
    // free-form value.
    final options = _mergedOptions();
    for (final o in options) {
      final lbl = (o['label'] as String?) ?? (o['value'] as String);
      final v = o['value'] as String? ?? '';
      if (v.isEmpty) continue;
      if (lbl == text) {
        if (v != widget.value) widget.onChanged(v);
        return;
      }
    }
    widget.onChanged(text);
  }

  String? _resolveProvider() {
    if (widget.fetchProvider != null && widget.fetchProvider!.isNotEmpty) {
      return widget.fetchProvider;
    }
    final p = widget.providerSelection;
    if (p == null || p.isEmpty) return null;
    return p;
  }

  Future<void> _maybeFetch({bool refresh = false}) async {
    if (!widget.dynamicEnabled) return;
    final scope = widget.fetchScope;
    final provider = _resolveProvider();
    if (scope == null || provider == null) {
      setState(() {
        _liveOptions = const [];
        _loading = false;
        _source = 'fallback';
        _error = null;
        _fetchedAt = null;
      });
      return;
    }
    // Skip duplicate fetch when nothing relevant changed.
    if (!refresh &&
        _lastFetchProvider == provider &&
        _liveOptions.isNotEmpty &&
        _source == 'live') {
      return;
    }
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final body = await widget.ws.fetchModels(
        provider: provider,
        scope: scope,
        refresh: refresh,
      );
      final rawOptions = body['options'] as List<dynamic>? ?? [];
      final options = rawOptions
          .map((o) => Map<String, dynamic>.from(o as Map))
          .toList();
      DateTime? fetchedAt;
      final fetchedAtRaw = body['fetched_at'];
      if (fetchedAtRaw is String) {
        fetchedAt = DateTime.tryParse(fetchedAtRaw);
      }
      if (!mounted) return;
      setState(() {
        _liveOptions = options;
        _source = (body['source'] as String?) ?? 'fallback';
        _fetchedAt = fetchedAt?.toLocal();
        _error = body['error'] as String?;
        _loading = false;
        _lastFetchProvider = provider;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _liveOptions = const [];
        _source = 'fallback';
        _error = e.toString();
        _loading = false;
      });
    }
  }

  /// Merge live options over the seed list. Live values win when both
  /// list the same id; otherwise both sets show up (sorted: live first,
  /// seed-only entries appended).
  List<Map<String, dynamic>> _mergedOptions() {
    if (_liveOptions.isEmpty) return widget.seedOptions;
    final seenValues = <String>{};
    final merged = <Map<String, dynamic>>[];
    for (final o in _liveOptions) {
      final v = o['value'] as String?;
      if (v == null || v.isEmpty) continue;
      seenValues.add(v);
      merged.add(o);
    }
    for (final o in widget.seedOptions) {
      final v = o['value'] as String?;
      if (v == null || seenValues.contains(v)) continue;
      merged.add(o);
    }
    return merged;
  }

  String _statusLabel() {
    if (_loading) return 'Loading models…';
    if (_error != null && _source == 'fallback') return 'Offline (fallback list)';
    if (_source == 'fallback') return 'Fallback list';
    final ts = _fetchedAt;
    if (_source == 'live' && ts != null) {
      return 'Live · ${_formatRelative(ts)}';
    }
    if (_source == 'cached' && ts != null) {
      return 'Cached · ${_formatRelative(ts)}';
    }
    return _source;
  }

  Color _statusColor(BuildContext context) {
    if (_error != null && _source == 'fallback') return Colors.amber.shade600;
    if (_source == 'live') return context.appColors.successText;
    if (_source == 'cached') return context.appColors.textSecondary;
    return context.appColors.textMuted;
  }

  Widget _buildStatusRow(BuildContext context) {
    if (!widget.dynamicEnabled) return const SizedBox.shrink();
    final fontSize = widget.compact ? 10.0 : 11.0;
    return Padding(
      padding: EdgeInsets.only(bottom: widget.compact ? 4 : 6),
      child: Row(
        children: [
          if (_loading)
            SizedBox(
              width: 12,
              height: 12,
              child: CircularProgressIndicator(
                strokeWidth: 1.5,
                color: context.appColors.textSecondary,
              ),
            )
          else
            Icon(Icons.circle, size: 8, color: _statusColor(context)),
          const SizedBox(width: 6),
          Flexible(
            child: Tooltip(
              message: _error ?? '',
              child: Text(
                _statusLabel(),
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  color: context.appColors.textSecondary,
                  fontSize: fontSize,
                ),
              ),
            ),
          ),
          IconButton(
            icon: Icon(
              Icons.refresh,
              size: widget.compact ? 14 : 16,
              color: context.appColors.textSecondary,
            ),
            tooltip: 'Refresh model list',
            onPressed: _loading ? null : () => _maybeFetch(refresh: true),
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(minHeight: 24, minWidth: 24),
          ),
        ],
      ),
    );
  }

  Widget _buildInput(BuildContext context, List<Map<String, dynamic>> options) {
    final colors = context.appColors;
    final textSize = widget.compact ? 13.0 : 14.0;
    final radius = widget.compact ? 8.0 : 10.0;
    final padding = widget.compact
        ? const EdgeInsets.symmetric(horizontal: 10, vertical: kSpace2)
        : const EdgeInsets.symmetric(horizontal: kSpace3, vertical: 10);

    if (options.isEmpty) {
      return TextField(
        controller: _textController,
        style: TextStyle(color: colors.textPrimary, fontSize: textSize),
        decoration: InputDecoration(
          hintText: widget.hintText,
          fillColor: colors.bgElevated,
          filled: true,
          isDense: widget.compact,
          border: OutlineInputBorder(
            borderSide: BorderSide.none,
            borderRadius: BorderRadius.circular(radius),
          ),
          contentPadding: padding,
        ),
      );
    }

    final entries = options
        .map(
          (o) => DropdownMenuEntry<String>(
            value: o['value'] as String,
            label: (o['label'] as String?) ?? (o['value'] as String),
          ),
        )
        .toList();

    // Show a warning glyph on the dropdown when the saved value is a
    // custom string the catalog does not list. The user can still save —
    // this is purely informational so they know the value isn't one we
    // got from the upstream provider.
    final value = widget.value;
    final isCustomValue =
        value.isNotEmpty && !entries.any((e) => e.value == value);
    final iconSize = widget.compact ? 16.0 : 18.0;
    final Widget? warningIcon = isCustomValue
        ? Tooltip(
            message:
                'Model "$value" is not listed in the catalog. '
                'It will still be saved as-is.',
            triggerMode: TooltipTriggerMode.tap,
            waitDuration: const Duration(milliseconds: 200),
            showDuration: const Duration(seconds: 4),
            child: Icon(
              Icons.warning_amber_rounded,
              size: iconSize,
              color: Colors.amber.shade600,
            ),
          )
        : null;

    return DropdownMenu<String>(
      controller: _textController,
      initialSelection:
          entries.any((e) => e.value == widget.value) ? widget.value : null,
      dropdownMenuEntries: entries,
      enableFilter: true,
      enableSearch: true,
      requestFocusOnTap: true,
      leadingIcon: warningIcon,
      // Substring match on BOTH the human label and the raw model id, so
      // typing "5.5" surfaces every entry containing that fragment instead
      // of only those whose label starts with "5.5". When nothing
      // matches, return a single disabled "no matches" entry so the
      // dropdown doesn't render as a blank empty box — the user keeps
      // their typed text and can still save it as a custom value.
      filterCallback: (List<DropdownMenuEntry<String>> entries, String filter) {
        if (filter.isEmpty) return entries;
        final needle = filter.toLowerCase();
        final matches = entries.where((e) {
          final label = e.label.toLowerCase();
          final value = e.value.toLowerCase();
          return label.contains(needle) || value.contains(needle);
        }).toList();
        if (matches.isEmpty) {
          return [
            DropdownMenuEntry<String>(
              value: '__no_match__',
              label: 'No models match "$filter" — value will save as custom',
              enabled: false,
            ),
          ];
        }
        return matches;
      },
      // Default `searchCallback` selects the first prefix-matching entry to
      // highlight while typing. Keep that behaviour off so the cursor
      // doesn't jump while the user is filtering — they can pick from the
      // menu themselves.
      searchCallback: (entries, query) => null,
      expandedInsets: EdgeInsets.zero,
      textStyle: TextStyle(color: colors.textPrimary, fontSize: textSize),
      menuStyle: MenuStyle(
        backgroundColor: WidgetStatePropertyAll(colors.bgElevated),
      ),
      inputDecorationTheme: InputDecorationTheme(
        fillColor: colors.bgElevated,
        filled: true,
        border: OutlineInputBorder(
          borderSide: BorderSide.none,
          borderRadius: BorderRadius.circular(radius),
        ),
        contentPadding: padding,
        hintStyle: TextStyle(
          color: colors.textMuted,
          fontSize: textSize,
        ),
        isDense: true,
      ),
      onSelected: (v) {
        // Suppress the listener while we sync the controller text to the
        // selected entry's id, then re-fire onChanged ourselves. Without
        // the suppression DropdownMenu's own ``controller.text = label``
        // write would briefly mark the value as "custom" and flicker the
        // warning icon between the keystroke and our resync.
        if (v == null || v == '__no_match__') return;
        _suppressTextListener = true;
        try {
          if (_textController.text != v) _textController.text = v;
        } finally {
          _suppressTextListener = false;
        }
        widget.onChanged(v);
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final options = _mergedOptions();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _buildStatusRow(context),
        _buildInput(context, options),
      ],
    );
  }
}

String _formatRelative(DateTime ts) {
  final now = DateTime.now();
  final diff = now.difference(ts);
  if (diff.inSeconds < 30) return 'just now';
  if (diff.inMinutes < 1) return '${diff.inSeconds}s ago';
  if (diff.inHours < 1) return '${diff.inMinutes}m ago';
  if (diff.inDays < 1) return '${diff.inHours}h ago';
  return '${diff.inDays}d ago';
}
