part of 'server_config_screen.dart';

class _ToolSecretField extends StatefulWidget {
  final String label;
  final String description;
  final String maskedValue;
  final TextEditingController? controller;
  final ValueChanged<dynamic> onChanged;

  const _ToolSecretField({
    super.key,
    required this.label,
    required this.description,
    required this.maskedValue,
    required this.controller,
    required this.onChanged,
  });

  @override
  State<_ToolSecretField> createState() => _ToolSecretFieldState();
}

class _ToolSecretFieldState extends State<_ToolSecretField> {
  bool _editing = false;
  bool _obscure = true;

  @override
  Widget build(BuildContext context) {
    final hasValue = widget.maskedValue.isNotEmpty;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          widget.label,
          style: TextStyle(
            color: context.appColors.textSecondary,
            fontSize: 11,
          ),
        ),
        SizedBox(height: 4),
        if (!_editing) ...[
          Row(
            children: [
              Expanded(
                child: Text(
                  hasValue ? widget.maskedValue : 'Not set',
                  style: TextStyle(
                    color: hasValue
                        ? context.appColors.textPrimary
                        : context.appColors.textMuted,
                    fontSize: 13,
                    fontFamily: 'monospace',
                  ),
                ),
              ),
              SizedBox(
                height: 28,
                child: TextButton(
                  onPressed: () => setState(() {
                    _editing = true;
                    _obscure = true;
                    widget.controller?.clear();
                  }),
                  style: TextButton.styleFrom(
                    foregroundColor: context.appColors.accent,
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(kRadiusSmall),
                    ),
                  ),
                  child: Text(
                    hasValue ? 'Change' : 'Set',
                    style: TextStyle(fontSize: 11),
                  ),
                ),
              ),
            ],
          ),
        ] else
          TextField(
            controller: widget.controller,
            obscureText: _obscure,
            style: TextStyle(
              color: context.appColors.textPrimary,
              fontSize: 13,
            ),
            onChanged: (v) => widget.onChanged(v),
            decoration: InputDecoration(
              hintText: 'Enter new value',
              hintStyle: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 12,
              ),
              fillColor: context.appColors.bgSurface,
              filled: true,
              border: OutlineInputBorder(
                borderSide: BorderSide.none,
                borderRadius: BorderRadius.circular(8),
              ),
              contentPadding: EdgeInsets.symmetric(horizontal: 10, vertical: 8),
              suffixIcon: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  IconButton(
                    icon: Icon(
                      _obscure ? Icons.visibility_off : Icons.visibility,
                      size: 18,
                      color: context.appColors.textMuted,
                    ),
                    onPressed: () => setState(() => _obscure = !_obscure),
                    splashRadius: 16,
                  ),
                  IconButton(
                    icon: Icon(
                      Icons.close,
                      size: 18,
                      color: context.appColors.textMuted,
                    ),
                    onPressed: () => setState(() {
                      _editing = false;
                      widget.controller?.clear();
                      // Reset edit — send masked value back so it's treated
                      // as unchanged on the server side.
                      widget.onChanged(widget.maskedValue);
                    }),
                    splashRadius: 16,
                  ),
                ],
              ),
            ),
          ),
        if (widget.description.isNotEmpty)
          Padding(
            padding: EdgeInsets.only(top: 3),
            child: Text(
              widget.description,
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 10,
              ),
            ),
          ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------

class _ToolActionButton extends StatelessWidget {
  final String label;
  final bool loading;
  final bool accent;
  final VoidCallback? onPressed;

  const _ToolActionButton({
    required this.label,
    required this.loading,
    this.accent = false,
    required this.onPressed,
  });

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 32,
      child: TextButton(
        onPressed: onPressed,
        style: TextButton.styleFrom(
          backgroundColor: accent
              ? context.appColors.accent
              : context.appColors.bgElevated,
          foregroundColor: accent
              ? Colors.white
              : context.appColors.textSecondary,
          padding: EdgeInsets.symmetric(horizontal: 12),
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
        ),
        child: loading
            ? SizedBox(
                width: 14,
                height: 14,
                child: CircularProgressIndicator(
                  strokeWidth: 2,
                  color: context.appColors.textSecondary,
                ),
              )
            : Text(label, style: const TextStyle(fontSize: 12)),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Tool progress bar
// ---------------------------------------------------------------------------

class _ToolProgressBar extends StatelessWidget {
  final Map<String, dynamic> progress;

  const _ToolProgressBar({required this.progress});

  @override
  Widget build(BuildContext context) {
    final message = progress['message'] as String? ?? '';
    final progressValue = progress['progress'];
    final double? pct = progressValue is num ? progressValue.toDouble() : null;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(3),
          child: SizedBox(
            height: 4,
            child: pct != null
                ? LinearProgressIndicator(
                    value: pct,
                    backgroundColor: context.appColors.bgOverlay,
                    valueColor: AlwaysStoppedAnimation<Color>(
                      context.appColors.accent,
                    ),
                  )
                : LinearProgressIndicator(
                    backgroundColor: context.appColors.bgOverlay,
                    valueColor: AlwaysStoppedAnimation<Color>(
                      context.appColors.accent,
                    ),
                  ),
          ),
        ),
        if (message.isNotEmpty)
          Padding(
            padding: EdgeInsets.only(top: 3),
            child: Text(
              message,
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 10,
              ),
            ),
          ),
      ],
    );
  }
}

// ---------------------------------------------------------------------------
// Source badge (non-switchable)
// ---------------------------------------------------------------------------
