"""Extra WorktreeExecutor coverage: dispatch helpers run for real with a mocked
WorktreeManager so the serialisation/dispatch branches are exercised.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from wtpython import WorktreeNotFound, WtException

from src.executors.worktree import (
    _VALID_ACTIONS,
    WorktreeExecutor,
    _run_attach,
    _run_list,
    _run_merge,
    _run_new,
    _run_rm,
    _worktree_to_dict,
)
from src.tools.loader import ToolDefinition


def _fake_wt(name: str = "feat/foo", path: str = "/tmp/wt/feat-foo", *, with_meta: bool = True) -> SimpleNamespace:
    meta = SimpleNamespace(created=datetime(2026, 1, 2, 3, 4, 5)) if with_meta else None
    return SimpleNamespace(name=name, branch=name, base="main", path=Path(path), meta=meta)


@pytest.fixture
def worktree_tool() -> ToolDefinition:
    return ToolDefinition(
        name="worktree",
        description="Manage git worktrees",
        version="1.0.0",
        session_type="one-shot",
        llm_context="stateless",
        executor="worktree",
        parameters={"type": "object", "properties": {}},
        executor_config={"worktree": {"default_base_branch": "main", "validate_branch_type": True}},
    )


@pytest.fixture
def executor() -> WorktreeExecutor:
    return WorktreeExecutor()


# ---------------------------------------------------------------------------
# _worktree_to_dict
# ---------------------------------------------------------------------------


class TestSerialise:
    def test_with_meta(self) -> None:
        d = _worktree_to_dict(_fake_wt())
        assert d["name"] == "feat/foo"
        assert d["branch"] == "feat/foo"
        assert d["base"] == "main"
        assert d["path"] == "/tmp/wt/feat-foo"
        assert d["created_at"] == "2026-01-02T03:04:05"

    def test_without_meta(self) -> None:
        d = _worktree_to_dict(_fake_wt(with_meta=False))
        assert d["created_at"] is None


# ---------------------------------------------------------------------------
# _run_* helpers
# ---------------------------------------------------------------------------


class TestRunHelpers:
    def test_run_new_uses_default_base(self) -> None:
        manager = MagicMock()
        manager.new.return_value = _fake_wt()
        out = _run_new(manager, {"branch": "feat/foo"}, "develop", True)
        manager.new.assert_called_once_with(branch="feat/foo", base="develop", open_tmux=False, validate_type=True)
        assert json.loads(out)["created"]["branch"] == "feat/foo"

    def test_run_new_explicit_base(self) -> None:
        manager = MagicMock()
        manager.new.return_value = _fake_wt()
        _run_new(manager, {"branch": "feat/foo", "base": "release"}, "main", False)
        _, kwargs = manager.new.call_args
        assert kwargs["base"] == "release"
        assert kwargs["validate_type"] is False

    def test_run_list(self) -> None:
        manager = MagicMock()
        manager.list.return_value = [_fake_wt("a"), _fake_wt("b")]
        out = json.loads(_run_list(manager))
        assert [w["name"] for w in out["worktrees"]] == ["a", "b"]

    def test_run_merge_passes_args(self) -> None:
        manager = MagicMock()
        out = _run_merge(
            manager,
            {"name": "feat/foo", "message": "msg", "into": "main", "no_ff": True, "keep": True},
        )
        manager.merge.assert_called_once_with(
            name="feat/foo", into="main", message="msg", no_ff=True, keep=True, auto_commit_changes=True
        )
        assert json.loads(out) == {"merged": True, "name": "feat/foo"}

    def test_run_merge_defaults(self) -> None:
        manager = MagicMock()
        _run_merge(manager, {"name": "feat/foo"})
        _, kwargs = manager.merge.call_args
        assert kwargs["no_ff"] is False
        assert kwargs["keep"] is False
        assert kwargs["into"] is None

    def test_run_rm(self) -> None:
        manager = MagicMock()
        out = _run_rm(manager, {"name": "feat/foo"})
        manager.rm.assert_called_once_with(name="feat/foo", force=True)
        assert json.loads(out) == {"removed": True, "name": "feat/foo"}


# ---------------------------------------------------------------------------
# _run_attach
# ---------------------------------------------------------------------------


class TestRunAttach:
    def test_requires_name_or_path(self) -> None:
        manager = MagicMock()
        with pytest.raises(WtException, match="requires"):
            _run_attach(manager, {})

    def test_match_by_name(self) -> None:
        manager = MagicMock()
        manager.list.return_value = [_fake_wt("other"), _fake_wt("feat/foo")]
        out = json.loads(_run_attach(manager, {"name": "feat/foo"}))
        assert out["attached"]["name"] == "feat/foo"

    def test_match_by_path(self, tmp_path: Path) -> None:
        target = tmp_path / "wtdir"
        target.mkdir()
        manager = MagicMock()
        manager.list.return_value = [_fake_wt("feat/foo", str(target))]
        out = json.loads(_run_attach(manager, {"path": str(target)}))
        assert out["attached"]["name"] == "feat/foo"

    def test_no_match_raises(self) -> None:
        manager = MagicMock()
        manager.list.return_value = [_fake_wt("other")]
        with pytest.raises(WorktreeNotFound, match="No worktree"):
            _run_attach(manager, {"name": "missing"})


# ---------------------------------------------------------------------------
# _dispatch_sync (drives the helpers through the executor with a mocked manager)
# ---------------------------------------------------------------------------


class TestDispatchSync:
    @pytest.fixture(autouse=True)
    def _patch_manager(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        manager = MagicMock()
        manager.new.return_value = _fake_wt()
        manager.list.return_value = [_fake_wt()]
        monkeypatch.setattr("src.executors.worktree.WorktreeManager", lambda repo_path: manager)
        return manager

    def test_dispatch_new(self, executor: WorktreeExecutor, _patch_manager: MagicMock) -> None:
        out = executor._dispatch_sync("new", Path("/repo"), {"branch": "feat/x"}, "main", True)
        assert json.loads(out)["created"]["branch"] == "feat/foo"

    def test_dispatch_list(self, executor: WorktreeExecutor) -> None:
        out = executor._dispatch_sync("list", Path("/repo"), {}, "main", True)
        assert "worktrees" in json.loads(out)

    def test_dispatch_attach(self, executor: WorktreeExecutor) -> None:
        out = executor._dispatch_sync("attach", Path("/repo"), {"name": "feat/foo"}, "main", True)
        assert json.loads(out)["attached"]["name"] == "feat/foo"

    def test_dispatch_merge(self, executor: WorktreeExecutor) -> None:
        out = executor._dispatch_sync("merge", Path("/repo"), {"name": "feat/foo"}, "main", True)
        assert json.loads(out)["merged"] is True

    def test_dispatch_rm(self, executor: WorktreeExecutor) -> None:
        out = executor._dispatch_sync("rm", Path("/repo"), {"name": "feat/foo"}, "main", True)
        assert json.loads(out)["removed"] is True

    def test_dispatch_unknown_raises(self, executor: WorktreeExecutor) -> None:
        with pytest.raises(ValueError, match="Unknown worktree action"):
            executor._dispatch_sync("bogus", Path("/repo"), {}, "main", True)


# ---------------------------------------------------------------------------
# Full execute() path through the real dispatch (no asyncio.to_thread mock)
# ---------------------------------------------------------------------------


class TestExecuteIntegration:
    async def test_execute_list_end_to_end(
        self, executor: WorktreeExecutor, worktree_tool: ToolDefinition, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager = MagicMock()
        manager.list.return_value = [_fake_wt("a")]
        monkeypatch.setattr("src.executors.worktree.WorktreeManager", lambda repo_path: manager)
        result = await executor.execute(worktree_tool, {"action": "list", "repo_path": "/tmp/repo"})
        assert result.exit_code == 0
        assert json.loads(result.output)["worktrees"][0]["name"] == "a"

    async def test_execute_new_end_to_end(
        self, executor: WorktreeExecutor, worktree_tool: ToolDefinition, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        manager = MagicMock()
        manager.new.return_value = _fake_wt("feat/bar")
        monkeypatch.setattr("src.executors.worktree.WorktreeManager", lambda repo_path: manager)
        result = await executor.execute(
            worktree_tool, {"action": "new", "repo_path": "/tmp/repo", "branch": "feat/bar"}
        )
        assert result.exit_code == 0
        assert json.loads(result.output)["created"]["name"] == "feat/bar"


def test_module_constants() -> None:
    assert frozenset({"new", "list", "attach", "merge", "rm"}) == _VALID_ACTIONS
