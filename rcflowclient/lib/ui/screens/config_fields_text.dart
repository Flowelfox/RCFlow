part of 'server_config_screen.dart';

class _TextField extends StatelessWidget {
  final ConfigOption option;
  final TextEditingController controller;
  final bool isModified;
  final ValueChanged<String> onChanged;

  const _TextField({
    required this.option,
    required this.controller,
    required this.isModified,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return _FieldWrapper(
      option: option,
      isModified: isModified,
      child: TextField(
        controller: controller,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 14),
        onChanged: onChanged,
        decoration: InputDecoration(
          hintText: option.label,
          fillColor: context.appColors.bgElevated,
          border: OutlineInputBorder(
            borderSide: BorderSide.none,
            borderRadius: BorderRadius.circular(10),
          ),
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 12,
            vertical: 10,
          ),
        ),
      ),
    );
  }
}

class _TextAreaField extends StatelessWidget {
  final ConfigOption option;
  final TextEditingController controller;
  final bool isModified;
  final ValueChanged<String> onChanged;

  const _TextAreaField({
    required this.option,
    required this.controller,
    required this.isModified,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    return _FieldWrapper(
      option: option,
      isModified: isModified,
      child: TextField(
        controller: controller,
        style: TextStyle(color: context.appColors.textPrimary, fontSize: 14),
        onChanged: onChanged,
        maxLines: null,
        minLines: 3,
        keyboardType: TextInputType.multiline,
        decoration: InputDecoration(
          hintText: option.label,
          fillColor: context.appColors.bgElevated,
          border: OutlineInputBorder(
            borderSide: BorderSide.none,
            borderRadius: BorderRadius.circular(10),
          ),
          contentPadding: const EdgeInsets.symmetric(
            horizontal: 12,
            vertical: 10,
          ),
        ),
      ),
    );
  }
}

class _SecretField extends StatefulWidget {
  final ConfigOption option;
  final TextEditingController controller;
  final bool isModified;
  final ValueChanged<String> onChanged;

  const _SecretField({
    required this.option,
    required this.controller,
    required this.isModified,
    required this.onChanged,
  });

  @override
  State<_SecretField> createState() => _SecretFieldState();
}

class _SecretFieldState extends State<_SecretField> {
  bool _obscure = true;
  bool _editing = false;

  @override
  Widget build(BuildContext context) {
    return _FieldWrapper(
      option: widget.option,
      isModified: widget.isModified,
      child: _editing
          ? TextField(
              controller: widget.controller,
              obscureText: _obscure,
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 14,
              ),
              onChanged: widget.onChanged,
              decoration: InputDecoration(
                hintText: 'Enter new value',
                fillColor: context.appColors.bgElevated,
                border: OutlineInputBorder(
                  borderSide: BorderSide.none,
                  borderRadius: BorderRadius.circular(10),
                ),
                contentPadding: EdgeInsets.symmetric(
                  horizontal: 12,
                  vertical: 10,
                ),
                suffixIcon: IconButton(
                  icon: Icon(
                    _obscure
                        ? Icons.visibility_off_outlined
                        : Icons.visibility_outlined,
                    color: context.appColors.textMuted,
                    size: 18,
                  ),
                  onPressed: () => setState(() => _obscure = !_obscure),
                ),
              ),
            )
          : Row(
              children: [
                Expanded(
                  child: Container(
                    padding: EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                    decoration: BoxDecoration(
                      color: context.appColors.bgElevated,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      widget.option.value?.toString() ?? '',
                      style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 14,
                      ),
                    ),
                  ),
                ),
                SizedBox(width: 8),
                TextButton(
                  onPressed: () {
                    widget.controller.clear();
                    setState(() => _editing = true);
                  },
                  child: Text(
                    'Change',
                    style: TextStyle(
                      color: context.appColors.accentLight,
                      fontSize: 12,
                    ),
                  ),
                ),
              ],
            ),
    );
  }
}

class _SelectField extends StatelessWidget {
  final ConfigOption option;
  final String value;
  final bool isModified;
  final ValueChanged<String> onChanged;

  const _SelectField({
    required this.option,
    required this.value,
    required this.isModified,
    required this.onChanged,
  });

  @override
  Widget build(BuildContext context) {
    final items = option.options ?? [];
    final values = items.map((o) => o.value).toList();
    return _FieldWrapper(
      option: option,
      isModified: isModified,
      child: Container(
        padding: EdgeInsets.symmetric(horizontal: 12),
        decoration: BoxDecoration(
          color: context.appColors.bgElevated,
          borderRadius: BorderRadius.circular(10),
        ),
        child: DropdownButtonHideUnderline(
          child: DropdownButton<String>(
            value: values.contains(value) ? value : null,
            isExpanded: true,
            dropdownColor: context.appColors.bgSurface,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 14,
            ),
            hint: Text(
              option.label,
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 14,
              ),
            ),
            items: items
                .map(
                  (o) => DropdownMenuItem(value: o.value, child: Text(o.label)),
                )
                .toList(),
            onChanged: (v) {
              if (v != null) onChanged(v);
            },
          ),
        ),
      ),
    );
  }
}
