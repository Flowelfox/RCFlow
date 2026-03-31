import 'package:flutter/widgets.dart';

/// Global keys used by the onboarding tour to locate target widgets.
///
/// Stored in a single file to avoid circular imports between home_screen,
/// session_list_panel, session_pane, etc.
final sidebarKey = GlobalKey(debugLabel: 'onboarding_sidebar');
final sidebarTabBarKey = GlobalKey(debugLabel: 'onboarding_sidebarTabBar');
final mainContentKey = GlobalKey(debugLabel: 'onboarding_mainContent');
final rightBookmarksKey = GlobalKey(debugLabel: 'onboarding_rightBookmarks');
final inputAreaKey = GlobalKey(debugLabel: 'onboarding_inputArea');
final settingsButtonKey = GlobalKey(debugLabel: 'onboarding_settingsButton');
