import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:provider/provider.dart';

import '../../state/app_state.dart';
import '../../theme.dart';

/// Shows a centered popup listing workers.
/// Returns the selected worker ID, or null if dismissed.
Future<String?> showWorkerPickerDialog(BuildContext context) {
  return showDialog<String>(
    context: context,
    barrierColor: Colors.black54,
    builder: (_) => ChangeNotifierProvider<AppState>.value(
      value: context.read<AppState>(),
      child: const _WorkerPickerDialog(),
    ),
  );
}

class _WorkerOption {
  final String id;
  final String name;
  final bool isConnected;

  _WorkerOption({
    required this.id,
    required this.name,
    required this.isConnected,
  });
}

class _WorkerPickerDialog extends StatefulWidget {
  const _WorkerPickerDialog();

  @override
  State<_WorkerPickerDialog> createState() => _WorkerPickerDialogState();
}

class _WorkerPickerDialogState extends State<_WorkerPickerDialog> {
  int _selectedIndex = 0;
  final FocusNode _focusNode = FocusNode();
  late List<_WorkerOption> _options;

  @override
  void initState() {
    super.initState();
    final appState = context.read<AppState>();
    _options = appState.workerConfigs.map((config) {
      final worker = appState.getWorker(config.id);
      final isConnected = worker?.isConnected ?? false;
      return _WorkerOption(
        id: config.id,
        name: config.name,
        isConnected: isConnected,
      );
    }).toList();

    // Pre-select first connected worker
    final firstConnected = _options.indexWhere((o) => o.isConnected);
    if (firstConnected >= 0) _selectedIndex = firstConnected;

    WidgetsBinding.instance.addPostFrameCallback((_) {
      _focusNode.requestFocus();
    });
  }

  @override
  void dispose() {
    _focusNode.dispose();
    super.dispose();
  }

  List<int> get _connectedIndices {
    final indices = <int>[];
    for (var i = 0; i < _options.length; i++) {
      if (_options[i].isConnected) indices.add(i);
    }
    return indices;
  }

  void _moveSelection(int delta) {
    final connected = _connectedIndices;
    if (connected.isEmpty) return;

    final currentPos = connected.indexOf(_selectedIndex);
    final newPos = (currentPos + delta + connected.length) % connected.length;
    setState(() => _selectedIndex = connected[newPos]);
  }

  void _confirm() {
    if (_selectedIndex >= 0 &&
        _selectedIndex < _options.length &&
        _options[_selectedIndex].isConnected) {
      Navigator.of(context).pop(_options[_selectedIndex].id);
    }
  }

  KeyEventResult _handleKeyEvent(FocusNode node, KeyEvent event) {
    if (event is! KeyDownEvent) return KeyEventResult.ignored;

    if (event.logicalKey == LogicalKeyboardKey.arrowDown) {
      _moveSelection(1);
      return KeyEventResult.handled;
    }
    if (event.logicalKey == LogicalKeyboardKey.arrowUp) {
      _moveSelection(-1);
      return KeyEventResult.handled;
    }
    if (event.logicalKey == LogicalKeyboardKey.enter) {
      _confirm();
      return KeyEventResult.handled;
    }
    if (event.logicalKey == LogicalKeyboardKey.escape) {
      Navigator.of(context).pop();
      return KeyEventResult.handled;
    }
    return KeyEventResult.ignored;
  }

  @override
  Widget build(BuildContext context) {
    final connected = _connectedIndices;
    final hasConnected = connected.isNotEmpty;

    return Dialog(
      backgroundColor: context.appColors.bgSurface,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
      child: Focus(
        focusNode: _focusNode,
        onKeyEvent: _handleKeyEvent,
        child: SizedBox(
          width: 340,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              // Title
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 20, 20, 0),
                child: Text(
                  'New Session',
                  style: TextStyle(
                    color: context.appColors.textPrimary,
                    fontSize: 16,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
              const SizedBox(height: 12),
              Divider(color: context.appColors.divider, height: 1),

              // Worker list
              if (!hasConnected)
                Padding(
                  padding: const EdgeInsets.all(24),
                  child: Column(
                    children: [
                      Icon(
                        Icons.cloud_off_outlined,
                        size: 32,
                        color: context.appColors.textMuted,
                      ),
                      const SizedBox(height: 12),
                      Text(
                        'No connected workers',
                        style: TextStyle(
                          color: context.appColors.textMuted,
                          fontSize: 14,
                        ),
                      ),
                      const SizedBox(height: 4),
                      Text(
                        'Connect a worker in Settings',
                        style: TextStyle(
                          color: context.appColors.textMuted,
                          fontSize: 12,
                        ),
                      ),
                    ],
                  ),
                )
              else
                ConstrainedBox(
                  constraints: const BoxConstraints(maxHeight: 300),
                  child: ListView.builder(
                    shrinkWrap: true,
                    padding: const EdgeInsets.symmetric(vertical: 8),
                    itemCount: _options.length,
                    itemBuilder: (ctx, index) {
                      final option = _options[index];
                      final isSelected = index == _selectedIndex;
                      final statusColor = option.isConnected
                          ? context.appColors.successText
                          : context.appColors.textMuted;

                      return InkWell(
                        onTap: option.isConnected
                            ? () {
                                setState(() => _selectedIndex = index);
                                _confirm();
                              }
                            : null,
                        child: Container(
                          color: isSelected && option.isConnected
                              ? context.appColors.accent.withAlpha(30)
                              : Colors.transparent,
                          padding: const EdgeInsets.symmetric(
                            horizontal: 20,
                            vertical: 10,
                          ),
                          child: Row(
                            children: [
                              Container(
                                width: 8,
                                height: 8,
                                decoration: BoxDecoration(
                                  shape: BoxShape.circle,
                                  color: statusColor,
                                ),
                              ),
                              const SizedBox(width: 12),
                              Expanded(
                                child: Text(
                                  option.name,
                                  style: TextStyle(
                                    color: option.isConnected
                                        ? context.appColors.textPrimary
                                        : context.appColors.textMuted,
                                    fontSize: 14,
                                  ),
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                              if (!option.isConnected)
                                Text(
                                  'disconnected',
                                  style: TextStyle(
                                    color: context.appColors.textMuted,
                                    fontSize: 11,
                                  ),
                                ),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
                ),

              // Footer hint
              Divider(color: context.appColors.divider, height: 1),
              Padding(
                padding: const EdgeInsets.symmetric(
                  horizontal: 20,
                  vertical: 12,
                ),
                child: Row(
                  children: [
                    Text(
                      'Enter to confirm',
                      style: TextStyle(
                        color: context.appColors.textMuted,
                        fontSize: 11,
                      ),
                    ),
                    const SizedBox(width: 16),
                    Text(
                      'Esc to cancel',
                      style: TextStyle(
                        color: context.appColors.textMuted,
                        fontSize: 11,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
