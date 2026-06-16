import 'package:flutter/material.dart';

import '../../../services/websocket_service.dart';
import '../../../theme.dart';
import '../../../theme/spacing.dart';

/// Lists the repositories this worker is the default action target for (shown in
/// Worker Settings → GitHub). Each row can be cleared to reset its default, so
/// the PR action picker asks again next time.
class GithubRepoDefaultsList extends StatefulWidget {
  final WebSocketService ws;

  const GithubRepoDefaultsList({super.key, required this.ws});

  @override
  State<GithubRepoDefaultsList> createState() => _GithubRepoDefaultsListState();
}

class _GithubRepoDefaultsListState extends State<GithubRepoDefaultsList> {
  bool _loading = false;
  List<Map<String, dynamic>> _defaults = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final res = await widget.ws.getGithubRepoDefaults();
      if (!mounted) return;
      setState(() {
        _defaults = (res['defaults'] as List<dynamic>? ?? [])
            .cast<Map<String, dynamic>>();
        _loading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _clear(String owner, String repo) async {
    try {
      await widget.ws.setGithubRepoDefault(owner, repo, false);
    } catch (_) {}
    await _load();
  }

  @override
  Widget build(BuildContext context) {
    final colors = context.appColors;
    return Container(
      margin: const EdgeInsets.only(top: kGapTight),
      padding: const EdgeInsets.all(kSpace3),
      decoration: BoxDecoration(
        color: colors.bgElevated,
        borderRadius: BorderRadius.circular(kRadiusMedium),
        border: Border.all(color: colors.divider),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(Icons.alt_route, size: 14, color: colors.textMuted),
              const SizedBox(width: 6),
              Text(
                'Default for PR actions',
                style: TextStyle(
                  color: colors.textSecondary,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const Spacer(),
              if (_loading)
                SizedBox(
                  width: 13,
                  height: 13,
                  child: CircularProgressIndicator(
                    strokeWidth: 1.5,
                    color: colors.textMuted,
                  ),
                )
              else
                TextButton(
                  onPressed: _load,
                  style: TextButton.styleFrom(
                    padding: const EdgeInsets.symmetric(horizontal: kSpace2),
                    minimumSize: const Size(0, 24),
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  ),
                  child: Text(
                    'Refresh',
                    style: TextStyle(color: colors.accent, fontSize: 11),
                  ),
                ),
            ],
          ),
          const SizedBox(height: kGapTight),
          if (_defaults.isEmpty)
            Text(
              'This worker is not the default for any repository. When several '
              'workers back the same PR, you can mark one as default from the '
              'action picker.',
              style: TextStyle(color: colors.textMuted, fontSize: 11),
            )
          else
            for (final d in _defaults)
              Padding(
                padding: const EdgeInsets.symmetric(vertical: 1),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        '${d['owner']}/${d['repo']}',
                        style: TextStyle(
                          color: colors.textPrimary,
                          fontSize: 11.5,
                          fontFamily: 'monospace',
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    IconButton(
                      padding: EdgeInsets.zero,
                      constraints: const BoxConstraints(),
                      iconSize: 15,
                      splashRadius: 14,
                      tooltip: 'Clear default',
                      icon: Icon(Icons.close, color: colors.textMuted),
                      onPressed: () =>
                          _clear(d['owner'] as String, d['repo'] as String),
                    ),
                  ],
                ),
              ),
        ],
      ),
    );
  }
}
