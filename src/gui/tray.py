"""Windows system tray application for RCFlow.

This module now delegates to the GUI module which provides a combined
window + system tray experience. Kept for backwards compatibility with
existing `rcflow tray` commands and installer shortcuts.
"""

from __future__ import annotations


def run_tray() -> None:
    """Start the combined GUI + tray application.

    This is a thin wrapper around ``src.gui.run_gui()`` for backwards
    compatibility. New code should use ``rcflow gui`` directly.
    """
    from src.gui.windows import run_gui  # noqa: PLC0415

    run_gui()
