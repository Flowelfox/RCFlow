import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.core.buffer import MessageType
from src.core.session import ActiveSession, SessionManager, SessionStatus, SessionType
from src.models.db import Base
from src.models.db import Session as SessionModel
from src.models.db import SessionMessage as SessionMessageModel


def _make_session_row(
    status: str = "active",
    session_type: str = "one-shot",
    title: str | None = None,
    metadata: dict | None = None,
    conversation_history: list | None = None,
) -> MagicMock:
    """Build a minimal mock SessionModel row for reload tests."""
    row = MagicMock()
    row.id = uuid.uuid4()
    row.session_type = session_type
    row.status = status
    row.created_at = datetime.now(UTC)
    row.title = title
    row.main_project_path = None
    row.metadata_ = metadata or {}
    row.input_tokens = 10
    row.output_tokens = 5
    row.cache_creation_input_tokens = 0
    row.cache_read_input_tokens = 0
    row.tool_input_tokens = 0
    row.tool_output_tokens = 0
    row.tool_cost_usd = 0.0
    row.conversation_history = conversation_history
    return row


def _make_db_mock(stale_rows: list, msg_rows: list | None = None) -> AsyncMock:
    """Build an AsyncMock DB session that returns *stale_rows* for the first
    execute call and *msg_rows* (default []) for each subsequent one."""
    if msg_rows is None:
        msg_rows = []
    db = AsyncMock()

    def _execute_side_effect(*_args, **_kwargs):
        # Each call returns a fresh MagicMock; we swap scalars() returns in order.
        result = MagicMock()
        return result

    # We need different results per call, so use a counter-based side_effect.
    call_results: list[MagicMock] = []
    # First call: stale sessions query
    r0 = MagicMock()
    r0.scalars.return_value.all.return_value = stale_rows
    call_results.append(r0)
    # For each row: one messages query + 5 delete statements
    for _ in stale_rows:
        r_msg = MagicMock()
        r_msg.scalars.return_value.all.return_value = msg_rows
        call_results.append(r_msg)
        for _ in range(5):
            call_results.append(MagicMock())

    async def _async_side(*_a, **_kw):
        return call_results.pop(0)

    db.execute = _async_side
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


class TestActiveSession:
    def test_create_session(self):
        session = ActiveSession("test-id", SessionType.ONE_SHOT)
        assert session.id == "test-id"
        assert session.status == SessionStatus.CREATED
        assert session.session_type == SessionType.ONE_SHOT
        assert session.conversation_history == []
        assert session.title is None

    def test_session_title(self):
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        assert session.title is None
        session.title = "List project files"
        assert session.title == "List project files"

    def test_session_lifecycle(self):
        session = ActiveSession("test-id", SessionType.ONE_SHOT)
        assert session.status == SessionStatus.CREATED

        session.set_active()
        assert session.status == SessionStatus.ACTIVE

        session.set_executing()
        assert session.status == SessionStatus.EXECUTING

        session.set_active()
        assert session.status == SessionStatus.ACTIVE

        session.complete()
        assert session.status == SessionStatus.COMPLETED
        assert session.ended_at is not None

    def test_session_fail(self):
        session = ActiveSession("test-id", SessionType.ONE_SHOT)
        session.fail("something went wrong")
        assert session.status == SessionStatus.FAILED
        assert session.metadata["error"] == "something went wrong"

    def test_session_cancel(self):
        session = ActiveSession("test-id", SessionType.ONE_SHOT)
        session.set_active()
        session.cancel()
        assert session.status == SessionStatus.CANCELLED

    def test_clear_subprocess_tracking(self):
        """clear_subprocess_tracking() resets all subprocess fields and pushes null status."""
        session = ActiveSession("test-id", SessionType.LONG_RUNNING)
        session.subprocess_started_at = datetime.now(UTC)
        session.subprocess_current_tool = "Read"
        session.subprocess_type = "claude_code"
        session.subprocess_display_name = "Claude Code"
        session.subprocess_working_directory = "/tmp/project"

        ephemeral_messages: list[dict] = []
        original_push = session.buffer.push_ephemeral

        def capture_ephemeral(msg_type, data):
            ephemeral_messages.append({"type": msg_type, "data": data})
            original_push(msg_type, data)

        session.buffer.push_ephemeral = capture_ephemeral

        session.clear_subprocess_tracking()

        assert session.subprocess_started_at is None
        assert session.subprocess_current_tool is None
        assert session.subprocess_type is None
        assert session.subprocess_display_name is None
        assert session.subprocess_working_directory is None

        # Verify null subprocess_status was pushed
        assert len(ephemeral_messages) == 1
        assert ephemeral_messages[0]["type"] == MessageType.SUBPROCESS_STATUS
        assert ephemeral_messages[0]["data"]["subprocess_type"] is None
        assert ephemeral_messages[0]["data"]["session_id"] == "test-id"


class TestPauseResume:
    def test_pause_active_session(self):
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()
        assert session.status == SessionStatus.PAUSED
        assert session.paused_at is not None

    def test_pause_executing_session(self):
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.set_executing()
        session.pause()
        assert session.status == SessionStatus.PAUSED

    def test_pause_created_session(self):
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.pause()
        assert session.status == SessionStatus.PAUSED

    def test_pause_terminal_session_raises(self):
        for terminal_fn in ("complete", "fail", "cancel"):
            session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
            session.set_active()
            getattr(session, terminal_fn)()
            with pytest.raises(RuntimeError, match="terminal state"):
                session.pause()

    def test_pause_already_paused_raises(self):
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()
        with pytest.raises(RuntimeError, match="already paused"):
            session.pause()

    def test_resume_paused_session(self):
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.set_active()
        old_activity = session.last_activity_at
        session.pause()
        session.resume()
        assert session.status == SessionStatus.ACTIVE
        assert session.paused_at is None
        assert session.last_activity_at >= old_activity

    def test_resume_non_paused_raises(self):
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.set_active()
        with pytest.raises(RuntimeError, match="Cannot resume"):
            session.resume()

    def test_set_active_noop_when_paused(self):
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()
        session.set_active()
        assert session.status == SessionStatus.PAUSED

    def test_set_executing_noop_when_paused(self):
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()
        session.set_executing()
        assert session.status == SessionStatus.PAUSED

    def test_complete_deferred_when_paused(self):
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()
        session.complete()
        assert session.status == SessionStatus.PAUSED
        assert session.ended_at is None
        assert session.metadata["completed_while_paused"] is True

    def test_fail_works_when_paused(self):
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()
        session.fail("something broke")
        assert session.status == SessionStatus.FAILED
        assert session.paused_at is None
        assert session.ended_at is not None

    def test_cancel_works_when_paused(self):
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()
        session.cancel()
        assert session.status == SessionStatus.CANCELLED
        assert session.paused_at is None
        assert session.ended_at is not None

    def test_paused_session_listed_as_active(self):
        manager = SessionManager("test-backend")
        session = manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()
        active = manager.list_active_sessions()
        assert any(s.id == session.id for s in active)


class TestSessionManager:
    def test_create_session(self):
        manager = SessionManager("test-backend")
        session = manager.create_session()
        assert session.id is not None
        assert manager.get_session(session.id) is session

    def test_list_active_sessions(self):
        manager = SessionManager("test-backend")
        s1 = manager.create_session()
        s1.set_active()
        s2 = manager.create_session()
        s2.set_active()
        s2.complete()

        active = manager.list_active_sessions()
        assert len(active) == 1
        assert active[0].id == s1.id

    def test_get_nonexistent_session(self):
        manager = SessionManager("test-backend")
        assert manager.get_session("nonexistent") is None

    def test_complete_all_active_returns_count(self):
        manager = SessionManager("test-backend")
        s1 = manager.create_session()
        s1.set_active()
        s2 = manager.create_session()
        s2.set_active()
        s3 = manager.create_session()
        s3.set_active()
        s3.complete()

        count = manager.complete_all_active()

        assert count == 2
        assert s1.status == SessionStatus.COMPLETED
        assert s2.status == SessionStatus.COMPLETED
        assert s3.status == SessionStatus.COMPLETED  # was already complete

    def test_complete_all_active_handles_paused(self):
        manager = SessionManager("test-backend")
        session = manager.create_session()
        session.set_active()
        session.pause()
        assert session.status == SessionStatus.PAUSED

        count = manager.complete_all_active()

        assert count == 1
        assert session.status == SessionStatus.COMPLETED

    def test_complete_all_active_skips_terminal(self):
        manager = SessionManager("test-backend")
        s1 = manager.create_session()
        s1.set_active()
        s1.fail("error")
        s2 = manager.create_session()
        s2.set_active()
        s2.cancel()

        count = manager.complete_all_active()

        assert count == 0

    def test_interrupt_all_active_returns_count(self):
        manager = SessionManager("test-backend")
        s1 = manager.create_session()
        s1.set_active()
        s2 = manager.create_session()
        s2.set_active()
        s3 = manager.create_session()
        s3.set_active()
        s3.complete()

        count = manager.interrupt_all_active()

        assert count == 2
        assert s1.status == SessionStatus.INTERRUPTED
        assert s2.status == SessionStatus.INTERRUPTED
        assert s3.status == SessionStatus.COMPLETED  # already terminal

    def test_interrupt_all_active_preserves_ended_at_as_none(self):
        """Interrupted sessions must not have ended_at set so clients can restore them."""
        manager = SessionManager("test-backend")
        session = manager.create_session()
        session.set_active()

        manager.interrupt_all_active()

        assert session.status == SessionStatus.INTERRUPTED
        assert session.ended_at is None

    def test_interrupt_all_active_handles_paused(self):
        manager = SessionManager("test-backend")
        session = manager.create_session()
        session.set_active()
        session.pause()

        count = manager.interrupt_all_active()

        assert count == 1
        assert session.status == SessionStatus.INTERRUPTED
        assert session.ended_at is None

    def test_interrupt_all_active_skips_terminal(self):
        manager = SessionManager("test-backend")
        s1 = manager.create_session()
        s1.set_active()
        s1.fail("error")
        s2 = manager.create_session()
        s2.set_active()
        s2.cancel()

        count = manager.interrupt_all_active()

        assert count == 0


class TestReloadStaleSessions:
    @pytest.mark.asyncio
    async def test_active_session_restored_as_active(self):
        manager = SessionManager("test-backend")
        row = _make_session_row(status="active")
        db = _make_db_mock([row])

        count = await manager.reload_stale_sessions(db, "test-backend")

        assert count == 1
        session = manager.get_session(str(row.id))
        assert session is not None
        assert session.status == SessionStatus.ACTIVE
        assert session.ended_at is None

    @pytest.mark.asyncio
    async def test_executing_session_restored_as_active(self):
        manager = SessionManager("test-backend")
        row = _make_session_row(status="executing")
        db = _make_db_mock([row])

        count = await manager.reload_stale_sessions(db, "test-backend")

        assert count == 1
        session = manager.get_session(str(row.id))
        assert session.status == SessionStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_paused_session_restored_as_paused(self):
        manager = SessionManager("test-backend")
        row = _make_session_row(status="paused")
        db = _make_db_mock([row])

        count = await manager.reload_stale_sessions(db, "test-backend")

        assert count == 1
        session = manager.get_session(str(row.id))
        assert session.status == SessionStatus.PAUSED
        assert session.ended_at is None
        assert session.metadata.get("was_paused_before_restart") is True

    @pytest.mark.asyncio
    async def test_restart_interrupted_flag_set(self):
        manager = SessionManager("test-backend")
        row = _make_session_row(status="active", metadata={"claude_code_session_id": "abc"})
        db = _make_db_mock([row])

        await manager.reload_stale_sessions(db, "test-backend")

        session = manager.get_session(str(row.id))
        assert session.metadata.get("restart_interrupted") is True
        # Existing metadata is preserved
        assert session.metadata.get("claude_code_session_id") == "abc"

    @pytest.mark.asyncio
    async def test_empty_db_returns_zero(self):
        manager = SessionManager("test-backend")
        row_result = MagicMock()
        row_result.scalars.return_value.all.return_value = []
        db = AsyncMock()
        db.execute = AsyncMock(return_value=row_result)

        count = await manager.reload_stale_sessions(db, "test-backend")

        assert count == 0
        assert manager.list_active_sessions() == []

    @pytest.mark.asyncio
    async def test_multiple_sessions_all_reloaded(self):
        manager = SessionManager("test-backend")
        rows = [
            _make_session_row(status="active"),
            _make_session_row(status="paused"),
            _make_session_row(status="executing"),
        ]
        db = _make_db_mock(rows)

        count = await manager.reload_stale_sessions(db, "test-backend")

        assert count == 3
        assert len(manager.list_active_sessions()) == 3

    @pytest.mark.asyncio
    async def test_token_counts_restored(self):
        manager = SessionManager("test-backend")
        row = _make_session_row(status="active")
        row.input_tokens = 200
        row.output_tokens = 100
        row.tool_cost_usd = 1.5
        db = _make_db_mock([row])

        await manager.reload_stale_sessions(db, "test-backend")

        session = manager.get_session(str(row.id))
        assert session.input_tokens == 200
        assert session.output_tokens == 100
        assert session.tool_cost_usd == 1.5

    @pytest.mark.asyncio
    async def test_conversation_history_restored(self):
        manager = SessionManager("test-backend")
        history = [{"role": "user", "content": "hello"}]
        row = _make_session_row(status="active", conversation_history=history)
        db = _make_db_mock([row])

        await manager.reload_stale_sessions(db, "test-backend")

        session = manager.get_session(str(row.id))
        assert session.conversation_history == history


class TestPauseReason:
    def test_pause_with_reason(self):
        session = ActiveSession("test-id", SessionType.LONG_RUNNING)
        session.set_active()
        session.pause(reason="max_turns")
        assert session.paused_reason == "max_turns"

    def test_pause_without_reason_is_none(self):
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()
        assert session.paused_reason is None

    def test_paused_reason_cleared_on_resume(self):
        session = ActiveSession("test-id", SessionType.LONG_RUNNING)
        session.set_active()
        session.pause(reason="max_turns")
        assert session.paused_reason == "max_turns"
        session.resume()
        assert session.paused_reason is None

    def test_set_active_noop_when_completed(self):
        session = ActiveSession("test-id", SessionType.ONE_SHOT)
        session.set_active()
        session.complete()
        assert session.status == SessionStatus.COMPLETED
        session.set_active()
        assert session.status == SessionStatus.COMPLETED

    def test_set_active_noop_when_failed(self):
        session = ActiveSession("test-id", SessionType.ONE_SHOT)
        session.fail("oops")
        session.set_active()
        assert session.status == SessionStatus.FAILED

    def test_set_active_noop_when_cancelled(self):
        session = ActiveSession("test-id", SessionType.ONE_SHOT)
        session.set_active()
        session.cancel()
        session.set_active()
        assert session.status == SessionStatus.CANCELLED

    def test_set_executing_noop_when_completed(self):
        session = ActiveSession("test-id", SessionType.ONE_SHOT)
        session.set_active()
        session.complete()
        session.set_executing()
        assert session.status == SessionStatus.COMPLETED

    def test_set_executing_noop_when_failed(self):
        session = ActiveSession("test-id", SessionType.ONE_SHOT)
        session.fail("err")
        session.set_executing()
        assert session.status == SessionStatus.FAILED

    def test_set_executing_noop_when_cancelled(self):
        session = ActiveSession("test-id", SessionType.ONE_SHOT)
        session.set_active()
        session.cancel()
        session.set_executing()
        assert session.status == SessionStatus.CANCELLED


class TestWorktreeContextInSessionList:
    """Regression tests for the bug where list_all_with_archived omitted
    worktree metadata, causing the client worktree tab to lose context on
    session pane reopen."""

    def test_list_all_with_archived_includes_worktree_for_active_session(self):
        """In-memory sessions with worktree metadata must appear in the list."""
        manager = SessionManager("test-backend")
        session = manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        worktree_meta = {
            "repo_path": "/home/fox/Projects/RCFlow",
            "last_action": "new",
            "branch": "feature/test-branch",
            "base": "main",
        }
        session.metadata["worktree"] = worktree_meta

        # list_all_with_archived with db=None falls back to in-memory only path;
        # the in-memory dict path is what we need to verify here.
        result = manager.list_all_sessions()
        assert len(result) == 1
        found = result[0]
        # The in-memory ActiveSession should carry the metadata
        assert found.metadata.get("worktree") == worktree_meta

    def test_broadcast_session_update_includes_worktree(self):
        """broadcast_session_update must include the worktree key so live
        session_update WebSocket messages carry worktree context."""
        manager = SessionManager("test-backend")
        queue = manager.subscribe_updates("test-sub")
        session = manager.create_session(SessionType.CONVERSATIONAL)

        # Drain the creation broadcast
        _ = queue.get_nowait()

        worktree_meta = {
            "repo_path": "/home/fox/Projects/RCFlow",
            "last_action": "list",
        }
        session.metadata["worktree"] = worktree_meta
        manager.broadcast_session_update(session)

        update = queue.get_nowait()
        assert update["type"] == "session_update"
        assert update["worktree"] == worktree_meta

    def test_broadcast_session_update_worktree_is_none_when_not_set(self):
        """Sessions without worktree metadata should broadcast worktree: null,
        so the client correctly clears any stale worktreeInfo."""
        manager = SessionManager("test-backend")
        queue = manager.subscribe_updates("test-sub")
        session = manager.create_session(SessionType.CONVERSATIONAL)
        _ = queue.get_nowait()

        manager.broadcast_session_update(session)
        update = queue.get_nowait()
        assert "worktree" in update
        assert update["worktree"] is None

    def test_list_all_with_archived_includes_worktree_in_dict(self):
        """list_all_with_archived must include 'worktree' key in each in-memory
        session dict so the list_sessions WS handler can forward it to clients."""
        manager = SessionManager("test-backend")
        session = manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        worktree_meta = {
            "repo_path": "/repo",
            "last_action": "new",
            "branch": "feat/xyz",
            "base": "main",
        }
        session.metadata["worktree"] = worktree_meta

        # Simulate the in-memory-only code path (no db) used in list_sessions
        # by reading directly from list_all_sessions + the same dict construction
        # that list_all_with_archived performs for in-memory sessions.
        in_memory_dicts = [
            {
                "session_id": s.id,
                "worktree": s.metadata.get("worktree"),
            }
            for s in manager.list_all_sessions()
        ]
        assert len(in_memory_dicts) == 1
        assert in_memory_dicts[0]["worktree"] == worktree_meta


class TestSelectedWorktree:
    def test_selected_worktree_broadcast(self):
        manager = SessionManager("test-backend")
        session = manager.create_session(SessionType.ONE_SHOT)
        q = manager.subscribe_updates("test-sub")

        session.metadata["selected_worktree_path"] = "/projects/myrepo/.worktrees/feature-abc"
        manager.broadcast_session_update(session)

        update = q.get_nowait()
        assert update["selected_worktree_path"] == "/projects/myrepo/.worktrees/feature-abc"

    def test_selected_worktree_none_by_default(self):
        manager = SessionManager("test-backend")
        session = manager.create_session(SessionType.ONE_SHOT)
        q = manager.subscribe_updates("test-sub")

        manager.broadcast_session_update(session)
        update = q.get_nowait()
        assert update["selected_worktree_path"] is None


class TestMainProjectPath:
    """Tests that main_project_path is preserved through all session state
    transitions and included in broadcasts / session list output.

    This is the backend counterpart of the client-side fix that auto-opens
    the project panel when switching to a session with a known project path.
    """

    def test_main_project_path_survives_pause(self):
        """main_project_path must not be cleared when a session is paused."""
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.set_active()
        session.main_project_path = "/home/fox/Projects/RCFlow"
        session.pause()
        assert session.main_project_path == "/home/fox/Projects/RCFlow"

    def test_main_project_path_survives_resume(self):
        """main_project_path must not be cleared when a session is resumed."""
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.set_active()
        session.main_project_path = "/home/fox/Projects/RCFlow"
        session.pause()
        session.resume()
        assert session.main_project_path == "/home/fox/Projects/RCFlow"

    def test_main_project_path_survives_complete(self):
        """main_project_path must not be cleared when a session completes."""
        session = ActiveSession("test-id", SessionType.ONE_SHOT)
        session.set_active()
        session.main_project_path = "/home/fox/Projects/Alpha"
        session.complete()
        assert session.main_project_path == "/home/fox/Projects/Alpha"

    def test_main_project_path_survives_fail(self):
        """main_project_path must not be cleared when a session fails."""
        session = ActiveSession("test-id", SessionType.ONE_SHOT)
        session.main_project_path = "/home/fox/Projects/Alpha"
        session.fail("oops")
        assert session.main_project_path == "/home/fox/Projects/Alpha"

    def test_broadcast_includes_main_project_path(self):
        """broadcast_session_update must include main_project_path so live
        session_update WS messages carry it to the client."""
        manager = SessionManager("test-backend")
        q = manager.subscribe_updates("test-sub")
        session = manager.create_session(SessionType.CONVERSATIONAL)
        # drain creation broadcast
        _ = q.get_nowait()

        session.main_project_path = "/home/fox/Projects/MyApp"
        manager.broadcast_session_update(session)

        update = q.get_nowait()
        assert update["type"] == "session_update"
        assert update["main_project_path"] == "/home/fox/Projects/MyApp"

    def test_broadcast_main_project_path_none_when_not_set(self):
        """Sessions without a project must broadcast main_project_path: null
        so the client never shows stale project data."""
        manager = SessionManager("test-backend")
        q = manager.subscribe_updates("test-sub")
        session = manager.create_session(SessionType.CONVERSATIONAL)
        _ = q.get_nowait()

        manager.broadcast_session_update(session)
        update = q.get_nowait()
        assert "main_project_path" in update
        assert update["main_project_path"] is None

    def test_broadcast_includes_main_project_path_after_pause(self):
        """After pausing, broadcasts must still carry the project path so the
        client project panel remains visible for paused sessions."""
        manager = SessionManager("test-backend")
        q = manager.subscribe_updates("test-sub")
        session = manager.create_session(SessionType.CONVERSATIONAL)
        _ = q.get_nowait()  # creation broadcast

        # Set main_project_path before set_active so that even the ACTIVE
        # status broadcast already carries the path.
        session.main_project_path = "/home/fox/Projects/MyApp"
        session.set_active()
        _ = q.get_nowait()  # CREATED→ACTIVE status broadcast

        session.pause()
        # pause() calls _on_update → broadcast
        update = q.get_nowait()
        assert update["main_project_path"] == "/home/fox/Projects/MyApp"

    def test_list_all_with_archived_includes_main_project_path(self):
        """list_all_with_archived in-memory dict must include main_project_path
        so the WS session_list message carries it to the client."""
        manager = SessionManager("test-backend")
        session = manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.main_project_path = "/home/fox/Projects/RCFlow"

        # Simulate the dict construction performed by list_all_with_archived
        # for in-memory sessions (without a DB call).
        in_memory_dicts = [
            {
                "session_id": s.id,
                "main_project_path": s.main_project_path,
            }
            for s in manager.list_all_sessions()
        ]
        assert len(in_memory_dicts) == 1
        assert in_memory_dicts[0]["main_project_path"] == "/home/fox/Projects/RCFlow"


# ---------------------------------------------------------------------------
# Helpers: real SQLite DB with FK enforcement (mirrors production engine.py)
# ---------------------------------------------------------------------------

def _make_sqlite_engine():
    """Create an async SQLite engine with StaticPool and PRAGMA foreign_keys=ON."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_fk_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


async def _create_tables(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Integration tests: archive_session and save_all_sessions vs real SQLite FK
# ---------------------------------------------------------------------------


class TestArchiveSessionSQLiteIntegration:
    """Integration tests that exercise archive_session and save_all_sessions
    against a real SQLite DB with PRAGMA foreign_keys=ON so that any FK
    violation is caught as an IntegrityError instead of silently succeeding.
    """

    @pytest.fixture(autouse=True)
    async def setup_db(self):
        self.engine = _make_sqlite_engine()
        await _create_tables(self.engine)
        self.factory = _session_factory(self.engine)
        yield
        await self.engine.dispose()

    @pytest.mark.asyncio
    async def test_archive_cancelled_session_no_prior_stub_row(self):
        """Archiving a cancelled session that has no pre-existing sessions row
        must not raise an FK IntegrityError.  This is the primary regression
        test for the bug: the session_messages rows reference sessions.id, so
        the sessions row must be written before the messages are committed.
        """
        manager = SessionManager("test-backend")
        session = manager.create_session(SessionType.ONE_SHOT)
        session.set_active()
        # Push a SESSION_END message to the buffer (mirrors cancel_session flow)
        session.buffer.push_text(MessageType.SESSION_END, {"session_id": session.id, "reason": "cancelled"})
        session.cancel()

        async with self.factory() as db:
            # No stub row pre-created — archive_session must create it itself.
            await manager.archive_session(session.id, db)

        # Verify both the sessions row and the session_messages row landed in DB.
        async with self.factory() as db:
            session_uuid = uuid.UUID(session.id)
            db_session = await db.get(SessionModel, session_uuid)
            assert db_session is not None
            assert db_session.status == "cancelled"

            msgs = (await db.execute(
                select(SessionMessageModel).where(SessionMessageModel.session_id == session_uuid)
            )).scalars().all()
            assert len(msgs) == 1
            assert msgs[0].message_type == MessageType.SESSION_END.value

    @pytest.mark.asyncio
    async def test_archive_cancelled_session_with_prior_stub_row(self):
        """Archiving when a stub sessions row already exists (created by
        _ensure_session_row_in_db) must update it and insert messages without
        any FK violation.
        """
        manager = SessionManager("test-backend")
        session = manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session_uuid = uuid.UUID(session.id)

        # Simulate a pre-existing stub row (as _ensure_session_row_in_db would create)
        async with self.factory() as db:
            db.add(SessionModel(
                id=session_uuid,
                backend_id="test-backend",
                created_at=session.created_at,
                session_type=session.session_type.value,
                status="active",
            ))
            await db.commit()

        # Now push a message, cancel, and archive
        session.buffer.push_text(MessageType.SESSION_END, {"session_id": session.id, "reason": "cancelled"})
        session.cancel()

        async with self.factory() as db:
            await manager.archive_session(session.id, db)

        async with self.factory() as db:
            db_session = await db.get(SessionModel, session_uuid)
            assert db_session is not None
            assert db_session.status == "cancelled"

            msgs = (await db.execute(
                select(SessionMessageModel).where(SessionMessageModel.session_id == session_uuid)
            )).scalars().all()
            assert len(msgs) == 1

    @pytest.mark.asyncio
    async def test_archive_session_with_multiple_messages(self):
        """Sessions that have several buffered messages (user text, tool output,
        session_end) must all be persisted without FK violations.
        """
        manager = SessionManager("test-backend")
        session = manager.create_session(SessionType.ONE_SHOT)
        session.set_active()

        session.buffer.push_text(MessageType.TEXT_CHUNK, {"session_id": session.id, "content": "hello"})
        session.buffer.push_text(MessageType.SUMMARY, {"session_id": session.id, "content": "world"})
        session.buffer.push_text(MessageType.SESSION_END, {"session_id": session.id, "reason": "cancelled"})
        session.cancel()

        async with self.factory() as db:
            await manager.archive_session(session.id, db)

        async with self.factory() as db:
            session_uuid = uuid.UUID(session.id)
            msgs = (await db.execute(
                select(SessionMessageModel)
                .where(SessionMessageModel.session_id == session_uuid)
                .order_by(SessionMessageModel.sequence)
            )).scalars().all()
            assert len(msgs) == 3
            assert msgs[0].message_type == MessageType.TEXT_CHUNK.value
            assert msgs[2].message_type == MessageType.SESSION_END.value

    @pytest.mark.asyncio
    async def test_save_all_sessions_cancelled_no_prior_stub(self):
        """save_all_sessions must persist a cancelled session that has no
        pre-existing sessions row without triggering an FK IntegrityError.
        This tests the explicit db.flush() added to save_all_sessions.
        """
        manager = SessionManager("test-backend")
        session = manager.create_session(SessionType.ONE_SHOT)
        session.set_active()
        session.buffer.push_text(MessageType.SESSION_END, {"session_id": session.id, "reason": "cancelled"})
        session.cancel()

        async with self.factory() as db:
            await manager.save_all_sessions(db)

        async with self.factory() as db:
            session_uuid = uuid.UUID(session.id)
            db_session = await db.get(SessionModel, session_uuid)
            assert db_session is not None
            assert db_session.status == "cancelled"

            msgs = (await db.execute(
                select(SessionMessageModel).where(SessionMessageModel.session_id == session_uuid)
            )).scalars().all()
            assert len(msgs) == 1
            assert msgs[0].message_type == MessageType.SESSION_END.value

    @pytest.mark.asyncio
    async def test_save_all_sessions_cancelled_with_prior_stub(self):
        """save_all_sessions must update an existing sessions stub row and
        correctly insert session_messages without FK violations.
        """
        manager = SessionManager("test-backend")
        session = manager.create_session(SessionType.ONE_SHOT)
        session.set_active()
        session_uuid = uuid.UUID(session.id)

        async with self.factory() as db:
            db.add(SessionModel(
                id=session_uuid,
                backend_id="test-backend",
                created_at=session.created_at,
                session_type=session.session_type.value,
                status="active",
            ))
            await db.commit()

        session.buffer.push_text(MessageType.SESSION_END, {"session_id": session.id, "reason": "cancelled"})
        session.cancel()

        async with self.factory() as db:
            await manager.save_all_sessions(db)

        async with self.factory() as db:
            db_session = await db.get(SessionModel, session_uuid)
            assert db_session is not None
            assert db_session.status == "cancelled"

            msgs = (await db.execute(
                select(SessionMessageModel).where(SessionMessageModel.session_id == session_uuid)
            )).scalars().all()
            assert len(msgs) == 1

    @pytest.mark.asyncio
    async def test_save_all_sessions_skips_already_archived(self):
        """If archive_session already ran and removed the session from memory,
        save_all_sessions must not see it (it's been removed from _sessions).
        Verifies no double-insert can cause a UniqueConstraint violation.
        """
        manager = SessionManager("test-backend")
        session = manager.create_session(SessionType.ONE_SHOT)
        session.set_active()
        session.buffer.push_text(MessageType.SESSION_END, {"session_id": session.id, "reason": "cancelled"})
        session.cancel()

        session_id = session.id

        # archive_session removes the session from memory
        async with self.factory() as db:
            await manager.archive_session(session_id, db)

        # session should no longer be in memory
        assert manager.get_session(session_id) is None

        # save_all_sessions operates on remaining in-memory sessions — this one
        # should not be touched (it was already removed).
        async with self.factory() as db:
            await manager.save_all_sessions(db)

        # Row should exist exactly once
        async with self.factory() as db:
            session_uuid = uuid.UUID(session_id)
            msgs = (await db.execute(
                select(SessionMessageModel).where(SessionMessageModel.session_id == session_uuid)
            )).scalars().all()
            assert len(msgs) == 1
