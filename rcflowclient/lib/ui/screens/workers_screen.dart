import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../models/worker_config.dart';
import '../../services/worker_connection.dart';
import '../../state/app_state.dart';
import '../../theme.dart';
import '../dialogs/worker_edit_dialog.dart';
import '../widgets/custom_title_bar.dart';
import 'server_config_screen.dart';

void showWorkersScreen(BuildContext context) {
  Navigator.of(context).push(
    MaterialPageRoute(builder: (_) => const _WorkersPage()),
  );
}

// ---------------------------------------------------------------------------
// Full-screen page
// ---------------------------------------------------------------------------

class _WorkersPage extends StatelessWidget {
  const _WorkersPage();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: kBgBase,
      body: Column(
        children: [
          const CustomTitleBar(),
          AppBar(
            backgroundColor: kBgBase,
            leading: IconButton(
              icon: const Icon(Icons.arrow_back, color: kTextPrimary),
              onPressed: () => Navigator.of(context).pop(),
            ),
            title: const Text(
              'Manage Workers',
              style: TextStyle(color: kTextPrimary, fontSize: 18),
            ),
            actions: [
              Consumer<AppState>(
                builder: (context, state, _) => Padding(
                  padding: const EdgeInsets.only(right: 12),
                  child: FilledButton.icon(
                    onPressed: () async {
                      final config = await showWorkerEditDialog(
                        context,
                        sortOrder: state.workerConfigs.length,
                      );
                      if (config != null && context.mounted) {
                        state.addWorker(config);
                      }
                    },
                    icon: const Icon(Icons.add_rounded, size: 18),
                    label: const Text('Add'),
                    style: FilledButton.styleFrom(
                      backgroundColor: kAccent,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(
                          horizontal: 16, vertical: 8),
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(10)),
                    ),
                  ),
                ),
              ),
            ],
          ),
          const Expanded(
            child: Padding(
              padding: EdgeInsets.all(24),
              child: _WorkersContent(),
            ),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Shared content
// ---------------------------------------------------------------------------

class _WorkersContent extends StatelessWidget {
  const _WorkersContent();

  @override
  Widget build(BuildContext context) {
    return Consumer<AppState>(
      builder: (context, state, _) {
        final configs = state.workerConfigs;
        return Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (configs.isEmpty)
              Container(
                width: double.infinity,
                padding: const EdgeInsets.all(24),
                decoration: BoxDecoration(
                  color: kBgElevated,
                  borderRadius: BorderRadius.circular(14),
                ),
                child: const Column(
                  children: [
                    Icon(Icons.dns_outlined, color: kTextMuted, size: 40),
                    SizedBox(height: 12),
                    Text('No workers configured',
                        style: TextStyle(color: kTextSecondary, fontSize: 14)),
                    SizedBox(height: 4),
                    Text('Add a worker to connect to an RCFlow server',
                        style: TextStyle(color: kTextMuted, fontSize: 12)),
                  ],
                ),
              )
            else
              Expanded(
                child: ListView.separated(
                  itemCount: configs.length,
                  separatorBuilder: (context, index) => const SizedBox(height: 8),
                  itemBuilder: (context, index) {
                    final config = configs[index];
                    final worker = state.getWorker(config.id);
                    return _WorkerCard(
                      config: config,
                      worker: worker,
                      onEdit: () async {
                        final updated = await showWorkerEditDialog(
                          context,
                          existing: config,
                        );
                        if (updated != null && context.mounted) {
                          state.updateWorker(updated);
                        }
                      },
                      onRemove: () =>
                          _confirmRemove(context, state, config),
                      onSettings: worker?.isConnected == true
                          ? () => showServerConfigScreen(
                                context,
                                ws: worker!.ws,
                                workerName: config.name,
                              )
                          : null,
                      onToggleConnect: () {
                        if (worker?.isConnected == true) {
                          state.disconnectWorker(config.id);
                        } else {
                          state.connectWorker(config.id);
                        }
                      },
                    );
                  },
                ),
              ),
          ],
        );
      },
    );
  }

  void _confirmRemove(
      BuildContext context, AppState state, WorkerConfig config) {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: kBgSurface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
        title: const Text('Remove worker?',
            style: TextStyle(color: kTextPrimary, fontSize: 18)),
        content: Text(
          'This will disconnect and remove "${config.name}".',
          style: const TextStyle(color: kTextSecondary, fontSize: 14),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(),
            child:
                const Text('Cancel', style: TextStyle(color: kTextSecondary)),
          ),
          FilledButton(
            style: FilledButton.styleFrom(backgroundColor: kErrorText),
            onPressed: () {
              Navigator.of(ctx).pop();
              state.removeWorker(config.id);
            },
            child: const Text('Remove',
                style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Worker card
// ---------------------------------------------------------------------------

class _WorkerCard extends StatelessWidget {
  final WorkerConfig config;
  final WorkerConnection? worker;
  final VoidCallback onEdit;
  final VoidCallback onRemove;
  final VoidCallback? onSettings;
  final VoidCallback onToggleConnect;

  const _WorkerCard({
    required this.config,
    required this.worker,
    required this.onEdit,
    required this.onRemove,
    this.onSettings,
    required this.onToggleConnect,
  });

  @override
  Widget build(BuildContext context) {
    final status = worker?.status ?? WorkerConnectionStatus.disconnected;
    final (statusText, statusColor) = switch (status) {
      WorkerConnectionStatus.connected => ('Connected', kSuccessText),
      WorkerConnectionStatus.connecting => ('Connecting...', kToolAccent),
      WorkerConnectionStatus.reconnecting => ('Reconnecting...', kToolAccent),
      WorkerConnectionStatus.disconnected => ('Disconnected', kTextMuted),
    };

    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: kBgElevated,
        borderRadius: BorderRadius.circular(14),
        border: status == WorkerConnectionStatus.connected
            ? Border.all(color: kSuccessText.withAlpha(60), width: 1)
            : null,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 10,
                height: 10,
                decoration: BoxDecoration(
                  shape: BoxShape.circle,
                  color: statusColor,
                  boxShadow: [
                    BoxShadow(
                      color: statusColor.withAlpha(80),
                      blurRadius: 4,
                      spreadRadius: 1,
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  config.name,
                  style: const TextStyle(
                    color: kTextPrimary,
                    fontSize: 15,
                    fontWeight: FontWeight.w600,
                  ),
                  overflow: TextOverflow.ellipsis,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            '${config.host}  \u00B7  $statusText',
            style: TextStyle(color: statusColor, fontSize: 12),
          ),
          if (worker?.serverOs != null)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text('OS: ${worker!.serverOs}',
                  style: const TextStyle(color: kTextMuted, fontSize: 11)),
            ),
          if (config.autoConnect)
            const Padding(
              padding: EdgeInsets.only(top: 4),
              child: Text('Auto-connect: ON',
                  style: TextStyle(color: kTextMuted, fontSize: 11)),
            ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 4,
            children: [
              _SmallButton(
                label: 'Edit',
                icon: Icons.edit_outlined,
                onPressed: onEdit,
              ),
              if (onSettings != null)
                _SmallButton(
                  label: 'Settings',
                  icon: Icons.settings_outlined,
                  onPressed: onSettings,
                  color: kAccentLight,
                ),
              _SmallButton(
                label: 'Remove',
                icon: Icons.delete_outline,
                onPressed: onRemove,
                color: kErrorText,
              ),
              _SmallButton(
                label: status == WorkerConnectionStatus.connected
                    ? 'Disconnect'
                    : 'Connect',
                icon: status == WorkerConnectionStatus.connected
                    ? Icons.link_off_rounded
                    : Icons.link_rounded,
                onPressed: status == WorkerConnectionStatus.connecting ||
                        status == WorkerConnectionStatus.reconnecting
                    ? null
                    : onToggleConnect,
                color: status == WorkerConnectionStatus.connected
                    ? kTextSecondary
                    : kAccentLight,
              ),
            ],
          ),
        ],
      ),
    );
  }
}

class _SmallButton extends StatelessWidget {
  final String label;
  final IconData icon;
  final VoidCallback? onPressed;
  final Color? color;

  const _SmallButton({
    required this.label,
    required this.icon,
    required this.onPressed,
    this.color,
  });

  @override
  Widget build(BuildContext context) {
    final c = color ?? kTextSecondary;
    return TextButton.icon(
      onPressed: onPressed,
      icon: Icon(icon, size: 16, color: onPressed != null ? c : kTextMuted),
      label: Text(
        label,
        style: TextStyle(
          color: onPressed != null ? c : kTextMuted,
          fontSize: 12,
        ),
      ),
      style: TextButton.styleFrom(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        minimumSize: Size.zero,
        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
      ),
    );
  }
}
