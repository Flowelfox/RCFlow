"""Pydantic schemas for session management endpoints."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003

from pydantic import BaseModel


class ReorderSessionRequest(BaseModel):
    """Body for the reorder-session endpoint."""

    after_session_id: str | None = None


class RenameSessionRequest(BaseModel):
    """Body for the rename-session endpoint."""

    title: str | None = None


class SetSessionWorktreeRequest(BaseModel):
    """Body for the set-session-worktree endpoint."""

    path: str | None = None


class DraftUpsertRequest(BaseModel):
    """Body for the save-draft endpoint."""

    content: str


class DraftResponse(BaseModel):
    """Response body for the get-draft endpoint."""

    content: str
    updated_at: datetime
