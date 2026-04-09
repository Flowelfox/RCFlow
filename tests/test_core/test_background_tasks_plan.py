"""Tests for BackgroundTasksMixin._finalize_plan_session.

Covers:
- Returns early when plan_output_path metadata is missing
- Returns early when task_id metadata is missing
- Returns early when plan file does not exist on disk
- Happy path: upserts artifact, links task, broadcasts task_update
- Race condition: IntegrityError on artifact insert → re-fetches existing artifact
- Task not in DB: artifact upserted but no broadcast
- DB factory not configured: returns early
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import IntegrityError

from src.core.background_tasks import BackgroundTasksMixin
from src.core.session import ActiveSession, SessionManager, SessionType

if TYPE_CHECKING:
    import asyncio
    from pathlib import Path

# ---------------------------------------------------------------------------
# Minimal concrete host
# ---------------------------------------------------------------------------


class _Host(BackgroundTasksMixin):
    """Minimal subclass for testing BackgroundTasksMixin in isolation."""

    def __init__(
        self,
        db_session_factory=None,
        settings: MagicMock | None = None,
        session_manager: MagicMock | None = None,
    ) -> None:
        self._db_session_factory = db_session_factory
        self._settings = settings or _make_settings()
        self._session_manager = session_manager or MagicMock()
        self._pending_plan_finalization_tasks: set[asyncio.Task] = set()
        # Other required attributes used by other methods (not under test here)
        self._llm = None
        self._pending_log_tasks: set = set()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(backend_id: str = "test-backend") -> MagicMock:
    s = MagicMock()
    s.RCFLOW_BACKEND_ID = backend_id
    return s


def _make_db_factory(db: AsyncMock) -> MagicMock:
    """Return a callable that mimics async_sessionmaker, yielding *db*."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


def _make_session(
    plan_output_path: str | None = None,
    task_id: str | None = None,
    session_purpose: str = "plan",
) -> ActiveSession:
    sm = SessionManager("test")
    session = sm.create_session(SessionType.ONE_SHOT)
    if session_purpose:
        session.metadata["session_purpose"] = session_purpose
    if task_id:
        session.metadata["task_id"] = task_id
    if plan_output_path:
        session.metadata["plan_output_path"] = plan_output_path
    return session


def _make_task_obj(task_id: uuid.UUID) -> MagicMock:
    task = MagicMock()
    task.id = task_id
    task.title = "Fix the login bug"
    task.description = "Users can't log in on Firefox"
    task.status = "todo"
    task.source = "user"
    task.plan_artifact_id = None
    task.updated_at = None
    task.created_at = MagicMock()
    task.created_at.isoformat.return_value = "2026-04-09T10:00:00+00:00"
    task.sessions = []
    return task


def _make_artifact_obj(artifact_id: uuid.UUID | None = None) -> MagicMock:
    art = MagicMock()
    art.id = artifact_id or uuid.uuid4()
    return art


# ---------------------------------------------------------------------------
# Tests: early-return guards
# ---------------------------------------------------------------------------


class TestFinalizePlanSessionGuards:
    @pytest.mark.asyncio
    async def test_no_plan_path_returns_early(self) -> None:
        host = _Host()
        session = _make_session(task_id=str(uuid.uuid4()))  # no plan_output_path
        # Should not raise
        await host._finalize_plan_session(session)

    @pytest.mark.asyncio
    async def test_no_task_id_returns_early(self) -> None:
        host = _Host()
        session = _make_session(plan_output_path="/tmp/plan.md")  # no task_id
        await host._finalize_plan_session(session)

    @pytest.mark.asyncio
    async def test_plan_file_missing_returns_early(self, tmp_path: Path) -> None:
        missing = str(tmp_path / "nonexistent.md")
        host = _Host()
        session = _make_session(plan_output_path=missing, task_id=str(uuid.uuid4()))
        # Should log a warning and return — no exception
        await host._finalize_plan_session(session)

    @pytest.mark.asyncio
    async def test_no_db_factory_returns_early(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.md"
        plan_path.write_text("# Plan\n- Step 1")
        host = _Host(db_session_factory=None)
        session = _make_session(plan_output_path=str(plan_path), task_id=str(uuid.uuid4()))
        await host._finalize_plan_session(session)
        # No broadcast expected
        host._session_manager.broadcast_task_update.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: happy path
# ---------------------------------------------------------------------------


class TestFinalizePlanSessionHappyPath:
    @pytest.mark.asyncio
    async def test_links_artifact_to_task(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.md"
        plan_path.write_text("# Plan\n- Fix the bug\n- Deploy")

        task_id = uuid.uuid4()
        artifact_id = uuid.uuid4()
        task_obj = _make_task_obj(task_id)
        artifact_obj = _make_artifact_obj(artifact_id)

        db = AsyncMock()
        # First call to db.get is for the artifact select (returns None → insert path)
        # but we use db.execute for the artifact select
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None  # artifact not yet in DB
        db.execute = AsyncMock(return_value=result_mock)
        db.get = AsyncMock(return_value=task_obj)  # task found
        db.add = MagicMock()
        db.flush = AsyncMock(side_effect=lambda: setattr(artifact_obj, "id", artifact_id))
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        # After flush, artifact.id is set
        def _add_side_effect(obj):
            if hasattr(obj, "file_path"):
                obj.id = artifact_id

        db.add.side_effect = _add_side_effect

        sm_mock = MagicMock()
        host = _Host(db_session_factory=_make_db_factory(db), session_manager=sm_mock)
        session = _make_session(plan_output_path=str(plan_path), task_id=str(task_id))
        await host._finalize_plan_session(session)

        # Task should have plan_artifact_id set
        assert task_obj.plan_artifact_id is not None
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_broadcasts_task_update(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.md"
        plan_path.write_text("# Plan content")

        task_id = uuid.uuid4()
        task_obj = _make_task_obj(task_id)
        task_obj.updated_at = MagicMock()
        task_obj.updated_at.isoformat.return_value = "2026-04-09T11:00:00+00:00"

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)
        db.get = AsyncMock(return_value=task_obj)
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        sm_mock = MagicMock()
        host = _Host(db_session_factory=_make_db_factory(db), session_manager=sm_mock)
        session = _make_session(plan_output_path=str(plan_path), task_id=str(task_id))
        await host._finalize_plan_session(session)

        sm_mock.broadcast_task_update.assert_called_once()
        broadcast_dict = sm_mock.broadcast_task_update.call_args[0][0]
        assert broadcast_dict["task_id"] == str(task_id)

    @pytest.mark.asyncio
    async def test_no_broadcast_when_task_not_found(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.md"
        plan_path.write_text("# Plan")

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)
        db.get = AsyncMock(return_value=None)  # task not in DB
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        sm_mock = MagicMock()
        host = _Host(db_session_factory=_make_db_factory(db), session_manager=sm_mock)
        session = _make_session(plan_output_path=str(plan_path), task_id=str(uuid.uuid4()))
        await host._finalize_plan_session(session)

        sm_mock.broadcast_task_update.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: race condition handling
# ---------------------------------------------------------------------------


class TestFinalizePlanSessionRaceCondition:
    @pytest.mark.asyncio
    async def test_integrity_error_on_insert_refetches_artifact(self, tmp_path: Path) -> None:
        """If flush raises IntegrityError (ArtifactScanner race), fall back to
        a second SELECT to find the existing artifact."""
        plan_path = tmp_path / "plan.md"
        plan_path.write_text("# Plan")

        task_id = uuid.uuid4()
        existing_artifact_id = uuid.uuid4()
        task_obj = _make_task_obj(task_id)

        existing_artifact = MagicMock()
        existing_artifact.id = existing_artifact_id

        db = AsyncMock()
        first_result = MagicMock()
        first_result.scalar_one_or_none.return_value = None  # first select: not found
        second_result = MagicMock()
        second_result.scalar_one.return_value = existing_artifact  # retry: found

        db.execute = AsyncMock(side_effect=[first_result, second_result])
        db.get = AsyncMock(return_value=task_obj)
        db.add = MagicMock()
        db.flush = AsyncMock(side_effect=IntegrityError("unique", {}, Exception("dup")))
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        sm_mock = MagicMock()
        host = _Host(db_session_factory=_make_db_factory(db), session_manager=sm_mock)
        session = _make_session(plan_output_path=str(plan_path), task_id=str(task_id))
        await host._finalize_plan_session(session)

        # Task linked to the pre-existing artifact
        assert task_obj.plan_artifact_id == existing_artifact_id
        db.rollback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_existing_artifact_is_updated_not_inserted(self, tmp_path: Path) -> None:
        """When artifact already exists in DB (first SELECT finds it), update
        its metadata in place rather than inserting a new row."""
        plan_path = tmp_path / "plan.md"
        plan_path.write_text("# Plan")

        task_id = uuid.uuid4()
        task_obj = _make_task_obj(task_id)
        existing = _make_artifact_obj()

        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing  # already there
        db.execute = AsyncMock(return_value=result_mock)
        db.get = AsyncMock(return_value=task_obj)
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.commit = AsyncMock()
        db.rollback = AsyncMock()

        sm_mock = MagicMock()
        host = _Host(db_session_factory=_make_db_factory(db), session_manager=sm_mock)
        session = _make_session(plan_output_path=str(plan_path), task_id=str(task_id))
        await host._finalize_plan_session(session)

        # db.add should NOT have been called (no new row inserted)
        db.add.assert_not_called()
        # Task linked to existing artifact
        assert task_obj.plan_artifact_id == existing.id
