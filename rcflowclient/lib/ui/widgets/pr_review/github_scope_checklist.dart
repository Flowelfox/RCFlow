import 'package:flutter/material.dart';

import '../../../services/websocket_service.dart';
import '../../../theme.dart';
import '../../../theme/spacing.dart';

/// A checklist of the OAuth scopes a worker's GitHub token grants, shown
/// beneath the GITHUB_TOKEN secret field in worker settings.
///
/// On build (and via the "Re-check" button) it calls the worker's
/// [WebSocketService.fetchGithubStatus] and renders:
/// - a connection status line,
/// - a row per scope with a satisfied/unsatisfied/unknown indicator,
/// - a note when the token is fine-grained (scopes aren't individually listed).
class GithubScopeChecklist extends StatefulWidget {
  /// The host worker's WebSocket service used to reach the status endpoint.
  final WebSocketService ws;

  const GithubScopeChecklist({super.key, required this.ws});

  @override
  State<GithubScopeChecklist> createState() => _GithubScopeChecklistState();
}

class _GithubScopeChecklistState extends State<GithubScopeChecklist> {
  bool _loading = false;
  String? _error;
  Map<String, dynamic>? _status;

  @override
  void initState() {
    super.initState();
    _check();
  }

  Future<void> _check() async {
    if (_loading) return;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await widget.ws.fetchGithubStatus();
      if (!mounted) return;
      setState(() {
        _status = result;
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.toString();
        _loading = false;
      });
    }
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
              Icon(
                Icons.verified_user_outlined,
                size: 14,
                color: colors.textMuted,
              ),
              const SizedBox(width: 6),
              Text(
                'Token access',
                style: TextStyle(
                  color: colors.textSecondary,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const Spacer(),
              if (_loading)
                SizedBox(
                  width: 14,
                  height: 14,
                  child: CircularProgressIndicator(
                    strokeWidth: 1.5,
                    color: colors.textMuted,
                  ),
                )
              else
                TextButton(
                  onPressed: _check,
                  style: TextButton.styleFrom(
                    padding: const EdgeInsets.symmetric(
                      horizontal: kSpace2,
                      vertical: 0,
                    ),
                    minimumSize: const Size(0, 24),
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  ),
                  child: Text(
                    'Re-check',
                    style: TextStyle(color: colors.accent, fontSize: 11),
                  ),
                ),
            ],
          ),
          const SizedBox(height: kGapTight),
          _buildBody(context),
        ],
      ),
    );
  }

  Widget _buildBody(BuildContext context) {
    final colors = context.appColors;
    if (_loading && _status == null) {
      return Text(
        'Checking token…',
        style: TextStyle(color: colors.textMuted, fontSize: 12),
      );
    }
    if (_error != null) {
      return _statusLine(
        icon: Icons.error_outline,
        color: colors.errorText,
        text: 'Could not check token: $_error',
      );
    }
    final status = _status;
    if (status == null) {
      return const SizedBox.shrink();
    }

    final configured = status['configured'] == true;
    final valid = status['valid'] == true;
    final fineGrained = status['fine_grained'] == true;
    final login = status['login'] as String?;
    final tokenError = status['error'] as String?;
    final scopes =
        (status['scopes'] as List?)?.cast<Map<String, dynamic>>() ?? const [];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (!configured)
          _statusLine(
            icon: Icons.remove_circle_outline,
            color: colors.textMuted,
            text: 'No token set',
          )
        else if (valid)
          _statusLine(
            icon: Icons.check_circle,
            color: colors.successText,
            text: login != null ? 'Connected as @$login' : 'Token valid',
          )
        else
          _statusLine(
            icon: Icons.cancel,
            color: colors.errorText,
            text: 'Token invalid${tokenError != null ? ': $tokenError' : ''}',
          ),
        if (fineGrained) ...[
          const SizedBox(height: kGapTight),
          Container(
            padding: const EdgeInsets.all(kSpace2),
            decoration: BoxDecoration(
              color: colors.accent.withAlpha(16),
              borderRadius: BorderRadius.circular(kRadiusSmall),
            ),
            child: Text(
              'Fine-grained token — access is validated but individual scopes '
              "aren't listed; ensure Pull requests: Read/write, "
              'Contents: Read/write.',
              style: TextStyle(color: colors.textSecondary, fontSize: 11),
            ),
          ),
        ],
        if (!fineGrained && scopes.isNotEmpty) ...[
          const SizedBox(height: kGapInline),
          for (final scope in scopes) _buildScopeRow(context, scope),
        ],
      ],
    );
  }

  Widget _statusLine({
    required IconData icon,
    required Color color,
    required String text,
  }) {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(icon, size: 14, color: color),
        const SizedBox(width: 6),
        Expanded(
          child: Text(text, style: TextStyle(color: color, fontSize: 12)),
        ),
      ],
    );
  }

  Widget _buildScopeRow(BuildContext context, Map<String, dynamic> scope) {
    final colors = context.appColors;
    final name = scope['scope'] as String? ?? '';
    final description = scope['description'] as String? ?? '';
    final required = scope['required'] == true;
    final satisfied = scope['satisfied'] as bool?;

    final IconData icon;
    final Color iconColor;
    if (satisfied == true) {
      icon = Icons.check_circle;
      iconColor = colors.successText;
    } else if (satisfied == false) {
      icon = Icons.cancel;
      iconColor = required ? colors.errorText : colors.textMuted;
    } else {
      icon = Icons.remove;
      iconColor = colors.textMuted;
    }

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.only(top: 1),
            child: Icon(icon, size: 14, color: iconColor),
          ),
          const SizedBox(width: 6),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Flexible(
                      child: Text(
                        name,
                        style: TextStyle(
                          color: colors.textPrimary,
                          fontSize: 11.5,
                          fontFamily: 'monospace',
                          fontFamilyFallback: const ['Courier'],
                        ),
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                    const SizedBox(width: 6),
                    _tag(context, required ? 'required' : 'optional', required),
                  ],
                ),
                if (description.isNotEmpty)
                  Text(
                    description,
                    style: TextStyle(color: colors.textMuted, fontSize: 11),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _tag(BuildContext context, String label, bool required) {
    final colors = context.appColors;
    final color = required ? colors.accent : colors.textMuted;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 1),
      decoration: BoxDecoration(
        color: color.withAlpha(24),
        borderRadius: BorderRadius.circular(kRadiusSmall),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontSize: 9,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
  }
}
