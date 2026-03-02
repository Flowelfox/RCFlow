import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/ws_messages.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';

/// Interactive question block for AskUserQuestion tool calls.
class QuestionBlock extends StatefulWidget {
  final DisplayMessage message;
  const QuestionBlock({super.key, required this.message});

  @override
  State<QuestionBlock> createState() => _QuestionBlockState();
}

class _QuestionBlockState extends State<QuestionBlock> {
  final Map<String, String> _selections = {};
  final Map<String, Set<String>> _multiSelections = {};
  final Map<String, TextEditingController> _otherControllers = {};
  final Map<String, bool> _otherSelected = {};

  List<dynamic> get _questions {
    final input = widget.message.toolInput;
    if (input == null) return [];

    final q = input['questions'];
    if (q is List) return q;

    // Flat format: top-level "question" string + "options" list.
    final question = input['question'];
    if (question is String) {
      return [
        {
          'question': question,
          'header': input['header'] as String?,
          'options': _normalizeOptions(input['options']),
          'multiSelect': input['multiSelect'] ?? false,
        }
      ];
    }

    return [];
  }

  /// Normalize options from Claude Code's flat format.
  /// Handles `value`/`label` keys and "Label — Description" patterns.
  static List<Map<String, dynamic>> _normalizeOptions(dynamic raw) {
    if (raw is! List) return [];
    return raw.map<Map<String, dynamic>>((opt) {
      if (opt is! Map<String, dynamic>) return <String, dynamic>{};

      var label = opt['label'] as String? ?? opt['value'] as String? ?? '';
      var description = opt['description'] as String?;

      if (description == null || description.isEmpty) {
        // Split "Label — Description" or "Label - Description" patterns.
        final dashIdx = label.indexOf(' — ');
        if (dashIdx > 0) {
          description = label.substring(dashIdx + 3).trim();
          label = label.substring(0, dashIdx).trim();
        }
      }

      return {
        'label': label,
        if (description != null && description.isNotEmpty)
          'description': description,
      };
    }).toList();
  }

  bool get _answered => widget.message.finished;

  @override
  void dispose() {
    for (final c in _otherControllers.values) {
      c.dispose();
    }
    super.dispose();
  }

  void _submit() {
    final answers = <String, String>{};
    for (final q in _questions) {
      if (q is! Map<String, dynamic>) continue;
      final question = q['question'] as String? ?? '';
      final multi = q['multiSelect'] == true;

      if (_otherSelected[question] == true) {
        final text = _otherControllers[question]?.text.trim() ?? '';
        answers[question] = text.isNotEmpty ? text : 'Other';
      } else if (multi) {
        final set = _multiSelections[question];
        if (set != null && set.isNotEmpty) {
          answers[question] = set.join(', ');
        }
      } else {
        final sel = _selections[question];
        if (sel != null) {
          answers[question] = sel;
        }
      }
    }
    context.read<PaneState>().answerQuestion(widget.message, answers);
  }

  bool get _hasSelection {
    for (final q in _questions) {
      if (q is! Map<String, dynamic>) continue;
      final question = q['question'] as String? ?? '';
      final multi = q['multiSelect'] == true;

      if (_otherSelected[question] == true) {
        return (_otherControllers[question]?.text.trim().isNotEmpty) ?? false;
      }
      if (multi) {
        final set = _multiSelections[question];
        if (set != null && set.isNotEmpty) return true;
      } else {
        if (_selections.containsKey(question)) return true;
      }
    }
    return false;
  }

  @override
  Widget build(BuildContext context) {
    if (_answered) {
      return _buildAnswered();
    }
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6),
      child: Container(
        decoration: BoxDecoration(
          color: kAccentDim.withAlpha(60),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: kAccent.withAlpha(80)),
        ),
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (final q in _questions)
              if (q is Map<String, dynamic>) _buildQuestion(q),
            const SizedBox(height: 10),
            SizedBox(
              width: double.infinity,
              child: FilledButton(
                onPressed: _hasSelection ? _submit : null,
                style: FilledButton.styleFrom(
                  backgroundColor: kAccent,
                  disabledBackgroundColor: kBgElevated,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                ),
                child: const Text('Submit',
                    style: TextStyle(
                        fontSize: 14, fontWeight: FontWeight.w600)),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildQuestion(Map<String, dynamic> q) {
    final question = q['question'] as String? ?? '';
    final header = q['header'] as String?;
    final options = q['options'] as List<dynamic>? ?? [];
    final multi = q['multiSelect'] == true;

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (header != null) ...[
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
            decoration: BoxDecoration(
              color: kAccent.withAlpha(40),
              borderRadius: BorderRadius.circular(6),
            ),
            child: Text(header,
                style: const TextStyle(
                    color: kAccentLight,
                    fontSize: 11,
                    fontWeight: FontWeight.w600)),
          ),
          const SizedBox(height: 6),
        ],
        Text(question,
            style: const TextStyle(
                color: kTextPrimary,
                fontSize: 14,
                fontWeight: FontWeight.w500)),
        if (multi)
          const Padding(
            padding: EdgeInsets.only(top: 2),
            child: Text('Select all that apply',
                style: TextStyle(color: kTextMuted, fontSize: 11)),
          ),
        const SizedBox(height: 10),
        for (final opt in options)
          if (opt is Map<String, dynamic>)
            _buildOption(question, opt, multi),
        _buildOtherOption(question),
        const SizedBox(height: 4),
      ],
    );
  }

  Widget _buildOption(
      String question, Map<String, dynamic> opt, bool multi) {
    final label = opt['label'] as String? ?? '';
    final desc = opt['description'] as String?;

    final bool selected;
    if (multi) {
      selected = _multiSelections[question]?.contains(label) ?? false;
    } else {
      selected =
          _selections[question] == label && _otherSelected[question] != true;
    }

    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: GestureDetector(
        onTap: () {
          setState(() {
            _otherSelected[question] = false;
            if (multi) {
              final set = _multiSelections.putIfAbsent(question, () => {});
              if (set.contains(label)) {
                set.remove(label);
              } else {
                set.add(label);
              }
            } else {
              _selections[question] = label;
            }
          });
        },
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          decoration: BoxDecoration(
            color: selected ? kAccent.withAlpha(30) : kBgElevated,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
              color: selected ? kAccent : kDivider,
              width: selected ? 1.5 : 1,
            ),
          ),
          child: Row(
            children: [
              Icon(
                multi
                    ? (selected
                        ? Icons.check_box_rounded
                        : Icons.check_box_outline_blank_rounded)
                    : (selected
                        ? Icons.radio_button_checked_rounded
                        : Icons.radio_button_off_rounded),
                color: selected ? kAccent : kTextMuted,
                size: 18,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(label,
                        style: TextStyle(
                          color: selected ? kTextPrimary : kTextSecondary,
                          fontSize: 13,
                          fontWeight: FontWeight.w500,
                        )),
                    if (desc != null && desc.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.only(top: 2),
                        child: Text(desc,
                            style: const TextStyle(
                                color: kTextMuted, fontSize: 11)),
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

  Widget _buildOtherOption(String question) {
    final isOther = _otherSelected[question] == true;
    _otherControllers.putIfAbsent(question, () => TextEditingController());

    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: GestureDetector(
        onTap: () {
          setState(() {
            _otherSelected[question] = true;
            _selections.remove(question);
            _multiSelections.remove(question);
          });
        },
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 150),
          width: double.infinity,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
          decoration: BoxDecoration(
            color: isOther ? kAccent.withAlpha(30) : kBgElevated,
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
              color: isOther ? kAccent : kDivider,
              width: isOther ? 1.5 : 1,
            ),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(
                    isOther
                        ? Icons.radio_button_checked_rounded
                        : Icons.radio_button_off_rounded,
                    color: isOther ? kAccent : kTextMuted,
                    size: 18,
                  ),
                  const SizedBox(width: 10),
                  Text('Other',
                      style: TextStyle(
                        color: isOther ? kTextPrimary : kTextSecondary,
                        fontSize: 13,
                        fontWeight: FontWeight.w500,
                      )),
                ],
              ),
              if (isOther) ...[
                const SizedBox(height: 8),
                TextField(
                  controller: _otherControllers[question],
                  style:
                      const TextStyle(color: kTextPrimary, fontSize: 13),
                  decoration: InputDecoration(
                    hintText: 'Type your answer...',
                    isDense: true,
                    contentPadding: const EdgeInsets.symmetric(
                        horizontal: 12, vertical: 10),
                    fillColor: kBgOverlay,
                    border: OutlineInputBorder(
                      borderSide: BorderSide.none,
                      borderRadius: BorderRadius.circular(8),
                    ),
                  ),
                  onChanged: (_) => setState(() {}),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildAnswered() {
    final answers = widget.message.selectedAnswers ?? {};

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Container(
        decoration: BoxDecoration(
          color: kToolBg,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(color: kDivider),
        ),
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        child: Row(
          children: [
            const Icon(Icons.check_circle_outline_rounded,
                color: kSuccessText, size: 16),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  for (final entry in answers.entries)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 2),
                      child: Text.rich(
                        TextSpan(children: [
                          TextSpan(
                            text: '${entry.key}: ',
                            style: const TextStyle(
                                color: kTextMuted, fontSize: 12),
                          ),
                          TextSpan(
                            text: entry.value,
                            style: const TextStyle(
                                color: kTextPrimary,
                                fontSize: 12,
                                fontWeight: FontWeight.w500),
                          ),
                        ]),
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
