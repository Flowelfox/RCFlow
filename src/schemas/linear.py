"""Pydantic schemas for Linear integration endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class TestLinearConnectionRequest(BaseModel):
    """Test Linear Connection Request."""

    api_key: str


class CreateIssueRequest(BaseModel):
    """Create Issue Request."""

    title: str
    description: str | None = None
    priority: int = 0
    team_id: str | None = None  # Required when LINEAR_TEAM_ID is not configured


class UpdateIssueRequest(BaseModel):
    """Update Issue Request."""

    title: str | None = None
    description: str | None = None
    state_id: str | None = None
    priority: int | None = None


class LinkTaskRequest(BaseModel):
    """Link Task Request."""

    task_id: str
