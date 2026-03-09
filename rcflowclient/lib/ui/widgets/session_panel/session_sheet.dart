import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../state/app_state.dart';
import '../../../theme.dart';
import 'session_list_panel.dart';

/// Shows sessions as a modal bottom sheet (mobile).
void showSessionSheet(BuildContext context) {
  context.read<AppState>().refreshSessions();
  showModalBottomSheet(
    context: context,
    isScrollControlled: true,
    builder: (_) => ChangeNotifierProvider.value(
      value: context.read<AppState>(),
      child: const _SessionSheetContent(),
    ),
  );
}

class _SessionSheetContent extends StatelessWidget {
  const _SessionSheetContent();

  @override
  Widget build(BuildContext context) {
    final screenHeight = MediaQuery.of(context).size.height;
    final statusBarHeight = MediaQuery.of(context).padding.top;
    final maxSize = (screenHeight - statusBarHeight) / screenHeight;

    return DraggableScrollableSheet(
      initialChildSize: 0.6,
      minChildSize: 0.3,
      maxChildSize: maxSize,
      expand: false,
      builder: (context, scrollController) {
        return CustomScrollView(
          controller: scrollController,
          slivers: [
            // Drag handle
            SliverToBoxAdapter(
              child: Column(
                children: [
                  SizedBox(height: 12),
                  Center(
                    child: Container(
                      width: 40,
                      height: 4,
                      decoration: BoxDecoration(
                        color: context.appColors.textMuted.withAlpha(100),
                        borderRadius: BorderRadius.circular(2),
                      ),
                    ),
                  ),
                  const SizedBox(height: 8),
                ],
              ),
            ),
            // Session list as a sliver that fills remaining space
            SliverFillRemaining(
              child: SessionListPanel(
                onSessionSelected: () => Navigator.of(context).pop(),
              ),
            ),
          ],
        );
      },
    );
  }
}
