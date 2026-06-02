"""Data model for a single managed CLI tool."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ManagedTool:
    """Represents a single managed CLI tool."""

    name: str  # "claude_code" or "codex"
    binary_name: str  # "claude" or "codex"
    current_version: str | None = None
    latest_version: str | None = None
    binary_path: str | None = None  # Resolved absolute path to binary
    managed: bool = False  # Whether RCFlow manages this install
    error: str | None = None  # Last error message, if any
    managed_path: str | None = None  # Path to managed install (if exists)
    external_path: str | None = None  # Path found on PATH (if exists)
