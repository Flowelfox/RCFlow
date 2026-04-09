"""Tests for PromptRouter.prepare_plan_session.

Covers:
- ValueError when task does not exist in DB
- RuntimeError when no project is available
- Session metadata set correctly (session_purpose, task_id, plan_output_path)
- PermissionManager pre-seeded with deny rules for Bash/Edit/Agent/Write
- Write-to-plan-dir is allowed (overrides the Write deny)
- Returns (session_id, planning_prompt) tuple
- Applies project_name and selected_worktree_path when provided
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.prompt_router import PromptRouter
from src.core.session import SessionManager, SessionType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db_factory(db: AsyncMock) -> MagicMock:
    """Return a callable that mimics async_sessionmaker, yielding *db*."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


def _make_task(task_id: uuid.UUID, title: str = "Fix the bug") -> MagicMock:
    task = MagicMock()
    task.id = task_id
    task.title = title
    task.description = "A detailed description of what needs fixing."
    return task


def _make_settings(project_dir: str = "/home/user/Projects/myapp") -> MagicMock:
    settings = MagicMock()
    settings.RCFLOW_BACKEND_ID = "test-backend"
    settings.projects_dirs = [Path(project_dir)]
    return settings


def _make_router(
    session_manager: SessionManager,
    db: AsyncMock | None = None,
    settings: MagicMock | None = None,
) -> PromptRouter:
    router = PromptRouter(
        llm_client=MagicMock(),
        session_manager=session_manager,
        tool_registry=MagicMock(),
        db_session_factory=_make_db_factory(db) if db is not None else None,
        settings=settings,
    )
    return router


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPreparePlanSession:
    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_no_db(self) -> None:
        sm = SessionManager("test")
        router = _make_router(sm, db=None)
        with pytest.raises(RuntimeError, match="Database not configured"):
            await router.prepare_plan_session(task_id=str(uuid.uuid4()))

    @pytest.mark.asyncio
    async def test_raises_value_error_when_task_not_found(self) -> None:
        sm = SessionManager("test")
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)  # task not in DB
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        router = _make_router(sm, db=db, settings=_make_settings())
        task_id = str(uuid.uuid4())

        with pytest.raises(ValueError, match=task_id):
            await router.prepare_plan_session(task_id=task_id)

    @pytest.mark.asyncio
    async def test_raises_runtime_error_when_no_project(self) -> None:
        sm = SessionManager("test")
        task_id = uuid.uuid4()

        db = AsyncMock()
        db.get = AsyncMock(return_value=_make_task(task_id))
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        settings = _make_settings()
        settings.projects_dirs = []  # no project configured

        router = _make_router(sm, db=db, settings=settings)
        # Also no project_name provided → no project_root
        router._apply_project_name = MagicMock()

        with pytest.raises(RuntimeError, match="No project configured"):
            await router.prepare_plan_session(task_id=str(task_id))

    @pytest.mark.asyncio
    async def test_returns_session_id_and_prompt(self) -> None:
        sm = SessionManager("test")
        task_id = uuid.uuid4()

        db = AsyncMock()
        db.get = AsyncMock(return_value=_make_task(task_id, "My Task"))
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.add = MagicMock()

        router = _make_router(sm, db=db, settings=_make_settings())
        session_id, prompt = await router.prepare_plan_session(task_id=str(task_id))

        assert session_id  # non-empty UUID string
        assert "My Task" in prompt
        assert ".md" in prompt  # plan path referenced

    @pytest.mark.asyncio
    async def test_session_metadata_set_correctly(self) -> None:
        sm = SessionManager("test")
        task_id = uuid.uuid4()

        db = AsyncMock()
        db.get = AsyncMock(return_value=_make_task(task_id))
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.add = MagicMock()

        router = _make_router(sm, db=db, settings=_make_settings("/repo"))
        session_id, _ = await router.prepare_plan_session(task_id=str(task_id))

        session = sm.get_session(session_id)
        assert session is not None
        assert session.metadata["session_purpose"] == "plan"
        assert session.metadata["task_id"] == str(task_id)
        assert str(task_id) in session.metadata["plan_output_path"]
        assert session.metadata["plan_output_path"].endswith(".md")

    @pytest.mark.asyncio
    async def test_plan_output_path_under_rcflow_plans(self) -> None:
        sm = SessionManager("test")
        task_id = uuid.uuid4()

        db = AsyncMock()
        db.get = AsyncMock(return_value=_make_task(task_id))
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.add = MagicMock()

        router = _make_router(sm, db=db, settings=_make_settings("/repo"))
        session_id, _ = await router.prepare_plan_session(task_id=str(task_id))

        session = sm.get_session(session_id)
        plan_path = session.metadata["plan_output_path"]
        assert "/.rcflow/plans/" in plan_path

    @pytest.mark.asyncio
    async def test_permission_manager_seeded_with_deny_rules(self) -> None:
        sm = SessionManager("test")
        task_id = uuid.uuid4()

        db = AsyncMock()
        db.get = AsyncMock(return_value=_make_task(task_id))
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.add = MagicMock()

        router = _make_router(sm, db=db, settings=_make_settings("/repo"))
        session_id, _ = await router.prepare_plan_session(task_id=str(task_id))

        session = sm.get_session(session_id)
        assert session.permission_manager is not None

        denied_tools = {"Bash", "Edit", "Agent", "Write"}
        rules = session.metadata["permission_rules"]
        deny_rules = {r["tool_name"] for r in rules if r["decision"] == "deny"}
        assert denied_tools == deny_rules
        # All deny rules must use tool_session scope so check_cached() matches them
        for r in rules:
            if r["decision"] == "deny":
                assert r["scope"] == "tool_session", f"{r['tool_name']} deny rule uses wrong scope: {r['scope']}"

    @pytest.mark.asyncio
    async def test_write_to_plan_dir_is_allowed(self) -> None:
        sm = SessionManager("test")
        task_id = uuid.uuid4()

        db = AsyncMock()
        db.get = AsyncMock(return_value=_make_task(task_id))
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.add = MagicMock()

        router = _make_router(sm, db=db, settings=_make_settings("/repo"))
        session_id, _ = await router.prepare_plan_session(task_id=str(task_id))

        session = sm.get_session(session_id)
        rules = session.metadata["permission_rules"]
        allow_rules = [r for r in rules if r["decision"] == "allow"]
        assert len(allow_rules) == 1
        assert allow_rules[0]["tool_name"] == "Write"
        assert allow_rules[0]["path_prefix"] is not None
        assert ".rcflow/plans" in allow_rules[0]["path_prefix"]

    @pytest.mark.asyncio
    async def test_permission_manager_denies_bash(self) -> None:
        """PermissionManager.check_cached rejects Bash for the plan session."""
        sm = SessionManager("test")
        task_id = uuid.uuid4()

        db = AsyncMock()
        db.get = AsyncMock(return_value=_make_task(task_id))
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.add = MagicMock()

        router = _make_router(sm, db=db, settings=_make_settings("/repo"))
        session_id, _ = await router.prepare_plan_session(task_id=str(task_id))

        session = sm.get_session(session_id)
        pm = session.permission_manager
        assert pm is not None
        from src.core.permissions import PermissionDecision  # noqa: PLC0415

        decision = pm.check_cached("Bash", {})
        assert decision == PermissionDecision.DENY

    @pytest.mark.asyncio
    async def test_session_type_is_one_shot(self) -> None:
        sm = SessionManager("test")
        task_id = uuid.uuid4()

        db = AsyncMock()
        db.get = AsyncMock(return_value=_make_task(task_id))
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.add = MagicMock()

        router = _make_router(sm, db=db, settings=_make_settings("/repo"))
        session_id, _ = await router.prepare_plan_session(task_id=str(task_id))

        session = sm.get_session(session_id)
        assert session.session_type == SessionType.ONE_SHOT

    @pytest.mark.asyncio
    async def test_worktree_path_stored_in_metadata(self) -> None:
        sm = SessionManager("test")
        task_id = uuid.uuid4()

        db = AsyncMock()
        db.get = AsyncMock(return_value=_make_task(task_id))
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()
        db.add = MagicMock()

        router = _make_router(sm, db=db, settings=_make_settings("/repo"))
        session_id, _ = await router.prepare_plan_session(
            task_id=str(task_id),
            selected_worktree_path="/repo/.wt/feat/my-feat",
        )

        session = sm.get_session(session_id)
        assert session.metadata.get("selected_worktree_path") == "/repo/.wt/feat/my-feat"
