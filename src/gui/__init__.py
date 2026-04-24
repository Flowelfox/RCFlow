"""RCFlow desktop GUI package.

Platform-specific GUI modules:
- windows: CustomTkinter window + system tray (Windows)
- macos:   CustomTkinter settings panel + NSStatusItem menu bar (macOS)
- core:    Shared ServerManager, LogBuffer, poll_server_status
- theme:   Design tokens (colours, fonts, spacing)
- tray:    Legacy wrapper (delegates to windows.run_gui)

Platform modules are intentionally NOT imported at package load time.
``src.gui.windows`` requires CustomTkinter (the ``tray`` extra) and would
crash on macOS where the correct module is ``src.gui.macos``; the dispatch
in ``src/__main__.py`` imports the right one lazily.
"""
