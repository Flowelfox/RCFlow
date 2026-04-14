"""RCFlow desktop GUI package.

Platform-specific GUI modules:
- windows: CustomTkinter window + system tray (Windows)
- macos:   CustomTkinter settings panel + NSStatusItem menu bar (macOS)
- core:    Shared ServerManager, LogBuffer, poll_server_status
- theme:   Design tokens (colours, fonts, spacing)
- tray:    Legacy wrapper (delegates to windows.run_gui)
"""

from src.gui.windows import RCFlowGUI, run_gui

__all__ = ["RCFlowGUI", "run_gui"]
