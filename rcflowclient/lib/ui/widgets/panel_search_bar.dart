/// Shared search bar widget used across the session-panel list panels
/// (tasks, artifacts, sessions, worktrees).
///
/// Renders a 30-px-tall row with an inline search [TextField] and a slot
/// for [actions] — any number of 30×30 icon-button slots that appear to the
/// right of the search field.
///
/// The caller owns the [TextEditingController] and search-query state so that
/// clearing the field (via the × suffix icon) can also reset the caller's
/// filter state through [onChanged].
library;

import 'package:flutter/material.dart';

import '../../theme.dart';

class PanelSearchBar extends StatelessWidget {
  const PanelSearchBar({
    super.key,
    required this.controller,
    required this.query,
    required this.onChanged,
    required this.hint,
    this.actions = const [],
  });

  /// Text controller owned by the caller.
  final TextEditingController controller;

  /// Current search query string (used to show/hide the clear × button).
  final String query;

  /// Called whenever the search text changes (including when cleared via ×).
  final ValueChanged<String> onChanged;

  /// Placeholder text in the search field (e.g. `'Search tasks...'`).
  final String hint;

  /// Additional action widgets appended after the search field.
  /// Each is typically a 30×30 [SizedBox] wrapping an [IconButton].
  final List<Widget> actions;

  @override
  Widget build(BuildContext context) {
    return SizedBox(
      height: 30,
      child: Row(
        children: [
          Expanded(
            child: TextField(
              controller: controller,
              onChanged: onChanged,
              style: TextStyle(
                color: context.appColors.textPrimary,
                fontSize: 12,
              ),
              decoration: InputDecoration(
                hintText: hint,
                hintStyle: TextStyle(
                  color: context.appColors.textMuted,
                  fontSize: 12,
                ),
                prefixIcon: Padding(
                  padding: const EdgeInsets.only(left: 8, right: 4),
                  child: Icon(
                    Icons.search_rounded,
                    color: context.appColors.textMuted,
                    size: 16,
                  ),
                ),
                prefixIconConstraints: const BoxConstraints(
                  maxWidth: 28,
                  maxHeight: 30,
                ),
                suffixIcon: query.isNotEmpty
                    ? GestureDetector(
                        onTap: () {
                          controller.clear();
                          onChanged('');
                        },
                        child: Padding(
                          padding: const EdgeInsets.only(right: 6),
                          child: Icon(
                            Icons.close_rounded,
                            color: context.appColors.textMuted,
                            size: 14,
                          ),
                        ),
                      )
                    : null,
                suffixIconConstraints: const BoxConstraints(
                  maxWidth: 24,
                  maxHeight: 30,
                ),
                filled: true,
                fillColor: context.appColors.bgElevated,
                contentPadding: const EdgeInsets.symmetric(
                  horizontal: 8,
                  vertical: 0,
                ),
                border: OutlineInputBorder(
                  borderSide: BorderSide.none,
                  borderRadius: BorderRadius.circular(8),
                ),
                enabledBorder: OutlineInputBorder(
                  borderSide: BorderSide.none,
                  borderRadius: BorderRadius.circular(8),
                ),
                focusedBorder: OutlineInputBorder(
                  borderSide: BorderSide(
                    color: context.appColors.accent,
                    width: 1,
                  ),
                  borderRadius: BorderRadius.circular(8),
                ),
              ),
            ),
          ),
          if (actions.isNotEmpty) ...[
            const SizedBox(width: 6),
            ...actions,
          ],
        ],
      ),
    );
  }
}
