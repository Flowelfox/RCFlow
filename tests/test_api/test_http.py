import time
import uuid as _uuid_mod
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine as _sync_create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session as _OrmSession

from src.core.buffer import MessageType
from src.core.session import SessionManager, SessionType
from src.models.db import Base
from src.models.db import Session as _DbSession

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


# ---------------------------------------------------------------------------
# Fixtures — in-process SQLite DB for draft endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
def draft_db(tmp_path):
    """Temp SQLite DB with full schema and one pre-inserted session row.

    Returns (db_file: Path, session_id: uuid.UUID).
    """
    db_file = tmp_path / "drafts.db"
    sync_engine = _sync_create_engine(f"sqlite:///{db_file}")
    Base.metadata.create_all(sync_engine)
    session_id = _uuid_mod.uuid4()
    with _OrmSession(sync_engine) as sess:
        sess.add(
            _DbSession(
                id=session_id,
                backend_id="test-backend",
                created_at=datetime.now(UTC),
                session_type="conversational",
                status="active",
                metadata_={},
            )
        )
        sess.commit()
    sync_engine.dispose()
    return db_file, session_id


@pytest.fixture
def db_client(test_app: FastAPI, draft_db) -> TestClient:
    """TestClient with a real SQLite db_session_factory wired up."""
    db_file, _ = draft_db
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}",
        connect_args={"check_same_thread": False},
    )
    test_app.state.db_session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield TestClient(test_app)
    test_app.state.db_session_factory = None


# ---------------------------------------------------------------------------
# Draft endpoint tests
# ---------------------------------------------------------------------------


class TestDraft:
    # --- GET (no draft yet) ---

    def test_get_returns_empty_when_no_draft(self, db_client: TestClient, draft_db) -> None:
        _, session_id = draft_db
        resp = db_client.get(f"/api/sessions/{session_id}/draft", headers=_auth_headers())
        assert resp.status_code == 200
        body = resp.json()
        assert body["content"] == ""
        assert "updated_at" in body

    # --- PUT (create) ---

    def test_put_returns_204(self, db_client: TestClient, draft_db) -> None:
        _, session_id = draft_db
        resp = db_client.put(
            f"/api/sessions/{session_id}/draft",
            json={"content": "hello draft"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 204
        assert resp.content == b""

    def test_put_then_get_returns_saved_content(self, db_client: TestClient, draft_db) -> None:
        _, session_id = draft_db
        db_client.put(
            f"/api/sessions/{session_id}/draft",
            json={"content": "my unsent message"},
            headers=_auth_headers(),
        )
        resp = db_client.get(f"/api/sessions/{session_id}/draft", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json()["content"] == "my unsent message"

    # --- PUT (update) ---

    def test_put_updates_existing_draft(self, db_client: TestClient, draft_db) -> None:
        _, session_id = draft_db
        db_client.put(
            f"/api/sessions/{session_id}/draft",
            json={"content": "first version"},
            headers=_auth_headers(),
        )
        db_client.put(
            f"/api/sessions/{session_id}/draft",
            json={"content": "second version"},
            headers=_auth_headers(),
        )
        resp = db_client.get(f"/api/sessions/{session_id}/draft", headers=_auth_headers())
        assert resp.json()["content"] == "second version"

    def test_put_empty_string_clears_draft(self, db_client: TestClient, draft_db) -> None:
        _, session_id = draft_db
        db_client.put(
            f"/api/sessions/{session_id}/draft",
            json={"content": "something"},
            headers=_auth_headers(),
        )
        db_client.put(
            f"/api/sessions/{session_id}/draft",
            json={"content": ""},
            headers=_auth_headers(),
        )
        resp = db_client.get(f"/api/sessions/{session_id}/draft", headers=_auth_headers())
        assert resp.json()["content"] == ""

    def test_updated_at_advances_on_second_put(self, db_client: TestClient, draft_db) -> None:
        _, session_id = draft_db
        db_client.put(
            f"/api/sessions/{session_id}/draft",
            json={"content": "v1"},
            headers=_auth_headers(),
        )
        ts1 = db_client.get(f"/api/sessions/{session_id}/draft", headers=_auth_headers()).json()["updated_at"]

        time.sleep(0.01)

        db_client.put(
            f"/api/sessions/{session_id}/draft",
            json={"content": "v2"},
            headers=_auth_headers(),
        )
        ts2 = db_client.get(f"/api/sessions/{session_id}/draft", headers=_auth_headers()).json()["updated_at"]

        assert ts2 > ts1

    def test_drafts_isolated_per_session(self, db_client: TestClient, draft_db) -> None:
        """A draft for session A must not appear for session B."""
        db_file, session_a = draft_db
        # Insert second session into the same DB.
        session_b = _uuid_mod.uuid4()
        sync_engine = _sync_create_engine(f"sqlite:///{db_file}")
        with _OrmSession(sync_engine) as sess:
            sess.add(
                _DbSession(
                    id=session_b,
                    backend_id="test-backend",
                    created_at=datetime.now(UTC),
                    session_type="conversational",
                    status="active",
                    metadata_={},
                )
            )
            sess.commit()
        sync_engine.dispose()

        db_client.put(
            f"/api/sessions/{session_a}/draft",
            json={"content": "only for A"},
            headers=_auth_headers(),
        )

        resp_b = db_client.get(f"/api/sessions/{session_b}/draft", headers=_auth_headers())
        assert resp_b.json()["content"] == ""

    # --- Error paths ---

    def test_put_invalid_session_id_returns_400(self, db_client: TestClient) -> None:
        resp = db_client.put(
            "/api/sessions/not-a-uuid/draft",
            json={"content": "x"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 400

    def test_get_invalid_session_id_returns_400(self, db_client: TestClient) -> None:
        resp = db_client.get("/api/sessions/not-a-uuid/draft", headers=_auth_headers())
        assert resp.status_code == 400

    def test_put_unknown_session_returns_404(self, db_client: TestClient) -> None:
        unknown = _uuid_mod.uuid4()
        resp = db_client.put(
            f"/api/sessions/{unknown}/draft",
            json={"content": "x"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 404

    def test_get_unknown_session_returns_empty_not_404(self, db_client: TestClient) -> None:
        """GET never returns 404 — unknown session UUID returns empty content."""
        unknown = _uuid_mod.uuid4()
        resp = db_client.get(f"/api/sessions/{unknown}/draft", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json()["content"] == ""

    def test_put_requires_auth(self, db_client: TestClient, draft_db) -> None:
        _, session_id = draft_db
        resp = db_client.put(f"/api/sessions/{session_id}/draft", json={"content": "x"})
        assert resp.status_code in (401, 403, 422)

    def test_get_requires_auth(self, db_client: TestClient, draft_db) -> None:
        _, session_id = draft_db
        resp = db_client.get(f"/api/sessions/{session_id}/draft")
        assert resp.status_code in (401, 403, 422)

    def test_put_no_db_returns_503(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.ONE_SHOT)
        resp = client.put(
            f"/api/sessions/{session.id}/draft",
            json={"content": "x"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 503

    def test_get_no_db_returns_503(self, client: TestClient, session_manager: SessionManager) -> None:
        session = session_manager.create_session(SessionType.ONE_SHOT)
        resp = client.get(
            f"/api/sessions/{session.id}/draft",
            headers=_auth_headers(),
        )
        assert resp.status_code == 503
