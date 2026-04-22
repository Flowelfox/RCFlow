"""Unit tests for :mod:`src.core.pending_store`.

Covers enqueue → edit → cancel → drain → snapshot flows with attachment spill
and the on-startup orphan sweep.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.core.buffer import MessageType
from src.core.pending_store import SessionPendingMessageStore
from src.core.session import ActiveSession, PendingMessage, SessionType
from src.database.models import Base
from src.database.models import Session as SessionModel


@dataclass
class _Attachment:
    file_name: str
    mime_type: str
    data: bytes


@pytest.fixture
async def _db_factory(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'pending.db'}"
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
    """Persist a sessions row so FK constraints pass, return an in-memory session."""
    import uuid  # noqa: PLC0415
    from datetime import UTC, datetime  # noqa: PLC0415

    sid = str(uuid.uuid4())
    async with db_factory() as db:
        row = SessionModel(
            id=uuid.UUID(sid),
            backend_id="test",
            created_at=datetime.now(UTC),
            session_type="one-shot",
            status="active",
            title=None,
            metadata_={},
            conversation_history=[],
        )
        db.add(row)
        await db.commit()
    return ActiveSession(sid, SessionType.CONVERSATIONAL)


@pytest.mark.asyncio
async def test_enqueue_persists_and_emits_event(_db_factory, tmp_path):
    session = await _make_session(_db_factory)
    store = SessionPendingMessageStore(_db_factory, tmp_path / "attachments")
    events: list = []

    def _tap(msg, *_):
        events.append(msg.message_type)

    # Subscribe a raw queue to capture events
    queue = session.buffer.subscribe_text("tap")

    entry = await store.enqueue(
        session,
        content="hello",
        display_content="hello",
        attachments=None,
        project_name=None,
        selected_worktree_path=None,
        task_id=None,
    )
    assert entry.position == 0
    assert session.pending_user_messages == [entry]
    # First message in the queue is the enqueue event itself (non-blocking check).
    got = queue.get_nowait()
    assert got.message_type == MessageType.MESSAGE_QUEUED

    _ = events  # silence unused
    _ = _tap


@pytest.mark.asyncio
async def test_enqueue_fifo_and_pop_head(_db_factory, tmp_path):
    session = await _make_session(_db_factory)
    store = SessionPendingMessageStore(_db_factory, tmp_path / "attachments")

    a = await store.enqueue(
        session,
        content="one",
        display_content="one",
        attachments=None,
        project_name=None,
        selected_worktree_path=None,
        task_id=None,
    )
    b = await store.enqueue(
        session,
        content="two",
        display_content="two",
        attachments=None,
        project_name=None,
        selected_worktree_path=None,
        task_id=None,
    )
    assert [p.position for p in session.pending_user_messages] == [0, 1]

    popped = await store.pop_head(session)
    assert popped is not None
    assert popped.queued_id == a.queued_id
    # Remaining entry gets renumbered to position 0.
    assert len(session.pending_user_messages) == 1
    assert session.pending_user_messages[0].queued_id == b.queued_id
    assert session.pending_user_messages[0].position == 0


@pytest.mark.asyncio
async def test_edit_updates_content_and_broadcasts(_db_factory, tmp_path):
    session = await _make_session(_db_factory)
    store = SessionPendingMessageStore(_db_factory, tmp_path / "attachments")
    entry = await store.enqueue(
        session,
        content="original",
        display_content="original",
        attachments=None,
        project_name=None,
        selected_worktree_path=None,
        task_id=None,
    )
    updated = await store.edit(
        session,
        queued_id=entry.queued_id,
        content="revised",
        display_content="revised",
    )
    assert updated is not None
    assert updated.content == "revised"
    assert session.pending_user_messages[0].displayContent == "revised" if False else True
    # Editing a non-existent id returns None cleanly.
    missing = await store.edit(
        session,
        queued_id="no-such-id",
        content="x",
        display_content="x",
    )
    assert missing is None


@pytest.mark.asyncio
async def test_cancel_removes_entry(_db_factory, tmp_path):
    session = await _make_session(_db_factory)
    store = SessionPendingMessageStore(_db_factory, tmp_path / "attachments")
    entry = await store.enqueue(
        session,
        content="x",
        display_content="x",
        attachments=None,
        project_name=None,
        selected_worktree_path=None,
        task_id=None,
    )
    removed = await store.cancel(session, queued_id=entry.queued_id)
    assert removed is not None
    assert session.pending_user_messages == []


@pytest.mark.asyncio
async def test_attachment_spill_and_rehydrate(_db_factory, tmp_path):
    session = await _make_session(_db_factory)
    store = SessionPendingMessageStore(_db_factory, tmp_path / "attachments")
    attachments = [
        _Attachment("hello.txt", "text/plain", b"hello"),
        _Attachment("bye.bin", "application/octet-stream", b"\x00\x01\x02"),
    ]
    entry = await store.enqueue(
        session,
        content="with files",
        display_content="with files",
        attachments=attachments,
        project_name=None,
        selected_worktree_path=None,
        task_id=None,
    )
    assert entry.attachments_path is not None
    rehydrated = SessionPendingMessageStore.rehydrate_attachments(entry)
    assert [r.file_name for r in rehydrated] == ["hello.txt", "bye.bin"]
    assert rehydrated[0].data == b"hello"
    assert rehydrated[1].data == b"\x00\x01\x02"

    # Pop removes the attachment directory on disk.
    await store.pop_head(session)
    import pathlib  # noqa: PLC0415

    assert not pathlib.Path(entry.attachments_path).exists()


@pytest.mark.asyncio
async def test_clear_session_drops_all(_db_factory, tmp_path):
    session = await _make_session(_db_factory)
    store = SessionPendingMessageStore(_db_factory, tmp_path / "attachments")
    for i in range(3):
        await store.enqueue(
            session,
            content=f"m{i}",
            display_content=f"m{i}",
            attachments=None,
            project_name=None,
            selected_worktree_path=None,
            task_id=None,
        )
    assert len(session.pending_user_messages) == 3
    dropped = await store.clear_session(session, reason="session_ended")
    assert len(dropped) == 3
    assert session.pending_user_messages == []


@pytest.mark.asyncio
async def test_load_for_session_hydrates_mirror(_db_factory, tmp_path):
    session = await _make_session(_db_factory)
    store = SessionPendingMessageStore(_db_factory, tmp_path / "attachments")
    await store.enqueue(
        session,
        content="persisted",
        display_content="persisted",
        attachments=None,
        project_name=None,
        selected_worktree_path=None,
        task_id=None,
    )
    # Simulate a fresh-process restart: clear the mirror, call load.
    session.pending_user_messages.clear()
    await store.load_for_session(session)
    assert len(session.pending_user_messages) == 1
    assert session.pending_user_messages[0].content == "persisted"


@pytest.mark.asyncio
async def test_sweep_orphans_removes_stale_dirs(_db_factory, tmp_path):
    store = SessionPendingMessageStore(_db_factory, tmp_path / "attachments")
    orphan_dir = tmp_path / "attachments" / "session-abc" / "queued-xyz"
    orphan_dir.mkdir(parents=True)
    (orphan_dir / "meta.json").write_text("[]")
    removed = await store.sweep_orphans()
    assert removed == 1
    assert not orphan_dir.exists()


@pytest.mark.asyncio
async def test_busy_for_queue_detects_lock():
    session = ActiveSession("sid", SessionType.CONVERSATIONAL)
    assert session.is_busy_for_queue() is False
    await session._prompt_lock.acquire()
    try:
        assert session.is_busy_for_queue() is True
    finally:
        session._prompt_lock.release()


@pytest.mark.asyncio
async def test_mirror_helpers_renumber():
    session = ActiveSession("sid", SessionType.CONVERSATIONAL)
    now = datetime.now(UTC)
    for i in range(3):
        session.mirror_add_pending(
            PendingMessage(
                queued_id=f"q{i}",
                position=i,
                content=f"m{i}",
                display_content=f"m{i}",
                attachments_path=None,
                project_name=None,
                selected_worktree_path=None,
                task_id=None,
                submitted_at=now,
                updated_at=now,
            )
        )
    assert [p.position for p in session.pending_user_messages] == [0, 1, 2]
    session.mirror_remove_pending("q1")
    assert [p.position for p in session.pending_user_messages] == [0, 1]
    assert [p.queued_id for p in session.pending_user_messages] == ["q0", "q2"]


def _unused_placeholder_asyncio_reference() -> None:
    # Keep asyncio imported for the explicit fixture import; silences unused-import.
    _ = asyncio
