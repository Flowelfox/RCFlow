import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/github_pr_info.dart';
import '../../../state/app_state.dart';
import '../../../theme.dart';
import '../../../theme/spacing.dart';
import 'pr_tile.dart';

/// Sidebar panel for the Pull Requests tab — shows cached GitHub PRs split
/// across a "For me" / "Created" tab control.
class PrListPanel extends StatefulWidget {
  final VoidCallback? onPrSelected;

  const PrListPanel({super.key, this.onPrSelected});

  @override
  State<PrListPanel> createState() => _PrListPanelState();
}

class _PrListPanelState extends State<PrListPanel>
    with SingleTickerProviderStateMixin {
  late final TabController _roleTabController;
  final TextEditingController _searchController = TextEditingController();
  String _searchQuery = '';

  @override
  void initState() {
    super.initState();
    _roleTabController = TabController(length: 2, vsync: this);
  }

  @override
  void dispose() {
    _roleTabController.dispose();
    _searchController.dispose();
    super.dispose();
  }

  List<GithubPrInfo> _filterPrs(List<GithubPrInfo> prs) {
    if (_searchQuery.isEmpty) return prs;
    final q = _searchQuery.toLowerCase();
    return prs
        .where(
          (p) =>
              p.title.toLowerCase().contains(q) ||
              p.repoSlug.toLowerCase().contains(q) ||
              p.number.toString().contains(q) ||
              p.author.toLowerCase().contains(q),
        )
        .toList();
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<AppState>(
      builder: (context, state, _) {
        return Column(
          children: [
            _buildSearchBar(context),
            SizedBox(
              height: 32,
              child: TabBar(
                controller: _roleTabController,
                labelColor: context.appColors.textPrimary,
                unselectedLabelColor: context.appColors.textMuted,
                labelStyle: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
                unselectedLabelStyle: const TextStyle(
                  fontSize: 12,
                  fontWeight: FontWeight.w500,
                ),
                indicatorColor: context.appColors.accent,
                indicatorSize: TabBarIndicatorSize.label,
                indicatorWeight: 2,
                dividerHeight: 0,
                tabs: const [
                  Tab(text: 'For me'),
                  Tab(text: 'Created'),
                ],
              ),
            ),
            const Divider(height: 1),
            Expanded(
              child: TabBarView(
                controller: _roleTabController,
                children: [
                  _buildRoleList(context, state, 'for_me'),
                  _buildRoleList(context, state, 'created'),
                ],
              ),
            ),
          ],
        );
      },
    );
  }

  Widget _buildSearchBar(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 8, 10, 4),
      child: SizedBox(
        height: 30,
        child: TextField(
          controller: _searchController,
          onChanged: (v) => setState(() => _searchQuery = v),
          style: TextStyle(color: context.appColors.textPrimary, fontSize: 12),
          decoration: InputDecoration(
            hintText: 'Search pull requests...',
            hintStyle: TextStyle(
              color: context.appColors.textMuted,
              fontSize: 12,
            ),
            prefixIcon: Padding(
              padding: const EdgeInsets.only(left: kSpace2, right: kSpace1),
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
            suffixIcon: _searchQuery.isNotEmpty
                ? GestureDetector(
                    onTap: () {
                      _searchController.clear();
                      setState(() => _searchQuery = '');
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
              horizontal: kSpace2,
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
              borderSide: BorderSide(color: context.appColors.accent, width: 1),
              borderRadius: BorderRadius.circular(8),
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildRoleList(BuildContext context, AppState state, String role) {
    final prs = _filterPrs(
      state.githubPrs.where((p) => p.role == role).toList(),
    );

    if (prs.isEmpty) {
      return _buildEmptyState(context, role);
    }

    return ListView(
      padding: const EdgeInsets.symmetric(vertical: kSpace1),
      children: [
        for (final pr in prs)
          PrTile(pr: pr, state: state, onSelected: widget.onPrSelected),
      ],
    );
  }

  Widget _buildEmptyState(BuildContext context, String role) {
    final hasSearch = _searchQuery.isNotEmpty;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(kSpace5),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(
              hasSearch ? Icons.search_off : Icons.merge_type,
              color: context.appColors.textMuted,
              size: 36,
            ),
            const SizedBox(height: kGapRelaxed),
            Text(
              hasSearch
                  ? 'No pull requests match your search'
                  : role == 'for_me'
                  ? 'No pull requests for you to review'
                  : 'No pull requests you created',
              textAlign: TextAlign.center,
              style: TextStyle(
                color: context.appColors.textSecondary,
                fontSize: 13,
              ),
            ),
          ],
        ),
      ),
    );
  }
}
