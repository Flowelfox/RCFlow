import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../../state/app_state.dart';
import '../../state/pane_state.dart';
import '../../theme.dart';
import '../dialogs/setup_wizard.dart';
import '../widgets/input_area.dart';
import '../widgets/output_display.dart';
import '../widgets/session_identity_bar.dart';
import '../widgets/session_panel.dart';
import '../widgets/settings_menu.dart';

/// Root shell for Android — three-tab bottom navigation:
///   0 · Sessions  — scrollable session list with pull-to-refresh
///   1 · Chat      — active pane (default / centre tab)
///   2 · Settings  — all settings sections
///
/// Uses [IndexedStack] so each tab preserves its scroll/state while hidden.
class AndroidShell extends StatefulWidget {
  const AndroidShell({super.key});

  @override
  State<AndroidShell> createState() => _AndroidShellState();
}

class _AndroidShellState extends State<AndroidShell> {
  int _selectedIndex = 1; // Chat is the default tab

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _checkFirstRun());
  }

  Future<void> _checkFirstRun() async {
    final settings = context.read<AppState>().settings;
    if (!settings.setupComplete) {
      await showSetupWizard(context);
    }
  }

  void _setTab(int index) {
    setState(() => _selectedIndex = index);
  }

  void _onPopInvoked(bool didPop, dynamic result) {
    if (didPop) return;
    // On any non-Chat tab: navigate back to Chat rather than exiting.
    if (_selectedIndex != 1) {
      setState(() => _selectedIndex = 1);
    }
    // On Chat tab with system back: let Android minimize the app (do nothing).
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: false,
      onPopInvokedWithResult: _onPopInvoked,
      child: Scaffold(
        body: IndexedStack(
          index: _selectedIndex,
          children: [
            _SessionsTab(onSessionSelected: () => _setTab(1)),
            _ChatTab(onBrowseSessions: () => _setTab(0)),
            const _SettingsTab(),
          ],
        ),
        bottomNavigationBar: NavigationBar(
          selectedIndex: _selectedIndex,
          onDestinationSelected: _setTab,
          destinations: const [
            NavigationDestination(
              icon: Icon(Icons.history_outlined),
              selectedIcon: Icon(Icons.history_rounded),
              label: 'Sessions',
            ),
            NavigationDestination(
              icon: Icon(Icons.chat_bubble_outline_rounded),
              selectedIcon: Icon(Icons.chat_bubble_rounded),
              label: 'Chat',
            ),
            NavigationDestination(
              icon: Icon(Icons.settings_outlined),
              selectedIcon: Icon(Icons.settings_rounded),
              label: 'Settings',
            ),
          ],
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Sessions tab
// ---------------------------------------------------------------------------

class _SessionsTab extends StatelessWidget {
  final VoidCallback onSessionSelected;

  const _SessionsTab({required this.onSessionSelected});

  @override
  Widget build(BuildContext context) {
    final appColors = context.appColors;
    return Scaffold(
      backgroundColor: appColors.bgBase,
      appBar: AppBar(
        backgroundColor: appColors.bgSurface,
        foregroundColor: appColors.textPrimary,
        elevation: 0,
        title: Text(
          'Sessions',
          style: TextStyle(
            color: appColors.textPrimary,
            fontSize: 20,
            fontWeight: FontWeight.w700,
          ),
        ),
        actions: [
          IconButton(
            icon: Icon(Icons.refresh_rounded, color: appColors.textSecondary),
            tooltip: 'Refresh sessions',
            onPressed: () => context.read<AppState>().refreshSessions(),
          ),
          const SizedBox(width: 4),
        ],
      ),
      body: SessionListPanel(onSessionSelected: onSessionSelected),
    );
  }
}

// ---------------------------------------------------------------------------
// Chat tab
// ---------------------------------------------------------------------------

class _ChatTab extends StatelessWidget {
  final VoidCallback onBrowseSessions;

  const _ChatTab({required this.onBrowseSessions});

  @override
  Widget build(BuildContext context) {
    return Consumer<AppState>(
      builder: (context, appState, _) {
        if (appState.hasNoPanes) {
          return _NoSessionView(onBrowseSessions: onBrowseSessions);
        }

        final appColors = context.appColors;
        return Scaffold(
          backgroundColor: appColors.bgBase,
          appBar: _ChatAppBar(onBrowseSessions: onBrowseSessions),
          body: ChangeNotifierProvider<PaneState>.value(
            value: appState.activePane,
            child: Column(
              children: [
                const SessionIdentityBar(),
                const Expanded(child: OutputDisplay()),
                const InputArea(),
              ],
            ),
          ),
        );
      },
    );
  }
}

// --- Chat AppBar ---

class _ChatAppBar extends StatelessWidget implements PreferredSizeWidget {
  final VoidCallback onBrowseSessions;

  const _ChatAppBar({required this.onBrowseSessions});

  @override
  Size get preferredSize => const Size.fromHeight(kToolbarHeight);

  @override
  Widget build(BuildContext context) {
    final appColors = context.appColors;
    final connected = context.select<AppState, bool>((s) => s.connected);
    final connecting = context.select<AppState, bool>((s) => s.connecting);

    final Color dotColor;
    if (connecting) {
      dotColor = appColors.accentLight;
    } else if (!connected) {
      dotColor = appColors.errorText;
    } else {
      dotColor = appColors.successText;
    }

    return AppBar(
      backgroundColor: appColors.bgSurface,
      foregroundColor: appColors.textPrimary,
      elevation: 0,
      titleSpacing: 16,
      title: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (connecting)
            SizedBox(
              width: 10,
              height: 10,
              child: CircularProgressIndicator(
                strokeWidth: 2,
                color: appColors.accentLight,
              ),
            )
          else
            Container(
              width: 10,
              height: 10,
              decoration: BoxDecoration(
                shape: BoxShape.circle,
                color: dotColor,
                boxShadow: [
                  BoxShadow(
                    color: dotColor.withAlpha(100),
                    blurRadius: 6,
                    spreadRadius: 1,
                  ),
                ],
              ),
            ),
          const SizedBox(width: 10),
          Text(
            'Chat',
            style: TextStyle(
              fontSize: 20,
              fontWeight: FontWeight.w700,
              color: appColors.textPrimary,
            ),
          ),
        ],
      ),
      actions: [
        IconButton(
          icon: Icon(Icons.list_rounded, color: appColors.textSecondary),
          tooltip: 'Browse sessions',
          onPressed: onBrowseSessions,
        ),
        const SizedBox(width: 4),
      ],
    );
  }
}

// --- Empty state when no panes exist ---

class _NoSessionView extends StatelessWidget {
  final VoidCallback onBrowseSessions;

  const _NoSessionView({required this.onBrowseSessions});

  @override
  Widget build(BuildContext context) {
    final appColors = context.appColors;
    return Scaffold(
      backgroundColor: appColors.bgBase,
      appBar: AppBar(
        backgroundColor: appColors.bgSurface,
        foregroundColor: appColors.textPrimary,
        elevation: 0,
        title: Text(
          'Chat',
          style: TextStyle(
            color: appColors.textPrimary,
            fontSize: 20,
            fontWeight: FontWeight.w700,
          ),
        ),
      ),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 32),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(
                Icons.chat_bubble_outline_rounded,
                size: 56,
                color: appColors.textMuted,
              ),
              const SizedBox(height: 20),
              Text(
                'No session open',
                style: TextStyle(
                  color: appColors.textSecondary,
                  fontSize: 18,
                  fontWeight: FontWeight.w600,
                ),
              ),
              const SizedBox(height: 8),
              Text(
                'Select a session from the Sessions tab or start a new chat.',
                textAlign: TextAlign.center,
                style: TextStyle(color: appColors.textMuted, fontSize: 14),
              ),
              const SizedBox(height: 28),
              FilledButton.icon(
                style: FilledButton.styleFrom(
                  backgroundColor: appColors.accent,
                  padding: const EdgeInsets.symmetric(
                    horizontal: 24,
                    vertical: 12,
                  ),
                ),
                icon: const Icon(Icons.history_rounded, color: Colors.white),
                label: const Text(
                  'Browse Sessions',
                  style: TextStyle(color: Colors.white, fontSize: 14),
                ),
                onPressed: onBrowseSessions,
              ),
              const SizedBox(height: 12),
              OutlinedButton.icon(
                icon: Icon(
                  Icons.add_rounded,
                  color: appColors.textSecondary,
                  size: 18,
                ),
                label: Text(
                  'New Chat',
                  style: TextStyle(
                    color: appColors.textSecondary,
                    fontSize: 14,
                  ),
                ),
                onPressed: () => context.read<AppState>().createNewPane(),
                style: OutlinedButton.styleFrom(
                  padding: const EdgeInsets.symmetric(
                    horizontal: 20,
                    vertical: 10,
                  ),
                  side: BorderSide(color: appColors.divider),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(10),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

// ---------------------------------------------------------------------------
// Settings tab
// ---------------------------------------------------------------------------

class _SettingsTab extends StatelessWidget {
  const _SettingsTab();

  @override
  Widget build(BuildContext context) {
    final appColors = context.appColors;
    return Scaffold(
      backgroundColor: appColors.bgBase,
      appBar: AppBar(
        backgroundColor: appColors.bgSurface,
        foregroundColor: appColors.textPrimary,
        elevation: 0,
        title: Text(
          'Settings',
          style: TextStyle(
            color: appColors.textPrimary,
            fontSize: 20,
            fontWeight: FontWeight.w700,
          ),
        ),
      ),
      // Pass null onClose — workers section will open the Workers screen
      // (Navigator.push from within the tab) rather than trying to pop.
      body: const AndroidSettingsBody(),
    );
  }
}
