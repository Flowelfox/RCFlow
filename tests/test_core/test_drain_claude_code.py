"""Tests for the pending-message drain path on the Claude Code branch.

These exercise :meth:`PromptRouter.schedule_pending_drain`,
:meth:`PromptRouter._drain_one`, and the
``_schedule_drain_after_stream_task`` helper added on
:class:`~src.core.agent_claude_code.ClaudeCodeAgentMixin`.

The goal: confirm a single queued message reliably reaches
``handle_prompt`` (and that ``handle_prompt`` failures surface as
``ERROR`` rather than silently disappearing), so the bug "queued
messages show in history but Claude Code never reacts" cannot
regress un-noticed.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.core.buffer import MessageType
from src.core.pending_store import SessionPendingMessageStore
from src.core.prompt_router import PromptRouter
from src.core.session import ActiveSession, SessionManager, SessionType
from src.database.models import Base
from src.database.models import Session as SessionModel


@pytest.fixture
async def _db_factory(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'drain.db'}"
    engine = create_async_engine(
        db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def _make_session(db_factory, session_manager: SessionManager) -> ActiveSession:
    """Persist a sessions row and register an in-memory ActiveSession."""
    sid = str(uuid.uuid4())
    async with db_factory() as db:
        row = SessionModel(
            id=uuid.UUID(sid),
            backend_id="test",
            created_at=datetime.now(UTC),
            session_type="long-running",
            status="active",
            title=None,
            metadata_={},
            conversation_history=[],
        )
        db.add(row)
        await db.commit()
    session = ActiveSession(sid, SessionType.LONG_RUNNING)
    session_manager._sessions[sid] = session
    return session


def _make_router(session_manager: SessionManager, store: SessionPendingMessageStore) -> PromptRouter:
    llm_client = MagicMock()
    tool_registry = MagicMock()
    return PromptRouter(llm_client, session_manager, tool_registry, pending_store=store)


async def _drain_quiet(router: PromptRouter) -> None:
    """Wait for all currently-scheduled drain tasks to finish."""
    while router._drain_tasks:
        await asyncio.gather(*list(router._drain_tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_drain_delivers_single_queued_message(_db_factory, tmp_path):
    session_manager = SessionManager("test-backend")
    store = SessionPendingMessageStore(_db_factory, tmp_path / "attachments")
    session = await _make_session(_db_factory, session_manager)
    router = _make_router(session_manager, store)

    handle_calls: list[dict] = []

    async def fake_handle(text: str, session_id=None, **kwargs):
        handle_calls.append({"text": text, "session_id": session_id, **kwargs})
        return session_id or session.id

    router.handle_prompt = fake_handle  # type: ignore[method-assign]

    entry = await store.enqueue(
        session,
        content="hello world",
        display_content="hello world",
        attachments=None,
        project_name=None,
        selected_worktree_path=None,
        task_id=None,
    )

    router.schedule_pending_drain(session)
    await _drain_quiet(router)

    assert handle_calls == [
        {
            "text": "hello world",
            "session_id": session.id,
            "attachments": None,
            "project_name": None,
            "selected_worktree_path": None,
            "task_id": None,
            "display_text": "hello world",
            "queued_id": entry.queued_id,
        }
    ]
    assert session.pending_user_messages == []


@pytest.mark.asyncio
async def test_drain_surfaces_error_on_handle_prompt_failure(_db_factory, tmp_path):
    session_manager = SessionManager("test-backend")
    store = SessionPendingMessageStore(_db_factory, tmp_path / "attachments")
    session = await _make_session(_db_factory, session_manager)
    router = _make_router(session_manager, store)

    async def boom(*_a, **_kw):
        raise RuntimeError("forward exploded")

    router.handle_prompt = boom  # type: ignore[method-assign]

    await store.enqueue(
        session,
        content="x",
        display_content="x",
        attachments=None,
        project_name=None,
        selected_worktree_path=None,
        task_id=None,
    )

    router.schedule_pending_drain(session)
    await _drain_quiet(router)

    errors = [m for m in session.buffer.text_history if m.message_type == MessageType.ERROR]
    assert len(errors) == 1
    assert errors[0].data["code"] == "QUEUED_MESSAGE_DELIVERY_FAILED"
    # The message was already popped before the failure — that's expected, the
    # drain emits ERROR so the user sees something and can resend by hand.
    assert session.pending_user_messages == []


@pytest.mark.asyncio
async def test_drain_self_propels_through_multi_message_queue(_db_factory, tmp_path):
    session_manager = SessionManager("test-backend")
    store = SessionPendingMessageStore(_db_factory, tmp_path / "attachments")
    session = await _make_session(_db_factory, session_manager)
    router = _make_router(session_manager, store)

    handle_calls: list[str] = []

    async def fake_handle(text: str, session_id=None, **kwargs):
        handle_calls.append(text)
        return session_id or session.id

    router.handle_prompt = fake_handle  # type: ignore[method-assign]

    for word in ("one", "two", "three"):
        await store.enqueue(
            session,
            content=word,
            display_content=word,
            attachments=None,
            project_name=None,
            selected_worktree_path=None,
            task_id=None,
        )

    # A single schedule call should drive the queue to empty thanks to the
    # tail re-schedule in _drain_one.
    router.schedule_pending_drain(session)
    await _drain_quiet(router)

    assert handle_calls == ["one", "two", "three"]
    assert session.pending_user_messages == []


@pytest.mark.asyncio
async def test_schedule_drain_after_stream_task_waits_for_done(_db_factory, tmp_path):
    """The ``add_done_callback`` wiring must defer the drain until the
    stream task has actually finished. Without that guarantee the drain
    races ``_drain_monitor_events`` and ``is_busy_for_queue()`` can be
    True at drain time.
    """
    session_manager = SessionManager("test-backend")
    store = SessionPendingMessageStore(_db_factory, tmp_path / "attachments")
    session = await _make_session(_db_factory, session_manager)
    router = _make_router(session_manager, store)

    handle_calls: list[str] = []

    async def fake_handle(text: str, session_id=None, **kwargs):
        handle_calls.append(text)
        return session_id or session.id

    router.handle_prompt = fake_handle  # type: ignore[method-assign]

    await store.enqueue(
        session,
        content="queued",
        display_content="queued",
        attachments=None,
        project_name=None,
        selected_worktree_path=None,
        task_id=None,
    )

    # Simulate a still-running stream task. The helper should NOT drain
    # yet — it must wait for this task to transition to done().
    gate: asyncio.Future[None] = asyncio.get_event_loop().create_future()

    async def stream_body() -> None:
        await gate

    stream_task = asyncio.create_task(stream_body())
    session._claude_code_stream_task = stream_task

    router._schedule_drain_after_stream_task(session)
    # Yield once so the event loop can run any callbacks that *shouldn't* fire.
    await asyncio.sleep(0)
    assert handle_calls == []  # still gated by the running stream task

    # Release the stream task → the done-callback fires → drain proceeds.
    gate.set_result(None)
    await stream_task
    await _drain_quiet(router)

    assert handle_calls == ["queued"]


@pytest.mark.asyncio
async def test_schedule_drain_after_stream_task_runs_immediately_when_done(_db_factory, tmp_path):
    session_manager = SessionManager("test-backend")
    store = SessionPendingMessageStore(_db_factory, tmp_path / "attachments")
    session = await _make_session(_db_factory, session_manager)
    router = _make_router(session_manager, store)

    handle = AsyncMock(return_value=session.id)
    router.handle_prompt = handle  # type: ignore[method-assign]

    await store.enqueue(
        session,
        content="x",
        display_content="x",
        attachments=None,
        project_name=None,
        selected_worktree_path=None,
        task_id=None,
    )

    # No stream task → schedule immediately.
    session._claude_code_stream_task = None
    router._schedule_drain_after_stream_task(session)
    await _drain_quiet(router)
    assert handle.await_count == 1
