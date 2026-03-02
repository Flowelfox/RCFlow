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
