import 'dart:async';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../../models/ws_messages.dart';
import '../../../state/pane_state.dart';
import '../../../theme.dart';

/// Card shown when Claude Code or Codex requests permission to use a tool.
///
/// The user can allow or deny the request and optionally set a scope so
/// repeated questions for the same tool/path are suppressed.
class PermissionRequestCard extends StatefulWidget {
  final DisplayMessage message;
  const PermissionRequestCard({super.key, required this.message});

  @override
  State<PermissionRequestCard> createState() => _PermissionRequestCardState();
}

class _PermissionRequestCardState extends State<PermissionRequestCard> {
  String _selectedScope = 'once';
  Timer? _timeoutTimer;
  int _secondsRemaining = 120;

  @override
  void initState() {
    super.initState();
    if (widget.message.accepted == null) {
      _startTimeout();
    }
  }

  void _startTimeout() {
    _timeoutTimer = Timer.periodic(const Duration(seconds: 1), (timer) {
      if (!mounted || widget.message.accepted != null) {
        timer.cancel();
        return;
      }
      setState(() {
        _secondsRemaining--;
        if (_secondsRemaining <= 0) {
          timer.cancel();
          // Auto-deny is handled server-side; just update the UI
          widget.message.accepted = false;
        }
      });
    });
  }

  @override
  void dispose() {
    _timeoutTimer?.cancel();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final metadata = widget.message.toolInput ?? {};
    final riskLevel = metadata['risk_level'] as String? ?? 'medium';

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Container(
        width: double.infinity,
        padding: const EdgeInsets.all(14),
        decoration: BoxDecoration(
          color: _riskBgColor(context, riskLevel).withAlpha(60),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: _riskBorderColor(context, riskLevel).withAlpha(80)),
        ),
        child: widget.message.accepted == null
            ? _buildPending(context, metadata, riskLevel)
            : _buildResolved(metadata),
      ),
    );
  }

  Widget _buildPending(
    BuildContext context,
    Map<String, dynamic> metadata,
    String riskLevel,
  ) {
    final toolName = metadata['tool_name'] as String? ?? 'unknown';
    final description = metadata['description'] as String? ?? '';
    final scopeOptions =
        (metadata['scope_options'] as List<dynamic>?)?.cast<String>() ??
            ['once', 'tool_session', 'all_session'];

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        // Header
        Row(
          children: [
            Icon(Icons.shield_outlined,
                color: _riskIconColor(context, riskLevel), size: 18),
            const SizedBox(width: 8),
            Expanded(
              child: Text(
                'Permission Request',
                style: TextStyle(
                  color: _riskIconColor(context, riskLevel),
                  fontSize: 14,
                  fontWeight: FontWeight.w600,
                ),
              ),
            ),
            // Risk badge
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
              decoration: BoxDecoration(
                color: _riskBgColor(context, riskLevel).withAlpha(120),
                borderRadius: BorderRadius.circular(6),
              ),
              child: Text(
                riskLevel.toUpperCase(),
                style: TextStyle(
                  color: _riskIconColor(context, riskLevel),
                  fontSize: 10,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
          ],
        ),
        SizedBox(height: 10),

        // Description
        Container(
          width: double.infinity,
          padding: EdgeInsets.all(10),
          decoration: BoxDecoration(
            color: context.appColors.bgElevated,
            borderRadius: BorderRadius.circular(8),
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                description,
                style: TextStyle(
                  color: context.appColors.textPrimary,
                  fontSize: 13,
                  fontFamily: 'monospace',
                ),
                maxLines: 4,
                overflow: TextOverflow.ellipsis,
              ),
              SizedBox(height: 6),
              Text(
                'Tool: $toolName',
                style: TextStyle(color: context.appColors.textSecondary, fontSize: 11),
              ),
            ],
          ),
        ),
        SizedBox(height: 10),

        // Scope selector
        Row(
          children: [
            Text('Scope: ',
                style: TextStyle(color: context.appColors.textSecondary, fontSize: 12)),
            SizedBox(width: 4),
            Expanded(
              child: DropdownButton<String>(
                value: _selectedScope,
                isExpanded: true,
                dropdownColor: context.appColors.bgElevated,
                style: TextStyle(color: context.appColors.textPrimary, fontSize: 12),
                underline: Container(height: 1, color: context.appColors.divider),
                items: scopeOptions
                    .map((s) => DropdownMenuItem(
                          value: s,
                          child: Text(_scopeLabel(s)),
                        ))
                    .toList(),
                onChanged: (v) {
                  if (v != null) setState(() => _selectedScope = v);
                },
              ),
            ),
          ],
        ),
        SizedBox(height: 12),

        // Buttons
        Row(
          children: [
            Expanded(
              child: OutlinedButton(
                onPressed: () => _respond(context, false),
                style: OutlinedButton.styleFrom(
                  foregroundColor: context.appColors.errorText,
                  side: BorderSide(color: context.appColors.errorText),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                ),
                child: const Text('Deny',
                    style:
                        TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
              ),
            ),
            SizedBox(width: 10),
            Expanded(
              child: FilledButton(
                onPressed: () => _respond(context, true),
                style: FilledButton.styleFrom(
                  backgroundColor: context.appColors.accent,
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                  padding: const EdgeInsets.symmetric(vertical: 12),
                ),
                child: const Text('Allow',
                    style:
                        TextStyle(fontSize: 14, fontWeight: FontWeight.w600)),
              ),
            ),
          ],
        ),

        // Timeout indicator
        SizedBox(height: 8),
        Text(
          'Auto-deny in ${_secondsRemaining ~/ 60}:${(_secondsRemaining % 60).toString().padLeft(2, '0')}',
          style: TextStyle(color: context.appColors.textMuted, fontSize: 11),
        ),
      ],
    );
  }

  Widget _buildResolved(Map<String, dynamic> metadata) {
    final allowed = widget.message.accepted!;
    final description = metadata['description'] as String? ?? '';

    return Row(
      children: [
        Icon(
          allowed ? Icons.check_circle_outline : Icons.block_rounded,
          color: allowed ? context.appColors.successText : context.appColors.errorText,
          size: 18,
        ),
        SizedBox(width: 8),
        Expanded(
          child: Text(
            '${allowed ? "Allowed" : "Denied"}: $description',
            style: TextStyle(
              color: allowed ? context.appColors.textPrimary : context.appColors.textSecondary,
              fontSize: 13,
            ),
            maxLines: 2,
            overflow: TextOverflow.ellipsis,
          ),
        ),
      ],
    );
  }

  void _respond(BuildContext context, bool allow) {
    final metadata = widget.message.toolInput ?? {};
    final requestId = metadata['request_id'] as String?;
    final sessionId = widget.message.sessionId;

    if (requestId == null || sessionId == null) return;

    setState(() {
      widget.message.accepted = allow;
      _timeoutTimer?.cancel();
    });

    context.read<PaneState>().sendPermissionResponse(
          sessionId: sessionId,
          requestId: requestId,
          decision: allow ? 'allow' : 'deny',
          scope: _selectedScope,
        );
  }

  static String _scopeLabel(String scope) {
    switch (scope) {
      case 'once':
        return 'Just this once';
      case 'tool_session':
        return 'All uses of this tool (session)';
      case 'tool_path':
        return 'This tool for this directory (session)';
      case 'all_session':
        return 'All tools (session)';
      default:
        return scope;
    }
  }

  static Color _riskIconColor(BuildContext context, String riskLevel) {
    switch (riskLevel) {
      case 'low':
        return context.appColors.successText;
      case 'medium':
        return context.appColors.toolAccent;
      case 'high':
        return context.appColors.errorText;
      case 'critical':
        return Color(0xFFDC2626);
      default:
        return context.appColors.textSecondary;
    }
  }

  static Color _riskBgColor(BuildContext context, String riskLevel) {
    switch (riskLevel) {
      case 'low':
        return context.appColors.successBg;
      case 'medium':
        return Color(0xFF2A2000);
      case 'high':
        return context.appColors.errorBg;
      case 'critical':
        return Color(0xFF450A0A);
      default:
        return context.appColors.bgElevated;
    }
  }

  static Color _riskBorderColor(BuildContext context, String riskLevel) {
    switch (riskLevel) {
      case 'low':
        return context.appColors.successText;
      case 'medium':
        return context.appColors.toolAccent;
      case 'high':
        return context.appColors.errorText;
      case 'critical':
        return Color(0xFFDC2626);
      default:
        return context.appColors.divider;
    }
  }
}
