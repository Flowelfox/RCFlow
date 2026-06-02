"""Managed CLI tool installation package (Claude Code, Codex, OpenCode).

Public surface mirrors the historical ``src.services.tool_manager`` module:
import :class:`ToolManager` and :class:`ManagedTool` from here (or from the
back-compat shim at ``src.services.tool_manager``).
"""

from __future__ import annotations

from src.services.tools.manager import ToolManager
from src.services.tools.models import ManagedTool

__all__ = ["ManagedTool", "ToolManager"]
