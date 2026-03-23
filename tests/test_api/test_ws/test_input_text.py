"""Tests for /ws/input/text WebSocket endpoint.

Covers:
- ``list_linear_issues`` defensive handling (original tests kept)
- Invalid JSON → INVALID_JSON error
- Empty / whitespace-only prompt → EMPTY_PROMPT error
- Unknown message type → UNKNOWN_MESSAGE_TYPE error
- ``end_session``: missing session_id, success, router error
- ``pause_session``: missing session_id, success, router error
- ``resume_session``: missing session_id, success, router error
- ``restore_session``: missing session_id, success, router error
- ``dismiss_session_end_ask``: missing session_id, success, router error
- ``permission_response``: missing session_id, missing request_id, success
- ``prompt``: valid dispatch sends ack; prompt with existing session_id
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.session import SessionType

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

    async def _noop(api_key: str = "") -> str:
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
    return f"/ws/input/text?api_key={API_KEY}"


# ---------------------------------------------------------------------------
# Input channel — list_linear_issues defensive handling (original)
# ---------------------------------------------------------------------------


class TestInputWsListLinearIssues:
    def test_returns_empty_list_when_no_db(self, client: TestClient) -> None:
        """With db_session_factory=None the handler must return an empty list,
        not an 'Unknown message type' error — even on the input channel."""
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "list_linear_issues"})
            data = ws.receive_json()

        assert data["type"] == "linear_issue_list"
        assert data["issues"] == []

    def test_does_not_return_unknown_message_error(self, client: TestClient) -> None:
        """list_linear_issues on the input channel must not produce an error
        that would surface in the session pane as 'Unknown message type'."""
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "list_linear_issues"})
            data = ws.receive_json()

        assert data.get("code") != "UNKNOWN_MESSAGE_TYPE"
        assert data.get("type") != "error"

    def test_unknown_message_type_still_returns_error(self, client: TestClient) -> None:
        """Genuinely unknown message types on the input channel still return an error."""
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "totally_unknown_xyz"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "UNKNOWN_MESSAGE_TYPE"
        assert "totally_unknown_xyz" in data["content"]


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

    def test_plain_text_returns_error(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_text("hello")
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "INVALID_JSON"

    def test_connection_remains_open_after_invalid_json(self, client: TestClient) -> None:
        """The connection must NOT be dropped after receiving malformed JSON."""
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_text("{bad}")
            ws.receive_json()  # error response
            # Send a valid message afterwards — connection should still be alive
            ws.send_json({"type": "list_linear_issues"})
            data = ws.receive_json()

        assert data["type"] == "linear_issue_list"


# ---------------------------------------------------------------------------
# Empty prompt
# ---------------------------------------------------------------------------


class TestEmptyPrompt:
    def test_empty_text_returns_error(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "prompt", "text": ""})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "EMPTY_PROMPT"

    def test_whitespace_only_text_returns_error(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "prompt", "text": "   "})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "EMPTY_PROMPT"


# ---------------------------------------------------------------------------
# end_session
# ---------------------------------------------------------------------------


class TestEndSession:
    def test_missing_session_id_returns_error(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "end_session"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "MISSING_SESSION_ID"

    def test_success_returns_ack(self, client: TestClient) -> None:
        with patch.object(client.app.state.prompt_router, "end_session", new=AsyncMock()):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({"type": "end_session", "session_id": "test-session-id"})
                data = ws.receive_json()

        assert data["type"] == "ack"
        assert data["session_id"] == "test-session-id"

    def test_router_error_returns_error_response(self, client: TestClient) -> None:
        with patch.object(
            client.app.state.prompt_router,
            "end_session",
            new=AsyncMock(side_effect=ValueError("Session not found")),
        ):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({"type": "end_session", "session_id": "bad-id"})
                data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "END_SESSION_ERROR"


# ---------------------------------------------------------------------------
# pause_session
# ---------------------------------------------------------------------------


class TestPauseSession:
    def test_missing_session_id_returns_error(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "pause_session"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "MISSING_SESSION_ID"

    def test_success_returns_ack(self, client: TestClient) -> None:
        with patch.object(client.app.state.prompt_router, "pause_session", new=AsyncMock()):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({"type": "pause_session", "session_id": "test-id"})
                data = ws.receive_json()

        assert data["type"] == "ack"
        assert data["session_id"] == "test-id"

    def test_router_error_returns_error_response(self, client: TestClient) -> None:
        with patch.object(
            client.app.state.prompt_router,
            "pause_session",
            new=AsyncMock(side_effect=RuntimeError("Cannot pause")),
        ):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({"type": "pause_session", "session_id": "bad-id"})
                data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "PAUSE_SESSION_ERROR"


# ---------------------------------------------------------------------------
# resume_session
# ---------------------------------------------------------------------------


class TestResumeSession:
    def test_missing_session_id_returns_error(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "resume_session"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "MISSING_SESSION_ID"

    def test_success_returns_ack(self, client: TestClient) -> None:
        with patch.object(client.app.state.prompt_router, "resume_session", new=AsyncMock()):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({"type": "resume_session", "session_id": "test-id"})
                data = ws.receive_json()

        assert data["type"] == "ack"
        assert data["session_id"] == "test-id"

    def test_router_error_returns_error_response(self, client: TestClient) -> None:
        with patch.object(
            client.app.state.prompt_router,
            "resume_session",
            new=AsyncMock(side_effect=ValueError("Session not paused")),
        ):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({"type": "resume_session", "session_id": "bad-id"})
                data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "RESUME_SESSION_ERROR"


# ---------------------------------------------------------------------------
# restore_session
# ---------------------------------------------------------------------------


class TestRestoreSession:
    def test_missing_session_id_returns_error(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "restore_session"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "MISSING_SESSION_ID"

    def test_success_returns_ack_with_status(self, client: TestClient) -> None:
        mock_session = MagicMock()
        mock_session.status.value = "active"
        mock_session.session_type.value = "conversational"

        with patch.object(
            client.app.state.prompt_router,
            "restore_session",
            new=AsyncMock(return_value=mock_session),
        ):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({"type": "restore_session", "session_id": "test-id"})
                data = ws.receive_json()

        assert data["type"] == "ack"
        assert data["session_id"] == "test-id"
        assert data["status"] == "active"
        assert data["session_type"] == "conversational"

    def test_router_error_returns_error_response(self, client: TestClient) -> None:
        with patch.object(
            client.app.state.prompt_router,
            "restore_session",
            new=AsyncMock(side_effect=ValueError("Session not found")),
        ):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({"type": "restore_session", "session_id": "bad-id"})
                data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "RESTORE_SESSION_ERROR"


# ---------------------------------------------------------------------------
# dismiss_session_end_ask
# ---------------------------------------------------------------------------


class TestDismissSessionEndAsk:
    def test_missing_session_id_returns_error(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "dismiss_session_end_ask"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "MISSING_SESSION_ID"

    def test_success_returns_ack(self, client: TestClient) -> None:
        with patch.object(client.app.state.prompt_router, "dismiss_session_end_ask", return_value=None):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({"type": "dismiss_session_end_ask", "session_id": "test-id"})
                data = ws.receive_json()

        assert data["type"] == "ack"
        assert data["session_id"] == "test-id"

    def test_router_error_returns_error_response(self, client: TestClient) -> None:
        with patch.object(
            client.app.state.prompt_router,
            "dismiss_session_end_ask",
            side_effect=ValueError("Session not found"),
        ):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({"type": "dismiss_session_end_ask", "session_id": "bad-id"})
                data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "DISMISS_SESSION_END_ASK_ERROR"


# ---------------------------------------------------------------------------
# permission_response
# ---------------------------------------------------------------------------


class TestPermissionResponse:
    def test_missing_session_id_returns_error(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "permission_response", "request_id": "req-1"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "MISSING_SESSION_ID"

    def test_missing_request_id_returns_error(self, client: TestClient) -> None:
        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "permission_response", "session_id": "sess-1"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "MISSING_REQUEST_ID"

    def test_success_returns_ack(self, client: TestClient) -> None:
        with patch.object(client.app.state.prompt_router, "resolve_permission", return_value=None):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({
                    "type": "permission_response",
                    "session_id": "sess-1",
                    "request_id": "req-1",
                    "decision": "allow",
                    "scope": "once",
                })
                data = ws.receive_json()

        assert data["type"] == "ack"
        assert data["session_id"] == "sess-1"


# ---------------------------------------------------------------------------
# Prompt dispatch
# ---------------------------------------------------------------------------


class TestPromptDispatch:
    def test_valid_prompt_sends_ack_with_session_id(self, client: TestClient) -> None:
        with (
            patch.object(client.app.state.prompt_router, "ensure_session", return_value="new-session-id"),
            patch.object(client.app.state.prompt_router, "handle_prompt", new=AsyncMock()),
        ):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({"type": "prompt", "text": "list my files"})
                data = ws.receive_json()

        assert data["type"] == "ack"
        assert data["session_id"] == "new-session-id"

    def test_prompt_with_explicit_session_id_uses_it(self, client: TestClient) -> None:
        with (
            patch.object(client.app.state.prompt_router, "ensure_session", return_value="existing-id"),
            patch.object(client.app.state.prompt_router, "handle_prompt", new=AsyncMock()),
        ):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({"type": "prompt", "text": "continue", "session_id": "existing-id"})
                data = ws.receive_json()

        assert data["type"] == "ack"
        assert data["session_id"] == "existing-id"

    def test_ensure_session_called_with_given_session_id(self, client: TestClient) -> None:
        mock_ensure = MagicMock(return_value="existing-id")
        with (
            patch.object(client.app.state.prompt_router, "ensure_session", mock_ensure),
            patch.object(client.app.state.prompt_router, "handle_prompt", new=AsyncMock()),
        ):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({"type": "prompt", "text": "hello", "session_id": "existing-id"})
                ws.receive_json()

        mock_ensure.assert_called_once_with("existing-id")

    def test_prompt_without_session_id_creates_new_session(self, client: TestClient) -> None:
        mock_ensure = MagicMock(return_value="brand-new-id")
        with (
            patch.object(client.app.state.prompt_router, "ensure_session", mock_ensure),
            patch.object(client.app.state.prompt_router, "handle_prompt", new=AsyncMock()),
        ):
            with client.websocket_connect(_ws_url()) as ws:
                ws.send_json({"type": "prompt", "text": "hello"})
                ws.receive_json()

        mock_ensure.assert_called_once_with(None)
