"""Tests for /ws/output/text WebSocket endpoint.

Covers the message-type dispatch table, with particular focus on the
``list_linear_issues`` handler that was added with the Linear integration.
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
# Output channel — list_linear_issues
# ---------------------------------------------------------------------------


class TestOutputWsListLinearIssues:
    def test_returns_empty_list_when_no_db(self, client: TestClient) -> None:
        """With db_session_factory=None the handler must return an empty list,
        not an 'Unknown message type' error."""
        with client.websocket_connect(f"/ws/output/text?api_key={API_KEY}") as ws:
            ws.send_json({"type": "list_linear_issues"})
            data = ws.receive_json()

        assert data["type"] == "linear_issue_list"
        assert data["issues"] == []

    def test_does_not_return_unknown_message_error(self, client: TestClient) -> None:
        """list_linear_issues must never trigger the 'Unknown message type' error."""
        with client.websocket_connect(f"/ws/output/text?api_key={API_KEY}") as ws:
            ws.send_json({"type": "list_linear_issues"})
            data = ws.receive_json()

        assert data.get("code") != "UNKNOWN_MESSAGE_TYPE"

    def test_unknown_message_type_still_returns_error(self, client: TestClient) -> None:
        """Genuinely unknown message types on the output channel still return an error."""
        with client.websocket_connect(f"/ws/output/text?api_key={API_KEY}") as ws:
            ws.send_json({"type": "totally_unknown_xyz"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "UNKNOWN_MESSAGE_TYPE"
        assert "totally_unknown_xyz" in data["content"]
