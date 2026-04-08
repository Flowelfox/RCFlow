"""HTTP API routes for git worktree management.

Exposes ``WorktreeManager`` operations from the ``wtpython`` library over REST
so the Flutter client (and other HTTP consumers) can create, list, merge, and
remove worktrees without going through the LLM/tool pipeline.

All routes require ``X-API-Key`` authentication.  ``repo_path`` must be the
absolute filesystem path to an initialised git repository.  The default base
branch for new worktrees is ``main``.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from wtpython import (
    GitOperationError,
    InvalidBranchType,
    MergeError,
    NotInGitRepository,
    UncommittedChanges,
    WorktreeExists,
    WorktreeManager,
    WorktreeNotFound,
)

from src.api.deps import verify_http_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Worktrees"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DEFAULT_BASE = "main"


def _worktree_to_dict(wt: Any) -> dict[str, Any]:
    return {
        "name": wt.name,
        "branch": wt.branch,
        "base": wt.base,
        "path": str(wt.path),
        "created_at": wt.meta.created.isoformat() if wt.meta else None,
    }


def _manager(repo_path: str) -> WorktreeManager:
    """Return a WorktreeManager for *repo_path*, raising HTTP 400/404 on bad paths."""
    path = Path(repo_path).expanduser().resolve()
    if not path.is_dir():
        raise HTTPException(status_code=404, detail=f"Path not found: {repo_path}")
    try:
        return WorktreeManager(repo_path=path)
    except NotInGitRepository:
        raise HTTPException(status_code=400, detail=f"Not a git repository: {repo_path}") from None


def _map_exception(exc: Exception) -> HTTPException:
    """Map wtpython exceptions to appropriate HTTP status codes."""
    if isinstance(exc, WorktreeNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, WorktreeExists):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, InvalidBranchType):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, UncommittedChanges):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (MergeError, GitOperationError)):
        return HTTPException(status_code=500, detail=str(exc))
    if isinstance(exc, NotInGitRepository):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get(
    "/worktrees",
    summary="List worktrees",
    description=(
        "List all active git worktrees for the given repository. "
        "Returns name, branch, base branch, absolute path, and creation time for each worktree."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def list_worktrees(
    repo_path: str = Query(..., description="Absolute path to the git repository root"),
) -> dict[str, Any]:
    """Return all worktrees for a repository."""
    mgr = _manager(repo_path)
    try:
        worktrees = await asyncio.to_thread(mgr.list)
        return {"worktrees": [_worktree_to_dict(w) for w in worktrees]}
    except Exception as exc:
        raise _map_exception(exc) from exc


@router.post(
    "/worktrees",
    status_code=201,
    summary="Create a worktree",
    description=(
        "Create a new git worktree. Branch name must follow the ``type/ticket/description`` "
        "convention (e.g. ``feature/PROJ-123/add-auth``). The default base branch is ``main``."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def create_worktree(body: CreateWorktreeRequest) -> dict[str, Any]:
    """Create a new worktree and return its metadata."""
    mgr = _manager(body.repo_path)
    try:
        wt = await asyncio.to_thread(
            mgr.new,
            body.branch,
            body.base or _DEFAULT_BASE,
            False,  # open_tmux
            True,  # validate_type
        )
        return {"worktree": _worktree_to_dict(wt)}
    except Exception as exc:
        raise _map_exception(exc) from exc


@router.post(
    "/worktrees/{name}/merge",
    summary="Merge a worktree",
    description=(
        "Squash-merge the named worktree's branch into its base branch (default: ``main``) "
        "and clean up the worktree and branch. Uncommitted changes are committed automatically."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def merge_worktree(name: str, body: MergeWorktreeRequest) -> dict[str, Any]:
    """Merge a worktree and remove it."""
    mgr = _manager(body.repo_path)
    try:
        await asyncio.to_thread(
            mgr.merge,
            name,
            body.into,
            body.message,
            body.no_ff,
            body.keep,
            True,  # auto_commit_changes
        )
        return {"merged": True, "name": name}
    except Exception as exc:
        raise _map_exception(exc) from exc


@router.delete(
    "/worktrees/{name}",
    summary="Remove a worktree",
    description=("Remove a git worktree and delete its branch without merging. Use this to discard abandoned work."),
    dependencies=[Depends(verify_http_api_key)],
)
async def remove_worktree(
    name: str,
    repo_path: str = Query(..., description="Absolute path to the git repository root"),
) -> dict[str, Any]:
    """Remove a worktree and its branch without merging."""
    mgr = _manager(repo_path)
    try:
        await asyncio.to_thread(mgr.rm, name, True)  # force=True
        return {"removed": True, "name": name}
    except Exception as exc:
        raise _map_exception(exc) from exc
