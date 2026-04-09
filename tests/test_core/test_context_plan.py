"""Tests for ContextMixin._build_plan_context.

Covers:
- Returns None for plan sessions (session_purpose == "plan")
- Returns None when session has no primary_task_id in metadata
- Returns None when db_session_factory is not configured
- Returns None when task not found in DB
- Returns None when task has no plan_artifact_id
- Returns None when artifact file does not exist on disk
- Returns plan content for a valid task + artifact
- Truncates plan content to _MAX_PLAN_CONTEXT_CHARS (8 000)
- Includes truncation note when plan is clipped
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.context import ContextMixin
from src.core.session import SessionManager, SessionType

if TYPE_CHECKING:
    from pathlib import Path

    from src.core.session import ActiveSession


# ---------------------------------------------------------------------------
# Minimal concrete host
# ---------------------------------------------------------------------------


class _ContextHost(ContextMixin):
    def __init__(self, db_session_factory=None) -> None:
        self._tool_registry = MagicMock()
        self._settings = None
        self._db_session_factory = db_session_factory

    async def _execute_tool(self, session, tool_call):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db_factory(db: AsyncMock) -> MagicMock:
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


def _make_session(
    *,
    session_purpose: str | None = None,
    primary_task_id: str | None = None,
) -> ActiveSession:
    sm = SessionManager("test")
    session = sm.create_session(SessionType.CONVERSATIONAL)
    if session_purpose is not None:
        session.metadata["session_purpose"] = session_purpose
    if primary_task_id is not None:
        session.metadata["primary_task_id"] = primary_task_id
    return session


def _make_task(
    task_id: uuid.UUID,
    plan_artifact_id: uuid.UUID | None = None,
) -> MagicMock:
    task = MagicMock()
    task.id = task_id
    task.plan_artifact_id = plan_artifact_id
    return task


def _make_artifact(file_path: str, file_exists: bool = True) -> MagicMock:
    art = MagicMock()
    art.file_path = file_path
    art.file_exists = file_exists
    return art


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBuildPlanContext:
    @pytest.mark.asyncio
    async def test_returns_none_for_plan_sessions(self) -> None:
        host = _ContextHost()
        session = _make_session(session_purpose="plan", primary_task_id=str(uuid.uuid4()))
        result = await host._build_plan_context(session)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_primary_task_id(self) -> None:
        host = _ContextHost()
        session = _make_session()  # no primary_task_id
        result = await host._build_plan_context(session)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_db_factory(self) -> None:
        host = _ContextHost(db_session_factory=None)
        session = _make_session(primary_task_id=str(uuid.uuid4()))
        result = await host._build_plan_context(session)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_primary_task_id_is_invalid_uuid(self) -> None:
        db = AsyncMock()
        host = _ContextHost(db_session_factory=_make_db_factory(db))
        session = _make_session(primary_task_id="not-a-uuid")
        result = await host._build_plan_context(session)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_task_not_found(self) -> None:
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)  # task not in DB

        host = _ContextHost(db_session_factory=_make_db_factory(db))
        session = _make_session(primary_task_id=str(uuid.uuid4()))
        result = await host._build_plan_context(session)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_task_has_no_plan_artifact(self) -> None:
        task_id = uuid.uuid4()
        db = AsyncMock()
        db.get = AsyncMock(return_value=_make_task(task_id, plan_artifact_id=None))

        host = _ContextHost(db_session_factory=_make_db_factory(db))
        session = _make_session(primary_task_id=str(task_id))
        result = await host._build_plan_context(session)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_artifact_not_found(self) -> None:
        task_id = uuid.uuid4()
        artifact_id = uuid.uuid4()

        db = AsyncMock()
        db.get = AsyncMock(
            side_effect=[
                _make_task(task_id, plan_artifact_id=artifact_id),
                None,  # artifact lookup returns None
            ]
        )

        host = _ContextHost(db_session_factory=_make_db_factory(db))
        session = _make_session(primary_task_id=str(task_id))
        result = await host._build_plan_context(session)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_artifact_file_not_exists_flag(self) -> None:
        task_id = uuid.uuid4()
        artifact_id = uuid.uuid4()

        db = AsyncMock()
        db.get = AsyncMock(
            side_effect=[
                _make_task(task_id, plan_artifact_id=artifact_id),
                _make_artifact("/nonexistent/plan.md", file_exists=False),
            ]
        )

        host = _ContextHost(db_session_factory=_make_db_factory(db))
        session = _make_session(primary_task_id=str(task_id))
        result = await host._build_plan_context(session)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_plan_file_missing_on_disk(self, tmp_path: Path) -> None:
        task_id = uuid.uuid4()
        artifact_id = uuid.uuid4()
        missing_path = str(tmp_path / "missing.md")

        db = AsyncMock()
        db.get = AsyncMock(
            side_effect=[
                _make_task(task_id, plan_artifact_id=artifact_id),
                _make_artifact(missing_path, file_exists=True),
            ]
        )

        host = _ContextHost(db_session_factory=_make_db_factory(db))
        session = _make_session(primary_task_id=str(task_id))
        result = await host._build_plan_context(session)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_plan_content_for_valid_task(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.md"
        plan_content = "# Plan\n\n- Step 1: Do the thing\n- Step 2: Test it\n"
        plan_path.write_text(plan_content, encoding="utf-8")

        task_id = uuid.uuid4()
        artifact_id = uuid.uuid4()

        db = AsyncMock()
        db.get = AsyncMock(
            side_effect=[
                _make_task(task_id, plan_artifact_id=artifact_id),
                _make_artifact(str(plan_path), file_exists=True),
            ]
        )

        host = _ContextHost(db_session_factory=_make_db_factory(db))
        session = _make_session(primary_task_id=str(task_id))
        result = await host._build_plan_context(session)

        assert result is not None
        assert "Implementation Plan" in result
        assert "Step 1: Do the thing" in result

    @pytest.mark.asyncio
    async def test_truncates_long_plan(self, tmp_path: Path) -> None:
        max_chars = 8_000
        plan_path = tmp_path / "plan.md"
        long_content = "x" * (max_chars + 500)
        plan_path.write_text(long_content, encoding="utf-8")

        task_id = uuid.uuid4()
        artifact_id = uuid.uuid4()

        db = AsyncMock()
        db.get = AsyncMock(
            side_effect=[
                _make_task(task_id, plan_artifact_id=artifact_id),
                _make_artifact(str(plan_path), file_exists=True),
            ]
        )

        host = _ContextHost(db_session_factory=_make_db_factory(db))
        session = _make_session(primary_task_id=str(task_id))
        result = await host._build_plan_context(session)

        assert result is not None
        assert "truncated" in result
        assert str(plan_path) in result  # truncation note includes full path
        # Total content should not exceed max + preamble + truncation suffix overhead
        plan_text_portion = result.split("## Implementation Plan")[1]
        assert len(plan_text_portion) < max_chars + 500

    @pytest.mark.asyncio
    async def test_short_plan_not_truncated(self, tmp_path: Path) -> None:
        plan_path = tmp_path / "plan.md"
        content = "# Short plan\n\nDo it in one step."
        plan_path.write_text(content, encoding="utf-8")

        task_id = uuid.uuid4()
        artifact_id = uuid.uuid4()

        db = AsyncMock()
        db.get = AsyncMock(
            side_effect=[
                _make_task(task_id, plan_artifact_id=artifact_id),
                _make_artifact(str(plan_path), file_exists=True),
            ]
        )

        host = _ContextHost(db_session_factory=_make_db_factory(db))
        session = _make_session(primary_task_id=str(task_id))
        result = await host._build_plan_context(session)

        assert result is not None
        assert "truncated" not in result
        assert content in result
