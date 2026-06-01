"""Design tokens for the RCFlow GUI (Windows + macOS).

Consumed by both gui.py and gui_macos.py.  Every colour, font, and spacing
value used in the GUI should originate here so that visual changes only
require edits to this file.
"""

from __future__ import annotations

import sys

# ── Log viewer ─────────────────────────────────────────────────────────────
# Catppuccin palette: Mocha (dark) / Latte (light)
LOG_DARK_BG = "#1e1e2e"
LOG_DARK_FG = "#cdd6f4"
LOG_DARK_SEL = "#45475a"
LOG_DARK_ERROR = "#f38ba8"
LOG_DARK_WARN = "#fab387"

LOG_LIGHT_BG = "#eff1f5"
LOG_LIGHT_FG = "#4c4f69"
LOG_LIGHT_SEL = "#acb0be"
LOG_LIGHT_ERROR = "#d20f39"
LOG_LIGHT_WARN = "#8c5e15"

# ── Status pill ────────────────────────────────────────────────────────────
# Each value is a (light-mode, dark-mode) tuple passed to ctk fg_color.
STATUS_RUNNING = ("#166534", "#40a02b")
STATUS_STARTING = ("#92400e", "#df8e1d")
STATUS_STOPPING = ("#92400e", "#df8e1d")
STATUS_STOPPED = ("#6b7280", "#6b7280")
STATUS_ERROR = ("#991b1b", "#e64553")

# ── Action buttons ─────────────────────────────────────────────────────────
BTN_START_FG = ("#0369a1", "#38bdf8")
BTN_START_HOVER = ("#075985", "#0ea5e9")
BTN_START_TEXT = ("#ffffff", "#0c1a2b")

BTN_STOP_FG = ("#b91c1c", "#f87171")
BTN_STOP_HOVER = ("#991b1b", "#ef4444")
BTN_STOP_TEXT = ("#ffffff", "#1c0000")

BTN_COPY_FG = ("#374151", "#4b5563")
BTN_COPY_HOVER = ("#1f2937", "#374151")
BTN_COPY_TEXT = ("#f9fafb", "#e5e7eb")

# ── Typography ─────────────────────────────────────────────────────────────


def mono_font() -> str:
    """Monospace font for the log viewer."""
    return "Menlo" if sys.platform == "darwin" else "Consolas"


# Font sizes (points); macOS renders at higher DPI so larger values look right.
FONT_SIZE_LOG = 11 if sys.platform == "darwin" else 9
FONT_SIZE_BODY = 13 if sys.platform == "darwin" else 11
FONT_SIZE_SMALL = 11 if sys.platform == "darwin" else 10

# ── Spacing (pixels) ───────────────────────────────────────────────────────
PAD_OUTER = 14  # window-edge padding
PAD_GROUP = 10  # between widget groups
PAD_INNER = 8  # within a widget group
PAD_SMALL = 4  # tight coupling
