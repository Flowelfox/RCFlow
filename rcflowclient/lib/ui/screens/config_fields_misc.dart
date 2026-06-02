part of 'server_config_screen.dart';

class _BoolField extends StatelessWidget {
  final ConfigOption option;
  final bool value;
  final bool isModified;
  final ValueChanged<bool> onChanged;

  const _BoolField({
    required this.option,
    required this.value,
    required this.isModified,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return _FieldWrapper(
      option: option,
      isModified: isModified,
      child: SwitchListTile(
        title: Text(
          option.label,
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 14),
        ),
        value: value,
        activeTrackColor: context.appColors.accent,
        contentPadding: EdgeInsets.zero,
        onChanged: onChanged,
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// String list field (add/remove/reorder)
// ---------------------------------------------------------------------------

class _StringListField extends StatefulWidget {
  final ConfigOption option;
  final List<String> currentValue;
  final bool isModified;
  final ValueChanged<List<String>> onChanged;

  const _StringListField({
    required this.option,
    required this.currentValue,
    required this.isModified,
    required this.onChanged,
  });

  @override
  State<_StringListField> createState() => _StringListFieldState();
}

class _StringListFieldState extends State<_StringListField> {
  late List<TextEditingController> _controllers;
  final List<FocusNode> _focusNodes = [];

  @override
  void initState() {
    super.initState();
    _controllers = widget.currentValue
        .map((v) => TextEditingController(text: v))
        .toList();
    for (var i = 0; i < _controllers.length; i++) {
      _focusNodes.add(FocusNode());
    }
  }

  @override
  void didUpdateWidget(covariant _StringListField oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.currentValue.length != _controllers.length) {
      for (final c in _controllers) {
        c.dispose();
      }
      for (final f in _focusNodes) {
        f.dispose();
      }
      _controllers = widget.currentValue
          .map((v) => TextEditingController(text: v))
          .toList();
      _focusNodes.clear();
      for (var i = 0; i < _controllers.length; i++) {
        _focusNodes.add(FocusNode());
      }
    }
  }

  @override
  void dispose() {
    for (final c in _controllers) {
      c.dispose();
    }
    for (final f in _focusNodes) {
      f.dispose();
    }
    super.dispose();
  }

  void _emit() {
    final values = _controllers
        .map((c) => c.text.trim())
        .where((s) => s.isNotEmpty)
        .toList();
    widget.onChanged(values);
  }

  void _addEntry() {
    setState(() {
      _controllers.add(TextEditingController());
      _focusNodes.add(FocusNode());
    });
    _emit();
    // Focus the new field after build.
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_focusNodes.isNotEmpty) {
        _focusNodes.last.requestFocus();
      }
    });
  }

  void _removeEntry(int index) {
    setState(() {
      _controllers[index].dispose();
      _controllers.removeAt(index);
      _focusNodes[index].dispose();
      _focusNodes.removeAt(index);
    });
    _emit();
  }

  @override
  Widget build(BuildContext context) {
    return _FieldWrapper(
      option: widget.option,
      isModified: widget.isModified,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          for (var i = 0; i < _controllers.length; i++)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Row(
                children: [
                  Icon(
                    Icons.folder_outlined,
                    color: context.appColors.textMuted,
                    size: 18,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: TextField(
                      controller: _controllers[i],
                      focusNode: _focusNodes[i],
                      style: TextStyle(
                        color: context.appColors.textPrimary,
                        fontSize: 14,
                      ),
                      onChanged: (_) => _emit(),
                      decoration: InputDecoration(
                        hintText: '~/Projects',
                        hintStyle: TextStyle(
                          color: context.appColors.textMuted,
                          fontSize: 13,
                        ),
                        fillColor: context.appColors.bgElevated,
                        filled: true,
                        border: OutlineInputBorder(
                          borderSide: BorderSide.none,
                          borderRadius: BorderRadius.circular(kRadiusMedium),
                        ),
                        contentPadding: const EdgeInsets.symmetric(
                          horizontal: kSpace3,
                          vertical: 10,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 4),
                  SizedBox(
                    width: 32,
                    height: 32,
                    child: IconButton(
                      padding: EdgeInsets.zero,
                      iconSize: 18,
                      tooltip: 'Remove folder',
                      icon: Icon(
                        Icons.delete_outline,
                        color: context.appColors.textMuted,
                      ),
                      onPressed: () => _removeEntry(i),
                    ),
                  ),
                ],
              ),
            ),
          const SizedBox(height: 2),
          SizedBox(
            height: 32,
            child: TextButton.icon(
              onPressed: _addEntry,
              icon: Icon(
                Icons.add_rounded,
                size: 18,
                color: context.appColors.accentLight,
              ),
              label: Text(
                'Add folder',
                style: TextStyle(
                  color: context.appColors.accentLight,
                  fontSize: 12,
                ),
              ),
              style: TextButton.styleFrom(
                padding: const EdgeInsets.symmetric(horizontal: 10),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(8),
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Tool action button
// ---------------------------------------------------------------------------
