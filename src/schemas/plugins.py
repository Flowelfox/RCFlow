"""Pydantic schemas for plugin management endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class InstallPluginRequest(BaseModel):
    source: str
    """Git URL or local filesystem path to clone/copy from."""
    name: str | None = None
    """Override the plugin directory name (defaults to the last URL/path segment)."""


class SetPluginEnabledRequest(BaseModel):
    enabled: bool
    """Whether the plugin should be enabled (True) or disabled (False)."""


class _LegacyInstallRequest(BaseModel):
    source: str
    name: str | None = None
