from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.buffer import MessageType
from src.core.session import SessionManager, SessionType

API_KEY = "test-api-key"


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


def _auth_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


class TestCancelSession:
    def test_cancel_active_session(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.ONE_SHOT)
        session.set_active()

        resp = client.post(f"/api/sessions/{session.id}/cancel", headers=_auth_headers())

        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["session_id"] == session.id
        assert body["status"] == "cancelled"
        assert body["cancelled_at"] is not None

    def test_cancel_unknown_session(self, client: TestClient) -> None:
        resp = client.post("/api/sessions/nonexistent-id/cancel", headers=_auth_headers())
        assert resp.status_code == 404

    def test_cancel_completed_session(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.ONE_SHOT)
        session.complete()

        resp = client.post(f"/api/sessions/{session.id}/cancel", headers=_auth_headers())
        assert resp.status_code == 409

    def test_cancel_requires_auth(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.ONE_SHOT)
        session.set_active()

        # No API key header
        resp = client.post(f"/api/sessions/{session.id}/cancel")
        assert resp.status_code in (401, 403, 422)


class TestListSessionsTitle:
    def test_title_null_by_default(self, client: TestClient, session_manager: SessionManager) -> None:
        session_manager.create_session(SessionType.CONVERSATIONAL)

        resp = client.get("/api/sessions", headers=_auth_headers())
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["title"] is None

    def test_title_included_when_set(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.title = "List project files"

        resp = client.get("/api/sessions", headers=_auth_headers())
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["title"] == "List project files"


class TestRenameSession:
    def test_rename_active_session(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)

        resp = client.patch(
            f"/api/sessions/{session.id}/title",
            json={"title": "My new title"},
            headers=_auth_headers(),
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == session.id
        assert body["title"] == "My new title"
        # Verify in-memory update
        assert session.title == "My new title"

    def test_clear_title(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.title = "Old title"

        resp = client.patch(
            f"/api/sessions/{session.id}/title",
            json={"title": None},
            headers=_auth_headers(),
        )

        assert resp.status_code == 200
        assert resp.json()["title"] is None
        assert session.title is None

    def test_empty_string_clears_title(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.title = "Old title"

        resp = client.patch(
            f"/api/sessions/{session.id}/title",
            json={"title": "  "},
            headers=_auth_headers(),
        )

        assert resp.status_code == 200
        assert resp.json()["title"] is None
        assert session.title is None

    def test_title_too_long(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)

        resp = client.patch(
            f"/api/sessions/{session.id}/title",
            json={"title": "x" * 201},
            headers=_auth_headers(),
        )

        assert resp.status_code == 422

    def test_rename_unknown_session(self, client: TestClient) -> None:
        resp = client.patch(
            "/api/sessions/nonexistent-id/title",
            json={"title": "hello"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 404

    def test_rename_requires_auth(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)

        resp = client.patch(
            f"/api/sessions/{session.id}/title",
            json={"title": "hello"},
        )
        assert resp.status_code in (401, 403, 422)


class TestPauseSession:
    def test_pause_active_session(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()

        resp = client.post(f"/api/sessions/{session.id}/pause", headers=_auth_headers())

        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["session_id"] == session.id
        assert body["status"] == "paused"
        assert body["paused_at"] is not None

    def test_pause_unknown_session(self, client: TestClient) -> None:
        resp = client.post("/api/sessions/nonexistent-id/pause", headers=_auth_headers())
        assert resp.status_code == 404

    def test_pause_completed_session(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.ONE_SHOT)
        session.complete()

        resp = client.post(f"/api/sessions/{session.id}/pause", headers=_auth_headers())
        assert resp.status_code == 409

    def test_pause_already_paused(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()

        resp = client.post(f"/api/sessions/{session.id}/pause", headers=_auth_headers())
        assert resp.status_code == 409

    def test_pause_requires_auth(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()

        resp = client.post(f"/api/sessions/{session.id}/pause")
        assert resp.status_code in (401, 403, 422)


class TestResumeSession:
    def test_resume_paused_session(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()

        resp = client.post(f"/api/sessions/{session.id}/resume", headers=_auth_headers())

        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["session_id"] == session.id
        assert body["status"] == "active"

    def test_resume_unknown_session(self, client: TestClient) -> None:
        resp = client.post("/api/sessions/nonexistent-id/resume", headers=_auth_headers())
        assert resp.status_code == 404

    def test_resume_active_session(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()

        resp = client.post(f"/api/sessions/{session.id}/resume", headers=_auth_headers())
        assert resp.status_code == 409

    def test_resume_requires_auth(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()

        resp = client.post(f"/api/sessions/{session.id}/resume")
        assert resp.status_code in (401, 403, 422)


class TestInterruptSubprocess:
    def test_interrupt_active_session(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.LONG_RUNNING)
        session.set_active()

        resp = client.post(f"/api/sessions/{session.id}/interrupt", headers=_auth_headers())

        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["session_id"] == session.id
        assert body["status"] == "active"

    def test_interrupt_unknown_session(self, client: TestClient) -> None:
        resp = client.post("/api/sessions/nonexistent-id/interrupt", headers=_auth_headers())
        assert resp.status_code == 404

    def test_interrupt_paused_session(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()

        resp = client.post(f"/api/sessions/{session.id}/interrupt", headers=_auth_headers())
        assert resp.status_code == 409

    def test_interrupt_terminal_session(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.ONE_SHOT)
        session.complete()

        resp = client.post(f"/api/sessions/{session.id}/interrupt", headers=_auth_headers())
        assert resp.status_code == 409

    def test_interrupt_requires_auth(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.LONG_RUNNING)
        session.set_active()

        resp = client.post(f"/api/sessions/{session.id}/interrupt")
        assert resp.status_code in (401, 403, 422)


def _populate_session_messages(session_manager: SessionManager, n: int) -> str:
    """Create a session with `n` text_chunk messages, returning the session ID."""
    session = session_manager.create_session(SessionType.CONVERSATIONAL)
    for i in range(n):
        session.buffer.push_text(
            MessageType.TEXT_CHUNK,
            {"content": f"message-{i}", "role": "assistant"},
        )
    return session.id


class TestGetSessionMessagesPagination:
    def test_no_params_returns_all_with_pagination_metadata(
        self, client: TestClient, session_manager: SessionManager
    ) -> None:
        sid = _populate_session_messages(session_manager, 5)
        resp = client.get(f"/api/sessions/{sid}/messages", headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["messages"]) == 5
        pagination = body["pagination"]
        assert pagination["total_count"] == 5
        assert pagination["has_more"] is False
        assert pagination["next_cursor"] is None

    def test_limit_returns_most_recent(self, client: TestClient, session_manager: SessionManager) -> None:
        sid = _populate_session_messages(session_manager, 10)
        resp = client.get(f"/api/sessions/{sid}/messages?limit=3", headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        msgs = body["messages"]
        assert len(msgs) == 3
        # Should be the last 3 messages (sequences 8, 9, 10)
        assert msgs[0]["sequence"] == 8
        assert msgs[1]["sequence"] == 9
        assert msgs[2]["sequence"] == 10
        pagination = body["pagination"]
        assert pagination["total_count"] == 10
        assert pagination["has_more"] is True
        assert pagination["next_cursor"] == 8

    def test_before_and_limit(self, client: TestClient, session_manager: SessionManager) -> None:
        sid = _populate_session_messages(session_manager, 10)
        # Get messages before sequence 8, limit 3 -> should be 5, 6, 7
        resp = client.get(
            f"/api/sessions/{sid}/messages?before=8&limit=3",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        body = resp.json()
        msgs = body["messages"]
        assert len(msgs) == 3
        assert msgs[0]["sequence"] == 5
        assert msgs[1]["sequence"] == 6
        assert msgs[2]["sequence"] == 7
        pagination = body["pagination"]
        assert pagination["has_more"] is True
        assert pagination["next_cursor"] == 5

    def test_before_exhausts_remaining(self, client: TestClient, session_manager: SessionManager) -> None:
        sid = _populate_session_messages(session_manager, 5)
        # Get messages before sequence 3, limit 10 -> should be 1, 2
        resp = client.get(
            f"/api/sessions/{sid}/messages?before=3&limit=10",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        body = resp.json()
        msgs = body["messages"]
        assert len(msgs) == 2
        assert msgs[0]["sequence"] == 1
        assert msgs[1]["sequence"] == 2
        pagination = body["pagination"]
        assert pagination["has_more"] is False
        assert pagination["next_cursor"] is None

    def test_limit_equal_to_total_has_more_false(self, client: TestClient, session_manager: SessionManager) -> None:
        sid = _populate_session_messages(session_manager, 3)
        resp = client.get(f"/api/sessions/{sid}/messages?limit=3", headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["messages"]) == 3
        assert body["pagination"]["has_more"] is False
        assert body["pagination"]["next_cursor"] is None

    def test_limit_greater_than_total(self, client: TestClient, session_manager: SessionManager) -> None:
        sid = _populate_session_messages(session_manager, 2)
        resp = client.get(f"/api/sessions/{sid}/messages?limit=50", headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["messages"]) == 2
        assert body["pagination"]["has_more"] is False

    def test_empty_session(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        resp = client.get(
            f"/api/sessions/{session.id}/messages?limit=10",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["messages"] == []
        assert body["pagination"]["total_count"] == 0
        assert body["pagination"]["has_more"] is False

    def test_messages_in_chronological_order(self, client: TestClient, session_manager: SessionManager) -> None:
        sid = _populate_session_messages(session_manager, 10)
        resp = client.get(f"/api/sessions/{sid}/messages?limit=5", headers=_auth_headers())
        assert resp.status_code == 200
        seqs = [m["sequence"] for m in resp.json()["messages"]]
        assert seqs == sorted(seqs)


class TestSetSessionWorktree:
    def test_set_worktree_path(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)

        resp = client.patch(
            f"/api/sessions/{session.id}/worktree",
            json={"path": "/projects/myrepo/.worktrees/feature-abc"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["session_id"] == session.id
        assert body["selected_worktree_path"] == "/projects/myrepo/.worktrees/feature-abc"
        assert session.metadata["selected_worktree_path"] == "/projects/myrepo/.worktrees/feature-abc"

    def test_clear_worktree_path(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.metadata["selected_worktree_path"] = "/some/path"

        resp = client.patch(
            f"/api/sessions/{session.id}/worktree",
            json={"path": None},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["selected_worktree_path"] is None
        assert "selected_worktree_path" not in session.metadata

    def test_set_worktree_unknown_session(self, client: TestClient) -> None:
        resp = client.patch(
            "/api/sessions/nonexistent-id/worktree",
            json={"path": "/some/path"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 404

    def test_set_worktree_requires_auth(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        resp = client.patch(
            f"/api/sessions/{session.id}/worktree",
            json={"path": "/some/path"},
        )
        assert resp.status_code in (401, 403, 422)
