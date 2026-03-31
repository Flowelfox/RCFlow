"""Worktree executor — wraps wtpython's WorktreeManager for use in the tool pipeline.

A single ``worktree`` tool definition covers all operations.  Dispatch is keyed
on the ``action`` parameter supplied at call time:

- ``action=new``    → WorktreeManager.new()
- ``action=list``   → WorktreeManager.list()
- ``action=attach`` → select an existing worktree by name or path
- ``action=merge``  → WorktreeManager.merge()
- ``action=rm``     → WorktreeManager.rm()

All WorktreeManager calls use blocking subprocess I/O and are therefore wrapped
with ``asyncio.to_thread`` so they don't block the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wtpython import (
    GitOperationError,
    InvalidBranchType,
    MergeError,
    NotInGitRepository,
    UncommittedChanges,
    WorktreeExists,
    WorktreeManager,
    WorktreeNotFound,
    WtException,
)

from src.executors.base import BaseExecutor, ExecutionChunk, ExecutionResult

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from src.tools.loader import ToolDefinition

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_ACTIONS: frozenset[str] = frozenset({"new", "list", "attach", "merge", "rm"})


def _worktree_to_dict(wt: Any) -> dict[str, Any]:
    """Serialise a Worktree dataclass to a JSON-friendly dict."""
    return {
        "name": wt.name,
        "branch": wt.branch,
        "base": wt.base,
        "path": str(wt.path),
        "created_at": wt.meta.created.isoformat() if wt.meta else None,
    }


def _run_new(manager: WorktreeManager, params: dict[str, Any], default_base: str, validate_type: bool) -> str:
    """Execute worktree_new synchronously (called inside asyncio.to_thread)."""
    branch: str = params["branch"]
    base: str = params.get("base") or default_base
    wt = manager.new(branch=branch, base=base, open_tmux=False, validate_type=validate_type)
    return json.dumps({"created": _worktree_to_dict(wt)}, indent=2)


def _run_list(manager: WorktreeManager) -> str:
    """Execute worktree_list synchronously."""
    worktrees = manager.list()
    return json.dumps({"worktrees": [_worktree_to_dict(w) for w in worktrees]}, indent=2)


def _run_merge(manager: WorktreeManager, params: dict[str, Any]) -> str:
    """Execute worktree_merge synchronously."""
    name: str = params["name"]
    message: str | None = params.get("message")
    into: str | None = params.get("into")
    no_ff: bool = bool(params.get("no_ff", False))
    keep: bool = bool(params.get("keep", False))
    manager.merge(name=name, into=into, message=message, no_ff=no_ff, keep=keep, auto_commit_changes=True)
    return json.dumps({"merged": True, "name": name}, indent=2)


def _run_rm(manager: WorktreeManager, params: dict[str, Any]) -> str:
    """Execute worktree_rm synchronously."""
    name: str = params["name"]
    manager.rm(name=name, force=True)
    return json.dumps({"removed": True, "name": name}, indent=2)


def _run_attach(manager: WorktreeManager, params: dict[str, Any]) -> str:
    """Locate an existing worktree by name or path and return its details.

    Accepts either ``name`` (the branch/worktree short name) or ``path``
    (an absolute or relative filesystem path).  The worktree must already
    exist — this action never creates one.  On success the caller (prompt
    router) will auto-select the returned path as the session's active
    working directory.
    """
    name: str | None = params.get("name")
    path: str | None = params.get("path")
    if not name and not path:
        raise WtException("attach requires either 'name' or 'path' parameter")

    worktrees = manager.list()
    matched = None
    for wt in worktrees:
        if name and wt.name == name:
            matched = wt
            break
        if path and str(wt.path).rstrip("/") == str(Path(path).expanduser().resolve()).rstrip("/"):
            # Normalise both sides before comparing so trailing slashes etc. don't matter
            matched = wt
            break

    if matched is None:
        identifier = name or path
        raise WorktreeNotFound(f"No worktree found matching '{identifier}'")

    return json.dumps({"attached": _worktree_to_dict(matched)}, indent=2)


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class WorktreeExecutor(BaseExecutor):
    """Executor that delegates to wtpython's WorktreeManager.

    One shared instance handles all one-shot worktree tools.  Each call
    creates a fresh ``WorktreeManager`` pointed at the caller-supplied
    ``repo_path`` so multiple repositories are supported simultaneously.
    """

    async def execute(self, tool: ToolDefinition, parameters: dict[str, Any]) -> ExecutionResult:
        """Run a worktree operation and return the result as formatted JSON."""
        config = tool.get_worktree_config()
        repo_path_str: str | None = parameters.get("repo_path")

        if not repo_path_str:
            return ExecutionResult(output="", exit_code=1, error="repo_path parameter is required")

        repo_path = Path(repo_path_str).expanduser().resolve()

        action: str | None = parameters.get("action")
        if not action or action not in _VALID_ACTIONS:
            return ExecutionResult(
                output="",
                exit_code=1,
                error=f"Invalid or missing 'action'. Must be one of: {sorted(_VALID_ACTIONS)}",
            )

        try:
            output = await asyncio.to_thread(
                self._dispatch_sync,
                action,
                repo_path,
                parameters,
                config.default_base_branch,
                config.validate_branch_type,
            )
            return ExecutionResult(output=output, exit_code=0)
        except WorktreeNotFound as e:
            return ExecutionResult(output="", exit_code=1, error=f"Worktree not found: {e}")
        except WorktreeExists as e:
            return ExecutionResult(output="", exit_code=1, error=f"Worktree already exists: {e}")
        except InvalidBranchType as e:
            return ExecutionResult(output="", exit_code=1, error=f"Invalid branch type: {e}")
        except UncommittedChanges as e:
            return ExecutionResult(output="", exit_code=1, error=f"Uncommitted changes: {e}")
        except MergeError as e:
            return ExecutionResult(output="", exit_code=1, error=f"Merge failed: {e}")
        except NotInGitRepository as e:
            return ExecutionResult(output="", exit_code=1, error=f"Not a git repository: {e}")
        except GitOperationError as e:
            return ExecutionResult(output="", exit_code=1, error=f"Git error: {e}")
        except WtException as e:
            return ExecutionResult(output="", exit_code=1, error=str(e))
        except Exception as e:
            logger.exception("Unexpected error in WorktreeExecutor (action=%s)", action)
            return ExecutionResult(output="", exit_code=1, error=str(e))

    def _dispatch_sync(
        self,
        action: str,
        repo_path: Path,
        params: dict[str, Any],
        default_base: str,
        validate_type: bool,
    ) -> str:
        """Blocking dispatch — runs in a thread pool via asyncio.to_thread."""
        manager = WorktreeManager(repo_path=repo_path)

        match action:
            case "new":
                return _run_new(manager, params, default_base, validate_type)
            case "list":
                return _run_list(manager)
            case "attach":
                return _run_attach(manager, params)
            case "merge":
                return _run_merge(manager, params)
            case "rm":
                return _run_rm(manager, params)
            case _:
                raise ValueError(f"Unknown worktree action: {action}")

    async def execute_streaming(
        self, tool: ToolDefinition, parameters: dict[str, Any]
    ) -> AsyncGenerator[ExecutionChunk, None]:
        """Worktree operations are non-interactive; streaming wraps execute()."""
        result = await self.execute(tool, parameters)
        content = result.error or "" if result.exit_code != 0 else result.output
        yield ExecutionChunk(stream="stdout", content=content)

    async def send_input(self, data: str) -> None:
        raise NotImplementedError("Worktree executor does not support interactive input")

    async def cancel(self) -> None:
        # Worktree operations are run in a thread; no subprocess handle to kill
        pass
