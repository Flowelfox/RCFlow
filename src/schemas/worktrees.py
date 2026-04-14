"""Pydantic schemas for git worktree management endpoints."""

from __future__ import annotations

from pydantic import BaseModel

_DEFAULT_BASE = "main"


class CreateWorktreeRequest(BaseModel):
    """Body for POST /api/worktrees."""

    branch: str
    base: str = _DEFAULT_BASE
    repo_path: str


class MergeWorktreeRequest(BaseModel):
    """Body for POST /api/worktrees/{name}/merge."""

    message: str
    repo_path: str
    into: str | None = None
    no_ff: bool = False
    keep: bool = False
