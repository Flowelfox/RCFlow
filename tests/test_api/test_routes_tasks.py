"""Tests for the task CRUD + session-linking HTTP routes (`src/api/routes/tasks.py`).

Covers list/get/create/update/delete tasks and attach/detach sessions, plus the
status-transition validator and serialisation helpers. Uses an in-memory SQLite
DB wired onto ``app.state.db_session_factory``.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.api.routes.tasks import (
    VALID_TASK_TRANSITIONS,
    validate_status_transition,
)
from src.database.models import Base
from src.database.models import Session as SessionModel
from src.database.models import Task as TaskModel

if TYPE_CHECKING:
    from fastapi import FastAPI

API_KEY = "test-api-key"
BACKEND_ID = "test-backend"


def _auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


@pytest.fixture
async def db_factory():
    """In-memory SQLite session factory with all tables created."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
def client(test_app: FastAPI, db_factory) -> TestClient:
    # The settings backend id must match what the routes filter on.
    test_app.state.settings.RCFLOW_BACKEND_ID = BACKEND_ID
    test_app.state.db_session_factory = db_factory
    return TestClient(test_app)


@pytest.fixture
def no_db_client(test_app: FastAPI) -> TestClient:
    test_app.state.db_session_factory = None
    return TestClient(test_app)


# ── Helpers to seed rows directly ────────────────────────────────────────


async def _seed_task(
    factory,
    *,
    title: str = "A task",
    status: str = "todo",
    source: str = "user",
    backend_id: str = BACKEND_ID,
) -> str:
    now = datetime.now(UTC)
    task = TaskModel(
        backend_id=backend_id,
        title=title,
        description="desc",
        status=status,
        source=source,
        created_at=now,
        updated_at=now,
    )
    async with factory() as db:
        db.add(task)
        await db.commit()
        return str(task.id)


async def _seed_session(factory, *, backend_id: str = BACKEND_ID) -> str:
    sess_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with factory() as db:
        db.add(
            SessionModel(
                id=sess_id,
                backend_id=backend_id,
                created_at=now,
                ended_at=None,
                session_type="conversational",
                status="active",
                title="A session",
                metadata_={},
            )
        )
        await db.commit()
    return str(sess_id)


# ── validate_status_transition ───────────────────────────────────────────


class TestStatusTransitions:
    def test_same_status_is_noop(self) -> None:
        validate_status_transition("todo", "todo")  # no raise

    @pytest.mark.parametrize(
        ("current", "new"),
        [(c, n) for c, allowed in VALID_TASK_TRANSITIONS.items() for n in allowed],
    )
    def test_valid_transitions_allowed(self, current: str, new: str) -> None:
        validate_status_transition(current, new)  # no raise

    def test_invalid_transition_raises_409(self) -> None:
        with pytest.raises(HTTPException) as exc:
            validate_status_transition("todo", "review")
        assert exc.value.status_code == 409

    def test_unknown_current_raises_409(self) -> None:
        with pytest.raises(HTTPException) as exc:
            validate_status_transition("bogus", "todo")
        assert exc.value.status_code == 409

    def test_ai_cannot_set_done(self) -> None:
        with pytest.raises(HTTPException) as exc:
            validate_status_transition("in_progress", "done", source="ai")
        assert exc.value.status_code == 409
        assert "AI agents" in exc.value.detail


# ── GET /api/tasks ────────────────────────────────────────────────────────


class TestListTasks:
    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/tasks")
        assert resp.status_code in (401, 403)

    def test_empty_when_no_db(self, no_db_client: TestClient) -> None:
        resp = no_db_client.get("/api/tasks", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"tasks": []}

    def test_lists_seeded_tasks(self, client: TestClient, db_factory) -> None:
        asyncio.get_event_loop().run_until_complete(_seed_task(db_factory, title="T1"))
        resp = client.get("/api/tasks", headers=_auth())
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["title"] == "T1"
        assert tasks[0]["sessions"] == []

    def test_status_filter(self, client: TestClient, db_factory) -> None:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_task(db_factory, title="todo-task", status="todo"))
        loop.run_until_complete(_seed_task(db_factory, title="prog-task", status="in_progress"))

        resp = client.get("/api/tasks", params={"status": "in_progress"}, headers=_auth())
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["title"] == "prog-task"

    def test_source_filter(self, client: TestClient, db_factory) -> None:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(_seed_task(db_factory, title="ai-task", source="ai"))
        loop.run_until_complete(_seed_task(db_factory, title="user-task", source="user"))

        resp = client.get("/api/tasks", params={"source": "ai"}, headers=_auth())
        assert resp.status_code == 200
        tasks = resp.json()["tasks"]
        assert len(tasks) == 1
        assert tasks[0]["title"] == "ai-task"

    def test_other_backend_excluded(self, client: TestClient, db_factory) -> None:
        asyncio.get_event_loop().run_until_complete(
            _seed_task(db_factory, title="elsewhere", backend_id="other-backend")
        )
        resp = client.get("/api/tasks", headers=_auth())
        assert resp.json()["tasks"] == []


# ── GET /api/tasks/{id} ───────────────────────────────────────────────────


class TestGetTask:
    def test_no_db_returns_404(self, no_db_client: TestClient) -> None:
        resp = no_db_client.get(f"/api/tasks/{uuid.uuid4()}", headers=_auth())
        assert resp.status_code == 404

    def test_invalid_uuid_returns_400(self, client: TestClient) -> None:
        resp = client.get("/api/tasks/not-a-uuid", headers=_auth())
        assert resp.status_code == 400

    def test_missing_returns_404(self, client: TestClient) -> None:
        resp = client.get(f"/api/tasks/{uuid.uuid4()}", headers=_auth())
        assert resp.status_code == 404

    def test_get_existing(self, client: TestClient, db_factory) -> None:
        tid = asyncio.get_event_loop().run_until_complete(_seed_task(db_factory, title="Findme"))
        resp = client.get(f"/api/tasks/{tid}", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == tid
        assert body["title"] == "Findme"
        assert body["plan_artifact_id"] is None


# ── POST /api/tasks ───────────────────────────────────────────────────────


class TestCreateTask:
    def test_no_db_returns_500(self, no_db_client: TestClient) -> None:
        resp = no_db_client.post("/api/tasks", json={"title": "x"}, headers=_auth())
        assert resp.status_code == 500

    def test_validation_missing_title(self, client: TestClient) -> None:
        resp = client.post("/api/tasks", json={}, headers=_auth())
        assert resp.status_code == 422

    def test_create_minimal(self, client: TestClient) -> None:
        resp = client.post("/api/tasks", json={"title": "New task"}, headers=_auth())
        assert resp.status_code == 201
        body = resp.json()
        assert body["title"] == "New task"
        assert body["status"] == "todo"
        assert body["source"] == "user"
        assert body["sessions"] == []

    def test_create_with_invalid_session_id(self, client: TestClient) -> None:
        resp = client.post(
            "/api/tasks",
            json={"title": "T", "session_id": "bad-uuid"},
            headers=_auth(),
        )
        assert resp.status_code == 400

    def test_create_with_unknown_session_id_404(self, client: TestClient) -> None:
        resp = client.post(
            "/api/tasks",
            json={"title": "T", "session_id": str(uuid.uuid4())},
            headers=_auth(),
        )
        assert resp.status_code == 404

    def test_create_with_existing_session(self, client: TestClient, db_factory) -> None:
        sid = asyncio.get_event_loop().run_until_complete(_seed_session(db_factory))
        resp = client.post(
            "/api/tasks",
            json={"title": "Linked", "session_id": sid},
            headers=_auth(),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert len(body["sessions"]) == 1
        assert body["sessions"][0]["session_id"] == sid


# ── PATCH /api/tasks/{id} ─────────────────────────────────────────────────


class TestUpdateTask:
    def test_no_db_returns_500(self, no_db_client: TestClient) -> None:
        resp = no_db_client.patch(f"/api/tasks/{uuid.uuid4()}", json={"title": "x"}, headers=_auth())
        assert resp.status_code == 500

    def test_invalid_uuid_400(self, client: TestClient) -> None:
        resp = client.patch("/api/tasks/nope", json={"title": "x"}, headers=_auth())
        assert resp.status_code == 400

    def test_missing_404(self, client: TestClient) -> None:
        resp = client.patch(f"/api/tasks/{uuid.uuid4()}", json={"title": "x"}, headers=_auth())
        assert resp.status_code == 404

    def test_update_title_and_description(self, client: TestClient, db_factory) -> None:
        tid = asyncio.get_event_loop().run_until_complete(_seed_task(db_factory))
        resp = client.patch(
            f"/api/tasks/{tid}",
            json={"title": "Renamed", "description": "newdesc"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Renamed"
        assert body["description"] == "newdesc"

    def test_valid_status_transition(self, client: TestClient, db_factory) -> None:
        tid = asyncio.get_event_loop().run_until_complete(_seed_task(db_factory, status="todo"))
        resp = client.patch(f"/api/tasks/{tid}", json={"status": "in_progress"}, headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["status"] == "in_progress"

    def test_invalid_status_transition_409(self, client: TestClient, db_factory) -> None:
        tid = asyncio.get_event_loop().run_until_complete(_seed_task(db_factory, status="todo"))
        resp = client.patch(f"/api/tasks/{tid}", json={"status": "review"}, headers=_auth())
        assert resp.status_code == 409

    def test_no_change_still_200(self, client: TestClient, db_factory) -> None:
        tid = asyncio.get_event_loop().run_until_complete(_seed_task(db_factory, title="Same"))
        resp = client.patch(f"/api/tasks/{tid}", json={"title": "Same"}, headers=_auth())
        assert resp.status_code == 200

    def test_invalid_plan_artifact_id_400(self, client: TestClient, db_factory) -> None:
        tid = asyncio.get_event_loop().run_until_complete(_seed_task(db_factory))
        resp = client.patch(
            f"/api/tasks/{tid}",
            json={"plan_artifact_id": "not-a-uuid"},
            headers=_auth(),
        )
        assert resp.status_code == 400

    def test_clear_plan_artifact_id(self, client: TestClient, db_factory) -> None:
        tid = asyncio.get_event_loop().run_until_complete(_seed_task(db_factory))
        resp = client.patch(
            f"/api/tasks/{tid}",
            json={"plan_artifact_id": None},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert resp.json()["plan_artifact_id"] is None


# ── DELETE /api/tasks/{id} ────────────────────────────────────────────────


class TestDeleteTask:
    def test_no_db_returns_500(self, no_db_client: TestClient) -> None:
        resp = no_db_client.delete(f"/api/tasks/{uuid.uuid4()}", headers=_auth())
        assert resp.status_code == 500

    def test_invalid_uuid_400(self, client: TestClient) -> None:
        resp = client.delete("/api/tasks/nope", headers=_auth())
        assert resp.status_code == 400

    def test_missing_404(self, client: TestClient) -> None:
        resp = client.delete(f"/api/tasks/{uuid.uuid4()}", headers=_auth())
        assert resp.status_code == 404

    def test_delete_existing(self, client: TestClient, db_factory) -> None:
        tid = asyncio.get_event_loop().run_until_complete(_seed_task(db_factory))
        resp = client.delete(f"/api/tasks/{tid}", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["deleted"] == "ok"
        # gone now
        assert client.get(f"/api/tasks/{tid}", headers=_auth()).status_code == 404


# ── POST /api/tasks/{id}/sessions ─────────────────────────────────────────


class TestAttachSession:
    def test_no_db_returns_500(self, no_db_client: TestClient) -> None:
        resp = no_db_client.post(
            f"/api/tasks/{uuid.uuid4()}/sessions",
            json={"session_id": str(uuid.uuid4())},
            headers=_auth(),
        )
        assert resp.status_code == 500

    def test_invalid_task_uuid_400(self, client: TestClient) -> None:
        resp = client.post(
            "/api/tasks/nope/sessions",
            json={"session_id": str(uuid.uuid4())},
            headers=_auth(),
        )
        assert resp.status_code == 400

    def test_invalid_session_uuid_400(self, client: TestClient, db_factory) -> None:
        tid = asyncio.get_event_loop().run_until_complete(_seed_task(db_factory))
        resp = client.post(
            f"/api/tasks/{tid}/sessions",
            json={"session_id": "bad"},
            headers=_auth(),
        )
        assert resp.status_code == 400

    def test_missing_task_404(self, client: TestClient, db_factory) -> None:
        sid = asyncio.get_event_loop().run_until_complete(_seed_session(db_factory))
        resp = client.post(
            f"/api/tasks/{uuid.uuid4()}/sessions",
            json={"session_id": sid},
            headers=_auth(),
        )
        assert resp.status_code == 404

    def test_unknown_session_404(self, client: TestClient, db_factory) -> None:
        tid = asyncio.get_event_loop().run_until_complete(_seed_task(db_factory))
        resp = client.post(
            f"/api/tasks/{tid}/sessions",
            json={"session_id": str(uuid.uuid4())},
            headers=_auth(),
        )
        assert resp.status_code == 404

    def test_attach_existing_session(self, client: TestClient, db_factory) -> None:
        loop = asyncio.get_event_loop()
        tid = loop.run_until_complete(_seed_task(db_factory))
        sid = loop.run_until_complete(_seed_session(db_factory))
        resp = client.post(
            f"/api/tasks/{tid}/sessions",
            json={"session_id": sid},
            headers=_auth(),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert len(body["sessions"]) == 1
        assert body["sessions"][0]["session_id"] == sid

    def test_attach_duplicate_409(self, client: TestClient, db_factory) -> None:
        loop = asyncio.get_event_loop()
        tid = loop.run_until_complete(_seed_task(db_factory))
        sid = loop.run_until_complete(_seed_session(db_factory))
        first = client.post(f"/api/tasks/{tid}/sessions", json={"session_id": sid}, headers=_auth())
        assert first.status_code == 201
        second = client.post(f"/api/tasks/{tid}/sessions", json={"session_id": sid}, headers=_auth())
        assert second.status_code == 409


# ── DELETE /api/tasks/{id}/sessions/{sid} ─────────────────────────────────


class TestDetachSession:
    def test_no_db_returns_500(self, no_db_client: TestClient) -> None:
        resp = no_db_client.delete(
            f"/api/tasks/{uuid.uuid4()}/sessions/{uuid.uuid4()}",
            headers=_auth(),
        )
        assert resp.status_code == 500

    def test_invalid_task_uuid_400(self, client: TestClient) -> None:
        resp = client.delete(f"/api/tasks/nope/sessions/{uuid.uuid4()}", headers=_auth())
        assert resp.status_code == 400

    def test_invalid_session_uuid_400(self, client: TestClient, db_factory) -> None:
        tid = asyncio.get_event_loop().run_until_complete(_seed_task(db_factory))
        resp = client.delete(f"/api/tasks/{tid}/sessions/bad", headers=_auth())
        assert resp.status_code == 400

    def test_link_not_found_404(self, client: TestClient, db_factory) -> None:
        loop = asyncio.get_event_loop()
        tid = loop.run_until_complete(_seed_task(db_factory))
        sid = loop.run_until_complete(_seed_session(db_factory))
        resp = client.delete(f"/api/tasks/{tid}/sessions/{sid}", headers=_auth())
        assert resp.status_code == 404

    def test_detach_existing(self, client: TestClient, db_factory) -> None:
        loop = asyncio.get_event_loop()
        tid = loop.run_until_complete(_seed_task(db_factory))
        sid = loop.run_until_complete(_seed_session(db_factory))
        client.post(f"/api/tasks/{tid}/sessions", json={"session_id": sid}, headers=_auth())

        resp = client.delete(f"/api/tasks/{tid}/sessions/{sid}", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_id"] == tid
        assert body["sessions"] == []
