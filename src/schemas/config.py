"""Pydantic schemas for server configuration endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class UpdateConfigRequest(BaseModel):
    """Body for the PATCH /api/config endpoint.

    Keys are setting names (e.g. ``LLM_PROVIDER``), values are the new
    string values to apply.
    """

    updates: dict[str, Any]
