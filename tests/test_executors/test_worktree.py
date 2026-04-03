"""Tests for WorktreeExecutor."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from wtpython import (
    GitOperationError,
    InvalidBranchType,
    MergeError,
    NotInGitRepository,
    UncommittedChanges,
    WorktreeExists,
    WorktreeNotFound,
    WtException,
)

from src.executors.worktree import WorktreeExecutor
from src.tools.loader import ToolDefinition

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def worktree_tool() -> ToolDefinition:
    return ToolDefinition(
        name="worktree",
        description="Manage git worktrees",
        version="1.0.0",
        session_type="one-shot",
        llm_context="stateless",
        executor="worktree",
        parameters={
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "repo_path": {"type": "string"},
            },
            "required": ["action", "repo_path"],
        },
        executor_config={
            "worktree": {
                "default_base_branch": "main",
                "validate_branch_type": True,
            }
        },
    )


@pytest.fixture
def executor() -> WorktreeExecutor:
    return WorktreeExecutor()


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_repo_path_returns_error(executor: WorktreeExecutor, worktree_tool: ToolDefinition) -> None:
    result = await executor.execute(worktree_tool, {"action": "list"})
    assert result.exit_code == 1
    assert result.error is not None
    assert "repo_path" in result.error


@pytest.mark.asyncio
async def test_invalid_action_returns_error(executor: WorktreeExecutor, worktree_tool: ToolDefinition) -> None:
    result = await executor.execute(worktree_tool, {"action": "invalid", "repo_path": "/tmp/repo"})
    assert result.exit_code == 1
    assert result.error is not None
    assert "action" in result.error.lower()


@pytest.mark.asyncio
async def test_missing_action_returns_error(executor: WorktreeExecutor, worktree_tool: ToolDefinition) -> None:
    result = await executor.execute(worktree_tool, {"repo_path": "/tmp/repo"})
    assert result.exit_code == 1
    assert result.error is not None
    assert "action" in result.error.lower()


# ---------------------------------------------------------------------------
# Successful dispatch (asyncio.to_thread mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_action_returns_json(executor: WorktreeExecutor, worktree_tool: ToolDefinition) -> None:
    expected_output = json.dumps({"worktrees": []}, indent=2)
    with patch("src.executors.worktree.asyncio.to_thread", new=AsyncMock(return_value=expected_output)):
        result = await executor.execute(worktree_tool, {"action": "list", "repo_path": "/tmp/repo"})
    assert result.exit_code == 0
    assert result.output == expected_output


@pytest.mark.asyncio
async def test_new_action_returns_json(executor: WorktreeExecutor, worktree_tool: ToolDefinition) -> None:
    expected_output = json.dumps({"created": {"name": "feat/foo", "branch": "feat/foo"}}, indent=2)
    with patch("src.executors.worktree.asyncio.to_thread", new=AsyncMock(return_value=expected_output)):
        result = await executor.execute(
            worktree_tool,
            {"action": "new", "repo_path": "/tmp/repo", "branch": "feat/foo"},
        )
    assert result.exit_code == 0
    assert json.loads(result.output)["created"]["branch"] == "feat/foo"


@pytest.mark.asyncio
async def test_merge_action_returns_json(executor: WorktreeExecutor, worktree_tool: ToolDefinition) -> None:
    expected_output = json.dumps({"merged": True, "name": "feat/foo"}, indent=2)
    with patch("src.executors.worktree.asyncio.to_thread", new=AsyncMock(return_value=expected_output)):
        result = await executor.execute(
            worktree_tool,
            {"action": "merge", "repo_path": "/tmp/repo", "name": "feat/foo"},
        )
    assert result.exit_code == 0
    assert json.loads(result.output)["merged"] is True


@pytest.mark.asyncio
async def test_rm_action_returns_json(executor: WorktreeExecutor, worktree_tool: ToolDefinition) -> None:
    expected_output = json.dumps({"removed": True, "name": "feat/foo"}, indent=2)
    with patch("src.executors.worktree.asyncio.to_thread", new=AsyncMock(return_value=expected_output)):
        result = await executor.execute(
            worktree_tool,
            {"action": "rm", "repo_path": "/tmp/repo", "name": "feat/foo"},
        )
    assert result.exit_code == 0
    assert json.loads(result.output)["removed"] is True


# ---------------------------------------------------------------------------
# Exception mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc_class", "expected_fragment"),
    [
        (WorktreeNotFound, "not found"),
        (WorktreeExists, "already exists"),
        (InvalidBranchType, "branch type"),
        (UncommittedChanges, "Uncommitted"),
        (MergeError, "Merge failed"),
        (NotInGitRepository, "git repository"),
        (GitOperationError, "Git error"),
    ],
)
@pytest.mark.asyncio
async def test_known_exception_returns_error(
    executor: WorktreeExecutor,
    worktree_tool: ToolDefinition,
    exc_class: type[Exception],
    expected_fragment: str,
) -> None:
    with patch("src.executors.worktree.asyncio.to_thread", side_effect=exc_class("detail")):
        result = await executor.execute(worktree_tool, {"action": "list", "repo_path": "/tmp/repo"})
    assert result.exit_code == 1
    assert result.error is not None
    assert expected_fragment.lower() in result.error.lower()


@pytest.mark.asyncio
async def test_wt_exception_returns_error(executor: WorktreeExecutor, worktree_tool: ToolDefinition) -> None:
    with patch("src.executors.worktree.asyncio.to_thread", side_effect=WtException("generic wt error")):
        result = await executor.execute(worktree_tool, {"action": "list", "repo_path": "/tmp/repo"})
    assert result.exit_code == 1
    assert result.error is not None
    assert "generic wt error" in result.error


@pytest.mark.asyncio
async def test_unexpected_exception_returns_error(executor: WorktreeExecutor, worktree_tool: ToolDefinition) -> None:
    with patch("src.executors.worktree.asyncio.to_thread", side_effect=RuntimeError("boom")):
        result = await executor.execute(worktree_tool, {"action": "list", "repo_path": "/tmp/repo"})
    assert result.exit_code == 1
    assert result.error is not None
    assert "boom" in result.error


# ---------------------------------------------------------------------------
# execute_streaming
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_streaming_success(executor: WorktreeExecutor, worktree_tool: ToolDefinition) -> None:
    output_json = json.dumps({"worktrees": []})
    with patch("src.executors.worktree.asyncio.to_thread", new=AsyncMock(return_value=output_json)):
        chunks = [
            c async for c in executor.execute_streaming(worktree_tool, {"action": "list", "repo_path": "/tmp/repo"})
        ]
    assert len(chunks) == 1
    assert chunks[0].content == output_json
    assert chunks[0].stream == "stdout"


@pytest.mark.asyncio
async def test_execute_streaming_error_uses_error_field(
    executor: WorktreeExecutor, worktree_tool: ToolDefinition
) -> None:
    with patch("src.executors.worktree.asyncio.to_thread", side_effect=WorktreeNotFound("wt")):
        chunks = [
            c async for c in executor.execute_streaming(worktree_tool, {"action": "list", "repo_path": "/tmp/repo"})
        ]
    assert len(chunks) == 1
    assert "not found" in chunks[0].content.lower()


# ---------------------------------------------------------------------------
# cancel / send_input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_is_noop(executor: WorktreeExecutor) -> None:
    # Should complete without error
    await executor.cancel()


@pytest.mark.asyncio
async def test_send_input_raises(executor: WorktreeExecutor) -> None:
    with pytest.raises(NotImplementedError):
        await executor.send_input("data")
