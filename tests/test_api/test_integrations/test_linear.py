"""Tests for the Linear integration HTTP endpoints.

Endpoints under /api/integrations/linear/ are tested here.
The DB session factory is mocked; no real database is required.
LinearService is mocked to avoid real HTTP calls to Linear's API.

New in this revision:
- POST /api/integrations/linear/test  — validate API key, return teams
- GET  /api/integrations/linear/teams — list teams for configured key
- POST /sync no longer requires LINEAR_TEAM_ID (syncs all teams when blank)
- POST /issues accepts team_id in request body when LINEAR_TEAM_ID is blank
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.config import Settings
from src.core.session import SessionManager
from src.models.db import LinearIssue as LinearIssueModel
from src.models.db import Task as TaskModel

API_KEY = "test-api-key"
BACKEND_ID = "test-backend-id"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def _make_linear_settings(**overrides: Any) -> Settings:
    base = dict(
        RCFLOW_HOST="127.0.0.1",
        RCFLOW_PORT=8765,
        RCFLOW_API_KEY=API_KEY,
        RCFLOW_BACKEND_ID=BACKEND_ID,
        DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/rcflow_test",
        LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="test-key",
        ANTHROPIC_MODEL="claude-sonnet-4-20250514",
        LINEAR_API_KEY="lin_api_test_key",
        LINEAR_TEAM_ID="team-abc123",
    )
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _make_db_factory(mock_db: AsyncMock):
    """Wrap mock_db in an async context manager factory."""

    @asynccontextmanager
    async def _factory():
        yield mock_db

    return _factory


def _scalar_result(value: Any) -> MagicMock:
    """Return a mock that mimics SQLAlchemy Result for scalar operations."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    scalars = MagicMock()
    scalars.all.return_value = value if isinstance(value, list) else []
    result.scalars.return_value = scalars
    return result


def _make_issue_row(**kwargs: Any) -> LinearIssueModel:
    """Create a LinearIssueModel ORM object without a DB session."""
    now = datetime(2024, 3, 1, tzinfo=UTC)
    defaults: dict[str, Any] = dict(
        id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
        backend_id=BACKEND_ID,
        linear_id="lin-abc-001",
        identifier="ENG-1",
        title="Test Issue",
        description="A test description",
        priority=2,
        state_name="In Progress",
        state_type="started",
        assignee_id=None,
        assignee_name=None,
        team_id="team-abc123",
        team_name="Engineering",
        url="https://linear.app/eng/issue/ENG-1",
        labels='["bug"]',
        created_at=now,
        updated_at=now,
        synced_at=now,
        task_id=None,
    )
    defaults.update(kwargs)
    return LinearIssueModel(**defaults)


def _make_task_row(**kwargs: Any) -> TaskModel:
    """Create a TaskModel ORM object without a DB session."""
    now = datetime(2024, 3, 1, tzinfo=UTC)
    defaults: dict[str, Any] = dict(
        id=uuid.UUID("00000000-0000-0000-0000-000000000099"),
        backend_id=BACKEND_ID,
        title="Task One",
        description=None,
        status="todo",
        source="user",
        created_at=now,
        updated_at=now,
    )
    defaults.update(kwargs)
    return TaskModel(**defaults)


def _parsed_issue_dict() -> dict[str, Any]:
    """Return a dict matching the output of LinearService._parse_issue()."""
    now = datetime(2024, 3, 1, tzinfo=UTC)
    return {
        "linear_id": "lin-abc-001",
        "identifier": "ENG-1",
        "title": "Test Issue",
        "description": "A test description",
        "priority": 2,
        "state_name": "In Progress",
        "state_type": "started",
        "assignee_id": None,
        "assignee_name": None,
        "team_id": "team-abc123",
        "team_name": "Engineering",
        "url": "https://linear.app/eng/issue/ENG-1",
        "labels": "[]",
        "created_at": now,
        "updated_at": now,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def linear_settings() -> Settings:
    return _make_linear_settings()


@pytest.fixture
def linear_app(test_app: FastAPI, linear_settings: Settings) -> FastAPI:
    """Extend test_app with Linear settings on app.state."""
    test_app.state.settings = linear_settings
    return test_app


@pytest.fixture
def client(linear_app: FastAPI) -> TestClient:
    return TestClient(linear_app)


# ---------------------------------------------------------------------------
# TestTestLinearConnection
# ---------------------------------------------------------------------------


class TestTestLinearConnection:
    def test_returns_teams_on_valid_key(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        teams = [{"id": "team-1", "name": "Engineering"}]
        mock_svc = AsyncMock()
        mock_svc.fetch_teams = AsyncMock(return_value=teams)
        mock_svc.aclose = AsyncMock()

        with patch("src.api.integrations.linear.LinearService", return_value=mock_svc):
            resp = client.post(
                "/api/integrations/linear/test",
                json={"api_key": "lin_api_test"},
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["teams"] == teams

    def test_invalid_key_returns_502(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        from src.services.linear_service import LinearServiceError

        mock_svc = AsyncMock()
        mock_svc.fetch_teams = AsyncMock(
            side_effect=LinearServiceError("Linear API key is invalid or expired", status_code=401)
        )
        mock_svc.aclose = AsyncMock()

        with patch("src.api.integrations.linear.LinearService", return_value=mock_svc):
            resp = client.post(
                "/api/integrations/linear/test",
                json={"api_key": "bad_key"},
                headers=_auth_headers(),
            )

        assert resp.status_code == 502
        assert "invalid" in resp.json()["detail"].lower()

    def test_missing_api_key_body_returns_422(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        resp = client.post(
            "/api/integrations/linear/test",
            json={},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.post(
            "/api/integrations/linear/test",
            json={"api_key": "lin_api_test"},
        )
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# TestListLinearTeams
# ---------------------------------------------------------------------------


class TestListLinearTeams:
    def test_returns_teams(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        teams = [
            {"id": "team-1", "name": "Engineering"},
            {"id": "team-2", "name": "Design"},
        ]
        mock_svc = AsyncMock()
        mock_svc.fetch_teams = AsyncMock(return_value=teams)
        mock_svc.aclose = AsyncMock()

        with patch("src.api.integrations.linear._get_linear_service", return_value=mock_svc):
            resp = client.get(
                "/api/integrations/linear/teams", headers=_auth_headers()
            )

        assert resp.status_code == 200
        assert resp.json()["teams"] == teams

    def test_missing_api_key_returns_503(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        linear_app.state.settings = _make_linear_settings(LINEAR_API_KEY="")

        resp = client.get(
            "/api/integrations/linear/teams", headers=_auth_headers()
        )

        assert resp.status_code == 503

    def test_linear_api_error_returns_502(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        from src.services.linear_service import LinearServiceError

        mock_svc = AsyncMock()
        mock_svc.fetch_teams = AsyncMock(side_effect=LinearServiceError("rate limited"))
        mock_svc.aclose = AsyncMock()

        with patch("src.api.integrations.linear._get_linear_service", return_value=mock_svc):
            resp = client.get(
                "/api/integrations/linear/teams", headers=_auth_headers()
            )

        assert resp.status_code == 502

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/integrations/linear/teams")
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# TestListLinearIssues
# ---------------------------------------------------------------------------


class TestListLinearIssues:
    def test_returns_empty_list_when_no_issues(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result([]))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.get("/api/integrations/linear/issues", headers=_auth_headers())

        assert resp.status_code == 200
        body = resp.json()
        assert body["issues"] == []
        assert body["total"] == 0

    def test_returns_cached_issues(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        issue = _make_issue_row()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result([issue]))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.get("/api/integrations/linear/issues", headers=_auth_headers())

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["issues"][0]["identifier"] == "ENG-1"
        assert body["issues"][0]["title"] == "Test Issue"

    def test_filter_by_state_type_adds_where_clause(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        issue = _make_issue_row(state_type="started")
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result([issue]))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.get(
            "/api/integrations/linear/issues?state_type=started", headers=_auth_headers()
        )

        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_search_by_title(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        issue1 = _make_issue_row(title="Fix login bug")
        issue2 = _make_issue_row(
            id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            title="Update dashboard",
        )
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result([issue1, issue2]))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.get(
            "/api/integrations/linear/issues?q=login", headers=_auth_headers()
        )

        assert resp.status_code == 200
        issues = resp.json()["issues"]
        assert len(issues) == 1
        assert issues[0]["title"] == "Fix login bug"

    def test_search_by_identifier(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        issue = _make_issue_row(identifier="ENG-42")
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result([issue]))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.get(
            "/api/integrations/linear/issues?q=ENG-42", headers=_auth_headers()
        )

        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    def test_requires_auth(self, client: TestClient, linear_app: FastAPI) -> None:
        resp = client.get("/api/integrations/linear/issues")
        assert resp.status_code in (401, 403, 422)

    def test_results_sorted_by_updated_at_desc(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        older = _make_issue_row(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            title="Older",
            updated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        newer = _make_issue_row(
            id=uuid.UUID("00000000-0000-0000-0000-000000000002"),
            title="Newer",
            updated_at=datetime(2024, 3, 1, tzinfo=UTC),
        )
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result([older, newer]))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.get("/api/integrations/linear/issues", headers=_auth_headers())

        issues = resp.json()["issues"]
        assert issues[0]["title"] == "Newer"
        assert issues[1]["title"] == "Older"


# ---------------------------------------------------------------------------
# TestGetLinearIssue
# ---------------------------------------------------------------------------


class TestGetLinearIssue:
    def test_returns_issue(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        issue = _make_issue_row()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(issue))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.get(
            f"/api/integrations/linear/issues/{issue.id}", headers=_auth_headers()
        )

        assert resp.status_code == 200
        assert resp.json()["identifier"] == "ENG-1"

    def test_not_found_returns_404(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(None))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.get(
            f"/api/integrations/linear/issues/{uuid.uuid4()}", headers=_auth_headers()
        )

        assert resp.status_code == 404

    def test_invalid_uuid_returns_422(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        resp = client.get(
            "/api/integrations/linear/issues/not-a-uuid", headers=_auth_headers()
        )
        assert resp.status_code == 422

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get(f"/api/integrations/linear/issues/{uuid.uuid4()}")
        assert resp.status_code in (401, 403, 422)

    def test_task_id_null_when_not_linked(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        issue = _make_issue_row(task_id=None)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(issue))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.get(
            f"/api/integrations/linear/issues/{issue.id}", headers=_auth_headers()
        )

        assert resp.json()["task_id"] is None

    def test_task_id_serialized_when_linked(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        task_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
        issue = _make_issue_row(task_id=task_id)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(issue))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.get(
            f"/api/integrations/linear/issues/{issue.id}", headers=_auth_headers()
        )

        assert resp.json()["task_id"] == str(task_id)


# ---------------------------------------------------------------------------
# TestSyncLinearIssues
# ---------------------------------------------------------------------------


class TestSyncLinearIssues:
    def test_sync_upserts_issues_and_returns_count(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        issue = _make_issue_row()
        mock_db = AsyncMock()
        # First execute: check existing (None → insert), then scalars for list
        mock_db.execute = AsyncMock(return_value=_scalar_result(None))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        mock_svc = AsyncMock()
        mock_svc.fetch_issues = AsyncMock(return_value=[_parsed_issue_dict()])
        mock_svc.aclose = AsyncMock()

        with patch("src.api.integrations.linear._get_linear_service", return_value=mock_svc):
            resp = client.post(
                "/api/integrations/linear/sync", headers=_auth_headers()
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["synced"] == 1
        assert body["errors"] == []

    def test_sync_broadcasts_to_ws_clients(
        self, client: TestClient, linear_app: FastAPI, session_manager: SessionManager
    ) -> None:
        linear_app.state.session_manager = session_manager
        issue = _make_issue_row()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(None))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        mock_svc = AsyncMock()
        mock_svc.fetch_issues = AsyncMock(return_value=[_parsed_issue_dict()])
        mock_svc.aclose = AsyncMock()

        broadcast_calls: list[dict] = []
        original_broadcast = session_manager.broadcast_linear_issue_update

        def _capture(data: dict) -> None:
            broadcast_calls.append(data)

        session_manager.broadcast_linear_issue_update = _capture  # type: ignore[method-assign]
        try:
            with patch("src.api.integrations.linear._get_linear_service", return_value=mock_svc):
                client.post("/api/integrations/linear/sync", headers=_auth_headers())
        finally:
            session_manager.broadcast_linear_issue_update = original_broadcast  # type: ignore[method-assign]

        assert len(broadcast_calls) == 1
        assert broadcast_calls[0]["identifier"] == "ENG-1"

    def test_sync_without_team_id_fetches_all_issues(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        """When LINEAR_TEAM_ID is blank, sync fetches all issues across all teams."""
        linear_app.state.settings = _make_linear_settings(LINEAR_TEAM_ID="")

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(None))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        mock_svc = AsyncMock()
        mock_svc.fetch_all_issues = AsyncMock(return_value=[_parsed_issue_dict()])
        mock_svc.aclose = AsyncMock()

        with patch("src.api.integrations.linear._get_linear_service", return_value=mock_svc):
            resp = client.post(
                "/api/integrations/linear/sync", headers=_auth_headers()
            )

        assert resp.status_code == 200
        assert resp.json()["synced"] == 1
        mock_svc.fetch_all_issues.assert_called_once()
        mock_svc.fetch_issues.assert_not_called()

    def test_missing_api_key_returns_503(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        linear_app.state.settings = _make_linear_settings(LINEAR_API_KEY="")

        resp = client.post("/api/integrations/linear/sync", headers=_auth_headers())

        assert resp.status_code == 503

    def test_linear_api_error_returns_502(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        from src.services.linear_service import LinearServiceError

        mock_svc = AsyncMock()
        mock_svc.fetch_issues = AsyncMock(side_effect=LinearServiceError("API down"))
        mock_svc.aclose = AsyncMock()

        with patch("src.api.integrations.linear._get_linear_service", return_value=mock_svc):
            resp = client.post("/api/integrations/linear/sync", headers=_auth_headers())

        assert resp.status_code == 502

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.post("/api/integrations/linear/sync")
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# TestCreateLinearIssue
# ---------------------------------------------------------------------------


class TestCreateLinearIssue:
    def test_creates_and_caches_issue(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(None))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        mock_svc = AsyncMock()
        mock_svc.create_issue = AsyncMock(return_value=_parsed_issue_dict())
        mock_svc.aclose = AsyncMock()

        with patch("src.api.integrations.linear._get_linear_service", return_value=mock_svc):
            resp = client.post(
                "/api/integrations/linear/issues",
                json={"title": "Test Issue", "priority": 2},
                headers=_auth_headers(),
            )

        assert resp.status_code == 201
        body = resp.json()
        assert body["identifier"] == "ENG-1"
        assert body["title"] == "Test Issue"

    def test_broadcasts_after_create(
        self, client: TestClient, linear_app: FastAPI, session_manager: SessionManager
    ) -> None:
        linear_app.state.session_manager = session_manager
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(None))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        mock_svc = AsyncMock()
        mock_svc.create_issue = AsyncMock(return_value=_parsed_issue_dict())
        mock_svc.aclose = AsyncMock()

        broadcast_calls: list[dict] = []
        session_manager.broadcast_linear_issue_update = broadcast_calls.append  # type: ignore[method-assign]

        with patch("src.api.integrations.linear._get_linear_service", return_value=mock_svc):
            client.post(
                "/api/integrations/linear/issues",
                json={"title": "Test Issue"},
                headers=_auth_headers(),
            )

        assert len(broadcast_calls) == 1

    def test_missing_team_id_in_config_and_body_returns_422(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        """If LINEAR_TEAM_ID is blank and no team_id in body, return 422."""
        linear_app.state.settings = _make_linear_settings(LINEAR_TEAM_ID="")

        resp = client.post(
            "/api/integrations/linear/issues",
            json={"title": "Test"},
            headers=_auth_headers(),
        )

        assert resp.status_code == 422

    def test_creates_with_body_team_id_when_config_team_id_unset(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        """When LINEAR_TEAM_ID is blank, team_id in request body is used."""
        linear_app.state.settings = _make_linear_settings(LINEAR_TEAM_ID="")

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(None))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        mock_svc = AsyncMock()
        mock_svc.create_issue = AsyncMock(return_value=_parsed_issue_dict())
        mock_svc.aclose = AsyncMock()

        with patch("src.api.integrations.linear._get_linear_service", return_value=mock_svc):
            resp = client.post(
                "/api/integrations/linear/issues",
                json={"title": "Test", "team_id": "team-override-001"},
                headers=_auth_headers(),
            )

        assert resp.status_code == 201
        _, kwargs = mock_svc.create_issue.call_args
        assert kwargs["team_id"] == "team-override-001"

    def test_missing_api_key_returns_503(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        linear_app.state.settings = _make_linear_settings(LINEAR_API_KEY="")

        resp = client.post(
            "/api/integrations/linear/issues",
            json={"title": "Test"},
            headers=_auth_headers(),
        )

        assert resp.status_code == 503

    def test_linear_api_error_returns_502(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        from src.services.linear_service import LinearServiceError

        mock_svc = AsyncMock()
        mock_svc.create_issue = AsyncMock(side_effect=LinearServiceError("creation failed"))
        mock_svc.aclose = AsyncMock()

        with patch("src.api.integrations.linear._get_linear_service", return_value=mock_svc):
            resp = client.post(
                "/api/integrations/linear/issues",
                json={"title": "Test"},
                headers=_auth_headers(),
            )

        assert resp.status_code == 502

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.post(
            "/api/integrations/linear/issues", json={"title": "Test"}
        )
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# TestUpdateLinearIssue
# ---------------------------------------------------------------------------


class TestUpdateLinearIssue:
    def test_updates_and_returns_issue(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        issue = _make_issue_row()
        mock_db = AsyncMock()
        # First execute: find existing issue; second: upsert check (same issue)
        mock_db.execute = AsyncMock(side_effect=[
            _scalar_result(issue),   # find by id
            _scalar_result(issue),   # upsert check (existing)
        ])
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        updated_dict = {**_parsed_issue_dict(), "title": "Updated title"}
        mock_svc = AsyncMock()
        mock_svc.update_issue = AsyncMock(return_value=updated_dict)
        mock_svc.aclose = AsyncMock()

        with patch("src.api.integrations.linear._get_linear_service", return_value=mock_svc):
            resp = client.patch(
                f"/api/integrations/linear/issues/{issue.id}",
                json={"title": "Updated title"},
                headers=_auth_headers(),
            )

        assert resp.status_code == 200
        assert resp.json()["title"] == "Updated title"

    def test_issue_not_found_returns_404(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(None))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.patch(
            f"/api/integrations/linear/issues/{uuid.uuid4()}",
            json={"title": "New title"},
            headers=_auth_headers(),
        )

        assert resp.status_code == 404

    def test_invalid_uuid_returns_422(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        resp = client.patch(
            "/api/integrations/linear/issues/not-a-uuid",
            json={"title": "x"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422

    def test_linear_api_error_returns_502(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        from src.services.linear_service import LinearServiceError

        issue = _make_issue_row()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(issue))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        mock_svc = AsyncMock()
        mock_svc.update_issue = AsyncMock(side_effect=LinearServiceError("update failed"))
        mock_svc.aclose = AsyncMock()

        with patch("src.api.integrations.linear._get_linear_service", return_value=mock_svc):
            resp = client.patch(
                f"/api/integrations/linear/issues/{issue.id}",
                json={"title": "x"},
                headers=_auth_headers(),
            )

        assert resp.status_code == 502

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.patch(
            f"/api/integrations/linear/issues/{uuid.uuid4()}",
            json={"title": "x"},
        )
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# TestLinkIssueToTask
# ---------------------------------------------------------------------------


class TestLinkIssueToTask:
    def test_links_issue_to_task(
        self, client: TestClient, linear_app: FastAPI, session_manager: SessionManager
    ) -> None:
        linear_app.state.session_manager = session_manager
        issue = _make_issue_row()
        task = _make_task_row()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[
            _scalar_result(issue),
            _scalar_result(task),
        ])
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.post(
            f"/api/integrations/linear/issues/{issue.id}/link",
            json={"task_id": str(task.id)},
            headers=_auth_headers(),
        )

        assert resp.status_code == 200
        # issue.task_id is set to task.id by the endpoint
        assert issue.task_id == task.id

    def test_broadcasts_after_link(
        self, client: TestClient, linear_app: FastAPI, session_manager: SessionManager
    ) -> None:
        linear_app.state.session_manager = session_manager
        issue = _make_issue_row()
        task = _make_task_row()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[
            _scalar_result(issue),
            _scalar_result(task),
        ])
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        broadcast_calls: list[dict] = []
        session_manager.broadcast_linear_issue_update = broadcast_calls.append  # type: ignore[method-assign]

        client.post(
            f"/api/integrations/linear/issues/{issue.id}/link",
            json={"task_id": str(task.id)},
            headers=_auth_headers(),
        )

        assert len(broadcast_calls) == 1

    def test_issue_not_found_returns_404(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(None))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.post(
            f"/api/integrations/linear/issues/{uuid.uuid4()}/link",
            json={"task_id": str(uuid.uuid4())},
            headers=_auth_headers(),
        )

        assert resp.status_code == 404

    def test_task_not_found_returns_404(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        issue = _make_issue_row()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=[
            _scalar_result(issue),
            _scalar_result(None),  # task not found
        ])
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.post(
            f"/api/integrations/linear/issues/{issue.id}/link",
            json={"task_id": str(uuid.uuid4())},
            headers=_auth_headers(),
        )

        assert resp.status_code == 404
        assert "Task" in resp.json()["detail"]

    def test_invalid_issue_uuid_returns_422(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        resp = client.post(
            "/api/integrations/linear/issues/bad-uuid/link",
            json={"task_id": str(uuid.uuid4())},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422

    def test_invalid_task_uuid_returns_422(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        issue = _make_issue_row()
        resp = client.post(
            f"/api/integrations/linear/issues/{issue.id}/link",
            json={"task_id": "not-a-uuid"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.post(
            f"/api/integrations/linear/issues/{uuid.uuid4()}/link",
            json={"task_id": str(uuid.uuid4())},
        )
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# TestCreateTaskFromLinearIssue
# ---------------------------------------------------------------------------


class TestCreateTaskFromLinearIssue:
    def test_creates_task_and_links_issue(
        self, client: TestClient, linear_app: FastAPI, session_manager: SessionManager
    ) -> None:
        linear_app.state.session_manager = session_manager
        issue = _make_issue_row(task_id=None)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(issue))
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.post(
            f"/api/integrations/linear/issues/{issue.id}/create-task",
            headers=_auth_headers(),
        )

        assert resp.status_code == 201
        body = resp.json()
        assert "task" in body
        assert "issue" in body
        assert body["task"]["title"] == issue.title
        assert body["task"]["source"] == "linear"
        assert body["task"]["status"] == "todo"
        assert body["task"]["sessions"] == []

    def test_returns_409_when_already_linked(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        task_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
        issue = _make_issue_row(task_id=task_id)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(issue))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.post(
            f"/api/integrations/linear/issues/{issue.id}/create-task",
            headers=_auth_headers(),
        )

        assert resp.status_code == 409
        assert "already linked" in resp.json()["detail"]

    def test_returns_404_when_issue_not_found(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(None))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.post(
            f"/api/integrations/linear/issues/{uuid.uuid4()}/create-task",
            headers=_auth_headers(),
        )

        assert resp.status_code == 404

    def test_returns_422_for_invalid_uuid(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        resp = client.post(
            "/api/integrations/linear/issues/not-a-uuid/create-task",
            headers=_auth_headers(),
        )
        assert resp.status_code == 422

    def test_broadcasts_task_and_issue_updates(
        self, client: TestClient, linear_app: FastAPI, session_manager: SessionManager
    ) -> None:
        linear_app.state.session_manager = session_manager
        issue = _make_issue_row(task_id=None)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(issue))
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        task_broadcasts: list[dict] = []
        issue_broadcasts: list[dict] = []
        session_manager.broadcast_task_update = task_broadcasts.append  # type: ignore[method-assign]
        session_manager.broadcast_linear_issue_update = issue_broadcasts.append  # type: ignore[method-assign]

        client.post(
            f"/api/integrations/linear/issues/{issue.id}/create-task",
            headers=_auth_headers(),
        )

        assert len(task_broadcasts) == 1
        assert len(issue_broadcasts) == 1
        assert task_broadcasts[0]["source"] == "linear"

    def test_task_description_copied_from_issue(
        self, client: TestClient, linear_app: FastAPI, session_manager: SessionManager
    ) -> None:
        linear_app.state.session_manager = session_manager
        issue = _make_issue_row(task_id=None, description="Issue body text")
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(issue))
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.post(
            f"/api/integrations/linear/issues/{issue.id}/create-task",
            headers=_auth_headers(),
        )

        assert resp.status_code == 201
        assert resp.json()["task"]["description"] == "Issue body text"

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.post(
            f"/api/integrations/linear/issues/{uuid.uuid4()}/create-task"
        )
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# TestUnlinkIssueFromTask
# ---------------------------------------------------------------------------


class TestUnlinkIssueFromTask:
    def test_unlinks_issue_from_task(
        self, client: TestClient, linear_app: FastAPI, session_manager: SessionManager
    ) -> None:
        linear_app.state.session_manager = session_manager
        task_id = uuid.UUID("00000000-0000-0000-0000-000000000099")
        issue = _make_issue_row(task_id=task_id)
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(issue))
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.delete(
            f"/api/integrations/linear/issues/{issue.id}/link", headers=_auth_headers()
        )

        assert resp.status_code == 200
        # task_id should be cleared by the endpoint
        assert issue.task_id is None

    def test_broadcasts_after_unlink(
        self, client: TestClient, linear_app: FastAPI, session_manager: SessionManager
    ) -> None:
        linear_app.state.session_manager = session_manager
        issue = _make_issue_row(task_id=uuid.UUID("00000000-0000-0000-0000-000000000099"))
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(issue))
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        broadcast_calls: list[dict] = []
        session_manager.broadcast_linear_issue_update = broadcast_calls.append  # type: ignore[method-assign]

        client.delete(
            f"/api/integrations/linear/issues/{issue.id}/link", headers=_auth_headers()
        )

        assert len(broadcast_calls) == 1
        assert broadcast_calls[0]["task_id"] is None

    def test_issue_not_found_returns_404(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=_scalar_result(None))
        linear_app.state.db_session_factory = _make_db_factory(mock_db)

        resp = client.delete(
            f"/api/integrations/linear/issues/{uuid.uuid4()}/link", headers=_auth_headers()
        )

        assert resp.status_code == 404

    def test_invalid_uuid_returns_422(
        self, client: TestClient, linear_app: FastAPI
    ) -> None:
        resp = client.delete(
            "/api/integrations/linear/issues/not-a-uuid/link", headers=_auth_headers()
        )
        assert resp.status_code == 422

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.delete(
            f"/api/integrations/linear/issues/{uuid.uuid4()}/link"
        )
        assert resp.status_code in (401, 403, 422)
