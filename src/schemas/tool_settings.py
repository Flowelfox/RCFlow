"""Pydantic schemas for tool settings endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class UpdateToolSettingsRequest(BaseModel):
    """Body for the PATCH /api/tools/{tool_name}/settings endpoint."""

    updates: dict[str, Any]
