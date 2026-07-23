"""Tests for /ws/output/text WebSocket endpoint.

Covers:
- ``list_linear_issues`` handler (original tests kept)
- Unknown message type → UNKNOWN_MESSAGE_TYPE error
- Invalid JSON → INVALID_JSON error
- ``list_sessions``: returns session list (no DB path)
- ``list_tasks``: returns empty list when no DB
- ``list_artifacts``: returns empty list when no DB
- ``subscribe``: nonexistent session → SESSION_NOT_FOUND error
- ``subscribe_all``: no active sessions — no crash
- ``unsubscribe``: unknown session_id silently ignored
"""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.session import SessionType
from src.database.models import LinearIssue as LinearIssueModel

API_KEY = "test-api-key"

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


@pytest.fixture(autouse=True)
def _patch_ws_auth():
    """Bypass WebSocket API-key verification for all tests in this module."""

    async def _noop(websocket: object = None, api_key: str = "") -> str:
        return api_key

    with (
        patch("src.api.ws.output_text.verify_ws_api_key", new=_noop),
        patch("src.api.ws.input_text.verify_ws_api_key", new=_noop),
    ):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ws_url() -> str:
    return f"/ws/output/text?api_key={API_KEY}"


# ---------------------------------------------------------------------------
# Output channel — list_linear_issues (original)
# ---------------------------------------------------------------------------


class TestOutputWsListLinearIssues:
    def test_returns_empty_list_when_no_db(self, client: TestClient) -> None:
        """With db_session_factory=None the handler must return an empty list,
        not an 'Unknown message type' error.
        """
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "list_linear_issues"})
            data = ws.receive_json()

        assert data["type"] == "linear_issue_list"
        assert data["issues"] == []

    def test_does_not_return_unknown_message_error(self, client: TestClient) -> None:
        """list_linear_issues must never trigger the 'Unknown message type' error."""
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "list_linear_issues"})
            data = ws.receive_json()

        assert data.get("code") != "UNKNOWN_MESSAGE_TYPE"

    def test_unknown_message_type_still_returns_error(self, client: TestClient) -> None:
        """Genuinely unknown message types on the output channel still return an error."""
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "totally_unknown_xyz"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "UNKNOWN_MESSAGE_TYPE"
        assert "totally_unknown_xyz" in data["content"]

    def test_issue_payload_shape(self, client: TestClient, test_app: FastAPI) -> None:
        """Pins the exact wire shape of linear_issue_list issue dicts.

        Regression guard for the shared ``_issue_to_dict`` serializer: the key
        set must stay stable and ``labels`` must arrive JSON-decoded.
        """
        now = datetime(2024, 3, 1, tzinfo=UTC)
        row = LinearIssueModel(
            id=uuid.UUID("00000000-0000-0000-0000-000000000001"),
            backend_id=test_app.state.settings.RCFLOW_BACKEND_ID,
            linear_id="lin-abc-001",
            identifier="ENG-1",
            title="Test Issue",
            description="A test description",
            priority=2,
            state_name="In Progress",
            state_type="started",
            assignee_id="user-1",
            assignee_name="Alice",
            team_id="team-abc123",
            team_name="Engineering",
            url="https://linear.app/eng/issue/ENG-1",
            labels='["bug", "auth"]',
            created_at=now,
            updated_at=now,
            synced_at=now,
            task_id=None,
        )
        result = MagicMock()
        scalars = MagicMock()
        scalars.all.return_value = [row]
        result.scalars.return_value = scalars
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=result)

        @asynccontextmanager
        async def _factory():
            yield mock_db

        test_app.state.db_session_factory = _factory

        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "list_linear_issues"})
            data = ws.receive_json()

        assert data["type"] == "linear_issue_list"
        assert len(data["issues"]) == 1
        issue = data["issues"][0]
        assert set(issue.keys()) == {
            "id",
            "linear_id",
            "identifier",
            "title",
            "description",
            "priority",
            "state_name",
            "state_type",
            "assignee_id",
            "assignee_name",
            "team_id",
            "team_name",
            "url",
            "labels",
            "created_at",
            "updated_at",
            "synced_at",
            "task_id",
        }
        assert issue["labels"] == ["bug", "auth"]
        assert issue["assignee_id"] == "user-1"
        assert issue["created_at"] == now.isoformat()


# ---------------------------------------------------------------------------
# Invalid JSON
# ---------------------------------------------------------------------------


class TestInvalidJson:
    def test_malformed_json_returns_error(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_text("{not valid json{{")
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "INVALID_JSON"

    def test_connection_stays_open_after_invalid_json(self, client: TestClient) -> None:
        """The connection must NOT be dropped after malformed JSON."""
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_text("{bad}")
            ws.receive_json()  # error
            ws.send_json({"type": "list_linear_issues"})
            data = ws.receive_json()

        assert data["type"] == "linear_issue_list"


# ---------------------------------------------------------------------------
# list_sessions (no-DB in-memory path)
# ---------------------------------------------------------------------------


class TestListSessions:
    def test_returns_session_list_type(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "list_sessions"})
            data = ws.receive_json()

        assert data["type"] == "session_list"
        assert isinstance(data["sessions"], list)

    def test_empty_when_no_sessions(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "list_sessions"})
            data = ws.receive_json()

        assert data["sessions"] == []

    def test_includes_created_session(self, client: TestClient, session_manager) -> None:
        session = session_manager.create_session(SessionType.ONE_SHOT)

        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "list_sessions"})
            data = ws.receive_json()

        ids = [s["session_id"] for s in data["sessions"]]
        assert session.id in ids

    def test_session_entry_has_required_fields(self, client: TestClient, session_manager) -> None:
        session_manager.create_session(SessionType.CONVERSATIONAL)

        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "list_sessions"})
            data = ws.receive_json()

        entry = data["sessions"][0]
        for field in ("session_id", "status", "session_type", "created_at"):
            assert field in entry, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# list_tasks (no DB)
# ---------------------------------------------------------------------------


class TestListTasks:
    def test_returns_empty_list_when_no_db(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "list_tasks"})
            data = ws.receive_json()

        assert data["type"] == "task_list"
        assert data["tasks"] == []


# ---------------------------------------------------------------------------
# list_artifacts (no DB)
# ---------------------------------------------------------------------------


class TestListArtifacts:
    def test_returns_empty_list_when_no_db(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "list_artifacts"})
            data = ws.receive_json()

        assert data["type"] == "artifact_list"
        assert data["artifacts"] == []


# ---------------------------------------------------------------------------
# subscribe — nonexistent session
# ---------------------------------------------------------------------------


class TestSubscribe:
    def test_subscribe_nonexistent_session_returns_error(self, client: TestClient) -> None:
        """When subscribing to a session that doesn't exist, the server must
        send a SESSION_NOT_FOUND error instead of silently doing nothing.
        """
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "subscribe", "session_id": "does-not-exist"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "SESSION_NOT_FOUND"
        assert data["session_id"] == "does-not-exist"

    def test_subscribe_missing_session_id_is_ignored(self, client: TestClient) -> None:
        """Sending subscribe without a session_id must not crash the server."""
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "subscribe"})
            # No response expected for a no-op subscribe; follow up with a
            # known-good message to confirm the connection is still alive.
            ws.send_json({"type": "list_linear_issues"})
            data = ws.receive_json()

        assert data["type"] == "linear_issue_list"


# ---------------------------------------------------------------------------
# subscribe_all — no active sessions
# ---------------------------------------------------------------------------


class TestSubscribeAll:
    def test_subscribe_all_with_no_sessions_does_not_crash(self, client: TestClient) -> None:
        """subscribe_all with zero active sessions must not raise or disconnect."""
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "subscribe_all"})
            # Send another known message to verify the connection is healthy.
            ws.send_json({"type": "list_sessions"})
            data = ws.receive_json()

        assert data["type"] == "session_list"


# ---------------------------------------------------------------------------
# unsubscribe — unknown session ignored
# ---------------------------------------------------------------------------


class TestUnsubscribe:
    def test_unsubscribe_unknown_session_is_silently_ignored(self, client: TestClient) -> None:
        """Unsubscribing from a session that was never subscribed must not crash."""
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "unsubscribe", "session_id": "ghost-id"})
            ws.send_json({"type": "list_sessions"})
            data = ws.receive_json()

        assert data["type"] == "session_list"
