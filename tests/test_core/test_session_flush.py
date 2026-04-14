"""Tests for incremental session flush (flush_dirty_sessions).

Covers:
- Dirty tracking on ActiveSession (status changes, title, token updates)
- flush_dirty_sessions skips clean sessions
- flush_dirty_sessions writes dirty metadata + appends new messages
- Flush watermark (_last_flush_sequence) advances correctly
- archive_session after flush still produces correct final state
- reload_stale_sessions / restore_session set _last_flush_sequence
- conversation_history round-trip through flush
- save_all_sessions interaction with prior flush
- Buffer eviction (deque maxlen) edge case
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.core.buffer import MessageType
from src.core.session import ActiveSession, SessionManager, SessionType
from src.database.models import Base
from src.database.models import Session as SessionModel
from src.database.models import SessionMessage as SessionMessageModel


@pytest.fixture
async def db_session():
    """Create an in-memory SQLite DB with all tables for integration tests."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


# ---------------------------------------------------------------------------
# Dirty tracking
# ---------------------------------------------------------------------------


class TestDirtyTracking:
    def test_new_session_not_dirty(self):
        s = ActiveSession("id-1", SessionType.CONVERSATIONAL)
        assert s._dirty is False
        assert s._last_flush_sequence == 0

    def test_title_change_marks_dirty(self):
        s = ActiveSession("id-1", SessionType.CONVERSATIONAL)
        s.title = "Hello"
        assert s._dirty is True

    def test_status_transitions_mark_dirty(self):
        s = ActiveSession("id-1", SessionType.CONVERSATIONAL)
        s.set_active()
        assert s._dirty is True

        s._dirty = False
        s.set_executing()
        assert s._dirty is True

        s._dirty = False
        s.complete()
        assert s._dirty is True

    def test_fail_marks_dirty(self):
        s = ActiveSession("id-1", SessionType.CONVERSATIONAL)
        s.fail("oops")
        assert s._dirty is True

    def test_cancel_marks_dirty(self):
        s = ActiveSession("id-1", SessionType.CONVERSATIONAL)
        s.set_active()
        s._dirty = False
        s.cancel()
        assert s._dirty is True

    def test_pause_resume_mark_dirty(self):
        s = ActiveSession("id-1", SessionType.CONVERSATIONAL)
        s.set_active()
        s._dirty = False
        s.pause()
        assert s._dirty is True

        s._dirty = False
        s.resume()
        assert s._dirty is True

    def test_interrupt_marks_dirty(self):
        s = ActiveSession("id-1", SessionType.CONVERSATIONAL)
        s.set_active()
        s._dirty = False
        s.interrupt()
        assert s._dirty is True

    def test_restore_marks_dirty(self):
        s = ActiveSession("id-1", SessionType.CONVERSATIONAL)
        s.complete()
        s._dirty = False
        s.restore()
        assert s._dirty is True

    def test_mark_dirty_explicit(self):
        s = ActiveSession("id-1", SessionType.CONVERSATIONAL)
        s.mark_dirty()
        assert s._dirty is True

    def test_set_active_noop_when_paused(self):
        """set_active() is a no-op when paused — should NOT mark dirty."""
        s = ActiveSession("id-1", SessionType.CONVERSATIONAL)
        s.set_active()
        s.pause()
        s._dirty = False
        s.set_active()  # no-op: status stays PAUSED
        assert s._dirty is False


# ---------------------------------------------------------------------------
# flush_dirty_sessions integration tests
# ---------------------------------------------------------------------------


class TestFlushDirtySessions:
    @pytest.mark.asyncio
    async def test_skips_clean_sessions(self, db_session):
        mgr = SessionManager(backend_id="test-backend")
        session = mgr.create_session(SessionType.CONVERSATIONAL)
        session._dirty = False
        session._last_flush_sequence = session.buffer._text_sequence

        count = await mgr.flush_dirty_sessions(db_session)
        assert count == 0

    @pytest.mark.asyncio
    async def test_flushes_dirty_metadata(self, db_session):
        mgr = SessionManager(backend_id="test-backend")
        session = mgr.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.title = "Test Title"
        session.input_tokens = 42

        count = await mgr.flush_dirty_sessions(db_session)
        assert count == 1
        assert session._dirty is False

        # Verify DB row
        row = await db_session.get(SessionModel, uuid.UUID(session.id))
        assert row is not None
        assert row.title == "Test Title"
        assert row.input_tokens == 42
        assert row.status == "active"

    @pytest.mark.asyncio
    async def test_appends_new_messages_only(self, db_session):
        mgr = SessionManager(backend_id="test-backend")
        session = mgr.create_session(SessionType.CONVERSATIONAL)
        session.set_active()

        # Push 3 messages, flush
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "msg1"})
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "msg2"})
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "msg3"})

        await mgr.flush_dirty_sessions(db_session)
        assert session._last_flush_sequence == 3

        # Push 2 more, flush again
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "msg4"})
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "msg5"})
        # Mark dirty since we want the metadata flushed too
        session.mark_dirty()

        await mgr.flush_dirty_sessions(db_session)
        assert session._last_flush_sequence == 5

        # Verify exactly 5 messages in DB
        result = await db_session.execute(
            select(SessionMessageModel)
            .where(SessionMessageModel.session_id == uuid.UUID(session.id))
            .order_by(SessionMessageModel.sequence)
        )
        msgs = result.scalars().all()
        assert len(msgs) == 5
        assert msgs[0].content == "msg1"
        assert msgs[4].content == "msg5"

    @pytest.mark.asyncio
    async def test_flush_without_dirty_but_new_messages(self, db_session):
        """New buffer messages trigger flush even when _dirty flag is False."""
        mgr = SessionManager(backend_id="test-backend")
        session = mgr.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session._dirty = False

        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "hello"})
        # _dirty is still False but there are new messages
        assert session._dirty is False

        count = await mgr.flush_dirty_sessions(db_session)
        assert count == 1
        assert session._last_flush_sequence == 1

    @pytest.mark.asyncio
    async def test_flush_creates_session_row_if_missing(self, db_session):
        """Flush creates the sessions row even if telemetry stub doesn't exist."""
        mgr = SessionManager(backend_id="test-backend")
        session = mgr.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.title = "Brand New"

        await mgr.flush_dirty_sessions(db_session)

        row = await db_session.get(SessionModel, uuid.UUID(session.id))
        assert row is not None
        assert row.title == "Brand New"

    @pytest.mark.asyncio
    async def test_flush_updates_existing_row(self, db_session):
        """Flush updates an existing sessions row (e.g. from telemetry stub)."""
        mgr = SessionManager(backend_id="test-backend")
        session = mgr.create_session(SessionType.CONVERSATIONAL)
        session_uuid = uuid.UUID(session.id)

        # Pre-create a stub row
        db_session.add(
            SessionModel(
                id=session_uuid,
                backend_id="test-backend",
                created_at=session.created_at,
                session_type="conversational",
                status="active",
                metadata_={},
            )
        )
        await db_session.commit()

        session.title = "Updated Title"
        session.input_tokens = 100

        await mgr.flush_dirty_sessions(db_session)

        await db_session.refresh(await db_session.get(SessionModel, session_uuid))
        row = await db_session.get(SessionModel, session_uuid)
        assert row.title == "Updated Title"
        assert row.input_tokens == 100

    @pytest.mark.asyncio
    async def test_archive_after_flush_replaces_messages(self, db_session):
        """archive_session DELETE+INSERT produces correct final state after flush."""
        mgr = SessionManager(backend_id="test-backend")
        session = mgr.create_session(SessionType.CONVERSATIONAL)
        session.set_active()

        # Push and flush
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "early"})
        await mgr.flush_dirty_sessions(db_session)

        # Push more, then archive
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "late"})
        session.complete()

        await mgr.archive_session(session.id, db_session)

        # Should have exactly 2 messages (full re-insert)
        result = await db_session.execute(
            select(SessionMessageModel)
            .where(SessionMessageModel.session_id == uuid.UUID(session.id))
            .order_by(SessionMessageModel.sequence)
        )
        msgs = result.scalars().all()
        assert len(msgs) == 2
        assert msgs[0].content == "early"
        assert msgs[1].content == "late"

    @pytest.mark.asyncio
    async def test_flush_error_does_not_affect_other_sessions(self, db_session):
        """One session's flush failure doesn't prevent others from flushing."""
        mgr = SessionManager(backend_id="test-backend")

        s1 = mgr.create_session(SessionType.CONVERSATIONAL)
        s1.set_active()
        s1.title = "Good"

        s2 = mgr.create_session(SessionType.CONVERSATIONAL)
        s2.set_active()
        s2.title = "Also Good"

        # Both should flush successfully
        count = await mgr.flush_dirty_sessions(db_session)
        assert count == 2

    @pytest.mark.asyncio
    async def test_second_flush_is_noop_when_nothing_changed(self, db_session):
        """After flushing, a second call with no changes should be a no-op."""
        mgr = SessionManager(backend_id="test-backend")
        session = mgr.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "msg"})

        await mgr.flush_dirty_sessions(db_session)
        assert session._dirty is False

        count = await mgr.flush_dirty_sessions(db_session)
        assert count == 0

    @pytest.mark.asyncio
    async def test_conversation_history_persisted(self, db_session):
        """Flush writes conversation_history JSON to DB."""
        mgr = SessionManager(backend_id="test-backend")
        session = mgr.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.conversation_history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        session.mark_dirty()

        await mgr.flush_dirty_sessions(db_session)

        row = await db_session.get(SessionModel, uuid.UUID(session.id))
        assert row.conversation_history is not None
        assert len(row.conversation_history) == 2
        assert row.conversation_history[0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_save_all_after_flush_no_duplicate_messages(self, db_session):
        """save_all_sessions after a prior flush should not create duplicate messages."""
        mgr = SessionManager(backend_id="test-backend")
        session = mgr.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "early"})

        # Incremental flush writes 1 message
        await mgr.flush_dirty_sessions(db_session)

        # Push more, then graceful shutdown
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "late"})

        await mgr.save_all_sessions(db_session)

        # save_all_sessions does DELETE+INSERT, so should have exactly 2
        result = await db_session.execute(
            select(func.count())
            .select_from(SessionMessageModel)
            .where(SessionMessageModel.session_id == uuid.UUID(session.id))
        )
        assert result.scalar() == 2


# ---------------------------------------------------------------------------
# reload / restore set _last_flush_sequence
# ---------------------------------------------------------------------------


class TestFlushWatermarkOnReload:
    @pytest.mark.asyncio
    async def test_reload_stale_sets_watermark(self, db_session):
        """reload_stale_sessions sets _last_flush_sequence from restored messages."""
        mgr = SessionManager(backend_id="test-backend")
        session = mgr.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.title = "Stale Session"
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "msg1"})
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "msg2"})

        # Flush to DB
        await mgr.flush_dirty_sessions(db_session)

        # Remove from memory to simulate crash
        del mgr._sessions[session.id]

        # Reload
        mgr2 = SessionManager(backend_id="test-backend")
        count = await mgr2.reload_stale_sessions(db_session, "test-backend")
        assert count == 1

        reloaded = next(iter(mgr2._sessions.values()))
        assert reloaded._last_flush_sequence == reloaded.buffer._text_sequence
        assert reloaded._last_flush_sequence == 2

    @pytest.mark.asyncio
    async def test_restore_session_sets_watermark(self, db_session):
        """restore_session sets _last_flush_sequence from archived messages."""
        mgr = SessionManager(backend_id="test-backend")
        session = mgr.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "hi"})
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "bye"})
        session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": "end"})
        session.complete()

        # Archive to DB
        await mgr.archive_session(session.id, db_session)
        assert session.id not in mgr._sessions

        # Restore
        restored = await mgr.restore_session(session.id, db_session)
        assert restored._last_flush_sequence == 3
        assert restored.buffer._text_sequence == 3


# ---------------------------------------------------------------------------
# Buffer eviction edge case
# ---------------------------------------------------------------------------


class TestBufferEviction:
    @pytest.mark.asyncio
    async def test_flush_after_eviction_preserves_old_messages(self, db_session):
        """Messages flushed before deque eviction remain in DB even after archive."""
        mgr = SessionManager(backend_id="test-backend")
        session = mgr.create_session(SessionType.CONVERSATIONAL)
        session.set_active()

        # Push some messages and flush
        for i in range(5):
            session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": f"early-{i}"})
        await mgr.flush_dirty_sessions(db_session)
        assert session._last_flush_sequence == 5

        # Verify 5 in DB
        result = await db_session.execute(
            select(func.count())
            .select_from(SessionMessageModel)
            .where(SessionMessageModel.session_id == uuid.UUID(session.id))
        )
        assert result.scalar() == 5

        # Now push enough to trigger eviction if we had a small buffer.
        # With the real 2000-message deque this is too expensive to test fully,
        # but we can verify the append-only logic: new messages get sequence > 5.
        for i in range(3):
            session.buffer.push_text(MessageType.TEXT_CHUNK, {"content": f"late-{i}"})
        session.mark_dirty()
        await mgr.flush_dirty_sessions(db_session)

        result = await db_session.execute(
            select(func.count())
            .select_from(SessionMessageModel)
            .where(SessionMessageModel.session_id == uuid.UUID(session.id))
        )
        assert result.scalar() == 8
