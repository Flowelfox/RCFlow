part of 'settings_menu.dart';

class _HotkeysSection extends StatefulWidget {
  const _HotkeysSection();

  @override
  State<_HotkeysSection> createState() => _HotkeysSectionState();
}

class _HotkeysSectionState extends State<_HotkeysSection> {
  HotkeyAction? _recordingAction;
  String? _conflictError;

  @override
  Widget build(BuildContext context) {
    final service = context.read<AppState>().hotkeyService;

    return Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        _SectionHeader(title: 'Hotkeys', icon: Icons.keyboard_outlined),
        for (final entry in hotkeyActionGroups.entries) ...[
          Padding(
            padding: const EdgeInsets.only(top: 8, bottom: 6),
            child: Text(
              entry.key,
              style: TextStyle(
                color: context.appColors.textMuted,
                fontSize: 11,
                fontWeight: FontWeight.w600,
                letterSpacing: 0.5,
              ),
            ),
          ),
          for (final action in entry.value)
            _HotkeyRow(
              label: hotkeyActionLabel(action),
              binding: service.bindingFor(action)!,
              isDefault: _isDefault(service, action),
              isRecording: _recordingAction == action,
              error: _recordingAction == action ? _conflictError : null,
              onStartRecording: () {
                setState(() {
                  _recordingAction = action;
                  _conflictError = null;
                });
              },
              onCancelRecording: () {
                setState(() {
                  _recordingAction = null;
                  _conflictError = null;
                });
              },
              onNewBinding: (binding) {
                final conflict = service.updateBinding(binding);
                if (conflict != null) {
                  setState(() {
                    _conflictError =
                        'Conflicts with ${hotkeyActionLabel(conflict.action)}';
                  });
                } else {
                  setState(() {
                    _recordingAction = null;
                    _conflictError = null;
                  });
                }
              },
              onReset: () {
                service.resetBinding(action);
                setState(() {
                  _recordingAction = null;
                  _conflictError = null;
                });
              },
            ),
        ],
        const SizedBox(height: 16),
        SizedBox(
          width: double.infinity,
          height: 34,
          child: OutlinedButton(
            onPressed: () {
              service.resetAllBindings();
              setState(() {
                _recordingAction = null;
                _conflictError = null;
              });
            },
            style: OutlinedButton.styleFrom(
              foregroundColor: context.appColors.textSecondary,
              side: BorderSide(color: context.appColors.divider),
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(kRadiusMedium),
              ),
            ),
            child: Text(
              'Reset All to Defaults',
              style: TextStyle(fontSize: 12),
            ),
          ),
        ),
      ],
    );
  }

  bool _isDefault(dynamic service, HotkeyAction action) {
    final current = service.bindingFor(action) as HotkeyBinding?;
    final def = service.defaultBindingFor(action) as HotkeyBinding;
    if (current == null) return true;
    return current.ctrl == def.ctrl &&
        current.alt == def.alt &&
        current.shift == def.shift &&
        current.meta == def.meta &&
        current.key == def.key;
  }
}

class _HotkeyRow extends StatelessWidget {
  final String label;
  final HotkeyBinding binding;
  final bool isDefault;
  final bool isRecording;
  final String? error;
  final VoidCallback onStartRecording;
  final VoidCallback onCancelRecording;
  final void Function(HotkeyBinding) onNewBinding;
  final VoidCallback onReset;

  const _HotkeyRow({
    required this.label,
    required this.binding,
    required this.isDefault,
    required this.isRecording,
    required this.error,
    required this.onStartRecording,
    required this.onCancelRecording,
    required this.onNewBinding,
    required this.onReset,
  });

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 2),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  label,
                  style: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 13,
                  ),
                ),
              ),
              if (isRecording)
                _HotkeyRecorder(
                  action: binding.action,
                  onNewBinding: onNewBinding,
                  onCancel: onCancelRecording,
                )
              else
                InkWell(
                  borderRadius: BorderRadius.circular(kRadiusSmall),
                  onTap: onStartRecording,
                  child: Container(
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 4,
                    ),
                    decoration: BoxDecoration(
                      color: context.appColors.bgElevated,
                      borderRadius: BorderRadius.circular(kRadiusSmall),
                    ),
                    child: Text(
                      binding.label,
                      style: TextStyle(
                        color: context.appColors.textSecondary,
                        fontSize: 12,
                        fontFamily: 'monospace',
                      ),
                    ),
                  ),
                ),
              const SizedBox(width: 4),
              SizedBox(
                width: 24,
                height: 24,
                child: isDefault
                    ? const SizedBox.shrink()
                    : IconButton(
                        padding: EdgeInsets.zero,
                        iconSize: 14,
                        icon: Icon(
                          Icons.restart_alt_rounded,
                          color: context.appColors.textMuted,
                        ),
                        tooltip: 'Reset to default',
                        onPressed: onReset,
                        constraints: const BoxConstraints(
                          maxWidth: 24,
                          maxHeight: 24,
                        ),
                      ),
              ),
            ],
          ),
          if (error != null)
            Padding(
              padding: const EdgeInsets.only(top: 2, bottom: 4),
              child: Text(
                error!,
                style: TextStyle(
                  color: context.appColors.errorText,
                  fontSize: 11,
                ),
              ),
            ),
        ],
      ),
    );
  }
}

/// Captures a new key combination when in recording mode.
class _HotkeyRecorder extends StatefulWidget {
  final HotkeyAction action;
  final void Function(HotkeyBinding) onNewBinding;
  final VoidCallback onCancel;

  const _HotkeyRecorder({
    required this.action,
    required this.onNewBinding,
    required this.onCancel,
  });

  @override
  State<_HotkeyRecorder> createState() => _HotkeyRecorderState();
}

class _HotkeyRecorderState extends State<_HotkeyRecorder> {
  final FocusNode _focusNode = FocusNode();

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _focusNode.requestFocus();
    });
  }

  @override
  void dispose() {
    _focusNode.dispose();
    super.dispose();
  }

  static final _modifierKeys = {
    LogicalKeyboardKey.controlLeft,
    LogicalKeyboardKey.controlRight,
    LogicalKeyboardKey.altLeft,
    LogicalKeyboardKey.altRight,
    LogicalKeyboardKey.shiftLeft,
    LogicalKeyboardKey.shiftRight,
    LogicalKeyboardKey.metaLeft,
    LogicalKeyboardKey.metaRight,
  };

  KeyEventResult _onKeyEvent(FocusNode node, KeyEvent event) {
    if (event is! KeyDownEvent) return KeyEventResult.handled;

    // Escape cancels recording
    if (event.logicalKey == LogicalKeyboardKey.escape) {
      widget.onCancel();
      return KeyEventResult.handled;
    }

    // Ignore pure modifier presses (wait for the actual key)
    if (_modifierKeys.contains(event.logicalKey)) {
      return KeyEventResult.handled;
    }

    final pressed = HardwareKeyboard.instance.logicalKeysPressed;
    final isCtrl =
        pressed.contains(LogicalKeyboardKey.controlLeft) ||
        pressed.contains(LogicalKeyboardKey.controlRight);
    final isAlt =
        pressed.contains(LogicalKeyboardKey.altLeft) ||
        pressed.contains(LogicalKeyboardKey.altRight);
    final isShift =
        pressed.contains(LogicalKeyboardKey.shiftLeft) ||
        pressed.contains(LogicalKeyboardKey.shiftRight);
    final isMeta =
        pressed.contains(LogicalKeyboardKey.metaLeft) ||
        pressed.contains(LogicalKeyboardKey.metaRight);

    widget.onNewBinding(
      HotkeyBinding(
        action: widget.action,
        ctrl: isCtrl,
        alt: isAlt,
        shift: isShift,
        meta: isMeta,
        key: event.logicalKey,
      ),
    );

    return KeyEventResult.handled;
  }

  @override
  Widget build(BuildContext context) {
    return Focus(
      focusNode: _focusNode,
      onKeyEvent: _onKeyEvent,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: context.appColors.bgElevated,
          borderRadius: BorderRadius.circular(kRadiusSmall),
          border: Border.all(color: context.appColors.accent, width: 1.5),
        ),
        child: Text(
          'Press keys...',
          style: TextStyle(
            color: context.appColors.accentLight,
            fontSize: 12,
            fontStyle: FontStyle.italic,
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// About section
// ---------------------------------------------------------------------------
