"""Back-compat shim for the managed-tools package.

The implementation now lives under :mod:`src.services.tools`.  This module
re-exports the historical public surface (``ToolManager``, ``ManagedTool``)
plus the platform-detection / install helpers that tests import by name, so
existing ``from src.services.tool_manager import ...`` call sites keep working.

New code should import from :mod:`src.services.tools` directly.
"""

from __future__ import annotations

from src.services.tools.binary_install import _atomic_install_binary
from src.services.tools.manager import ToolManager
from src.services.tools.models import ManagedTool
from src.services.tools.platform_detect import (
    _detect_claude_platform,
    _detect_codex_target,
    _detect_opencode_asset,
    _parse_version,
)

__all__ = [
    "ManagedTool",
    "ToolManager",
    "_atomic_install_binary",
    "_detect_claude_platform",
    "_detect_codex_target",
    "_detect_opencode_asset",
    "_parse_version",
]
