"""Unit tests for :mod:`src.core.wakeup_store`."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.core.buffer import MessageType
from src.core.session import ActiveSession, SessionManager, SessionType
from src.core.wakeup_store import SessionScheduledWakeStore
from src.database.models import Base
from src.database.models import Session as SessionModel


@pytest.fixture
async def _db_factory(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'wakes.db'}"
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


async def _make_session(db_factory) -> ActiveSession:
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
    return ActiveSession(sid, SessionType.LONG_RUNNING)


@pytest.mark.asyncio
async def test_enqueue_persists_and_mirrors(_db_factory):
    session = await _make_session(_db_factory)
    store = SessionScheduledWakeStore(_db_factory)
    fire_at = datetime.now(UTC) + timedelta(minutes=2)
    wake = await store.enqueue(session, prompt="hi", reason="loop", fire_at=fire_at)

    assert wake.wake_id
    assert len(session.scheduled_wakes) == 1
    assert session.scheduled_wakes[0].prompt == "hi"
    # WAKEUP_SCHEDULED pushed to the buffer
    types = [m.message_type for m in session.buffer.text_history]
    assert MessageType.WAKEUP_SCHEDULED in types


@pytest.mark.asyncio
async def test_mark_fired_and_cancel(_db_factory):
    session = await _make_session(_db_factory)
    store = SessionScheduledWakeStore(_db_factory)
    w1 = await store.enqueue(
        session,
        prompt="p1",
        reason="r1",
        fire_at=datetime.now(UTC) + timedelta(seconds=120),
    )
    w2 = await store.enqueue(
        session,
        prompt="p2",
        reason="r2",
        fire_at=datetime.now(UTC) + timedelta(seconds=240),
    )

    removed = await store.mark_fired(session, w1.wake_id)
    assert removed is not None and removed.wake_id == w1.wake_id
    assert [w.wake_id for w in session.scheduled_wakes] == [w2.wake_id]

    cancelled = await store.cancel(session, w2.wake_id)
    assert cancelled is not None
    assert session.scheduled_wakes == []


@pytest.mark.asyncio
async def test_cancel_all_for_session(_db_factory):
    session = await _make_session(_db_factory)
    store = SessionScheduledWakeStore(_db_factory)
    for i in range(3):
        await store.enqueue(
            session,
            prompt=f"p{i}",
            reason="loop",
            fire_at=datetime.now(UTC) + timedelta(seconds=60 * (i + 1)),
        )
    cancelled = await store.cancel_all_for_session(session, reason="session_ended")
    assert len(cancelled) == 3
    assert session.scheduled_wakes == []


@pytest.mark.asyncio
async def test_restore_all_pending_rehydrates_mirror(_db_factory):
    # First session lifecycle: enqueue two wakes, simulate restart.
    session = await _make_session(_db_factory)
    store = SessionScheduledWakeStore(_db_factory)
    await store.enqueue(
        session,
        prompt="p1",
        reason="r1",
        fire_at=datetime.now(UTC) + timedelta(seconds=120),
    )
    await store.enqueue(
        session,
        prompt="p2",
        reason="r2",
        fire_at=datetime.now(UTC) + timedelta(seconds=240),
    )

    # Fresh in-memory session — mimics SessionManager after a restart.
    sm = SessionManager(backend_id="test")
    revived = ActiveSession(session.id, SessionType.LONG_RUNNING)
    sm._sessions[session.id] = revived

    restored = await store.restore_all_pending(sm)
    assert len(restored) == 2
    assert [w.prompt for _sid, w in restored] == ["p1", "p2"]
    assert len(revived.scheduled_wakes) == 2
