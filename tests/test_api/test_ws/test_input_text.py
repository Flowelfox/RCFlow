"""Tests for /ws/input/text WebSocket endpoint.

Covers the message-type dispatch table, including the defensive
``list_linear_issues`` handler that prevents "Unknown message type" errors
when the client accidentally (or due to a race on startup) sends this
control message to the input channel instead of the output channel.
"""

from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
# Input channel — list_linear_issues defensive handling
# ---------------------------------------------------------------------------


class TestInputWsListLinearIssues:
    def test_returns_empty_list_when_no_db(self, client: TestClient) -> None:
        """With db_session_factory=None the handler must return an empty list,
        not an 'Unknown message type' error — even on the input channel."""
        with client.websocket_connect(f"/ws/input/text?api_key={API_KEY}") as ws:
            ws.send_json({"type": "list_linear_issues"})
            data = ws.receive_json()

        assert data["type"] == "linear_issue_list"
        assert data["issues"] == []

    def test_does_not_return_unknown_message_error(self, client: TestClient) -> None:
        """list_linear_issues on the input channel must not produce an error
        that would surface in the session pane as 'Unknown message type'."""
        with client.websocket_connect(f"/ws/input/text?api_key={API_KEY}") as ws:
            ws.send_json({"type": "list_linear_issues"})
            data = ws.receive_json()

        assert data.get("code") != "UNKNOWN_MESSAGE_TYPE"
        assert data.get("type") != "error"

    def test_unknown_message_type_still_returns_error(self, client: TestClient) -> None:
        """Genuinely unknown message types on the input channel still return an error."""
        with client.websocket_connect(f"/ws/input/text?api_key={API_KEY}") as ws:
            ws.send_json({"type": "totally_unknown_xyz"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "UNKNOWN_MESSAGE_TYPE"
        assert "totally_unknown_xyz" in data["content"]
