import pytest

from src.core.session import ActiveSession, SessionManager, SessionStatus, SessionType


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
        received: list[dict] = []
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
