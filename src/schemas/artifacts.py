"""Pydantic schemas for artifact endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class UpdateArtifactSettingsRequest(BaseModel):
    include_pattern: str | None = None
    exclude_pattern: str | None = None
    auto_scan: bool | None = None
    max_file_size: int | None = None
