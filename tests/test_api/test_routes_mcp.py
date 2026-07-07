"""Tests for the MCP bridge HTTP endpoints (src/api/routes/mcp.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from src.services.mcp_bridge import McpSessionTokenRegistry, McpToolSpec, ToolCallOutcome

if TYPE_CHECKING:
    from fastapi import FastAPI


@pytest.fixture
def bridge() -> MagicMock:
    bridge = MagicMock()
    bridge.tokens = McpSessionTokenRegistry()
    bridge.list_agent_tools.return_value = [
        McpToolSpec(name="system_info", description="System info", input_schema={"type": "object"})
    ]
    bridge.call_tool = AsyncMock(return_value=ToolCallOutcome(text="result text", is_error=False))
    return bridge


@pytest.fixture
def client(test_app: FastAPI, bridge: MagicMock) -> TestClient:
    test_app.state.mcp_bridge = bridge
    return TestClient(test_app)


@pytest.fixture
def token(bridge: MagicMock) -> str:
    return bridge.tokens.issue("sess-1")


class TestAuth:
    def test_missing_token_401(self, client: TestClient) -> None:
        assert client.get("/api/mcp/tools").status_code == 401
        assert client.post("/api/mcp/call", json={"tool": "x"}).status_code == 401

    def test_invalid_token_401(self, client: TestClient) -> None:
        headers = {"X-RCFlow-MCP-Token": "bogus"}
        assert client.get("/api/mcp/tools", headers=headers).status_code == 401
        assert client.post("/api/mcp/call", json={"tool": "x"}, headers=headers).status_code == 401

    def test_worker_api_key_not_accepted(self, client: TestClient, token: str) -> None:
        """The worker-wide X-API-Key must NOT unlock the MCP endpoints."""
        headers = {"X-API-Key": "test-api-key"}
        assert client.get("/api/mcp/tools", headers=headers).status_code == 401

    def test_revoked_token_401(self, client: TestClient, bridge: MagicMock, token: str) -> None:
        bridge.tokens.revoke_session("sess-1")
        headers = {"X-RCFlow-MCP-Token": token}
        assert client.get("/api/mcp/tools", headers=headers).status_code == 401

    def test_bridge_not_initialised_503(self, test_app: FastAPI) -> None:
        if hasattr(test_app.state, "mcp_bridge"):
            del test_app.state.mcp_bridge
        client = TestClient(test_app)
        resp = client.get("/api/mcp/tools", headers={"X-RCFlow-MCP-Token": "x"})
        assert resp.status_code == 503


class TestListTools:
    def test_lists_mcp_shaped_tools(self, client: TestClient, token: str) -> None:
        resp = client.get("/api/mcp/tools", headers={"X-RCFlow-MCP-Token": token})
        assert resp.status_code == 200
        tools = resp.json()["tools"]
        assert tools == [{"name": "system_info", "description": "System info", "inputSchema": {"type": "object"}}]


class TestCall:
    def test_dispatches_with_token_session(self, client: TestClient, bridge: MagicMock, token: str) -> None:
        resp = client.post(
            "/api/mcp/call",
            json={"tool": "system_info", "arguments": {"category": "os"}},
            headers={"X-RCFlow-MCP-Token": token},
        )
        assert resp.status_code == 200
        assert resp.json() == {"content": "result text", "is_error": False}
        bridge.call_tool.assert_awaited_once_with("sess-1", "system_info", {"category": "os"})

    def test_error_outcome_is_200_with_flag(self, client: TestClient, bridge: MagicMock, token: str) -> None:
        bridge.call_tool = AsyncMock(return_value=ToolCallOutcome(text="nope", is_error=True))
        resp = client.post(
            "/api/mcp/call",
            json={"tool": "bad"},
            headers={"X-RCFlow-MCP-Token": token},
        )
        assert resp.status_code == 200
        assert resp.json() == {"content": "nope", "is_error": True}

    def test_arguments_default_empty(self, client: TestClient, bridge: MagicMock, token: str) -> None:
        resp = client.post(
            "/api/mcp/call",
            json={"tool": "system_info"},
            headers={"X-RCFlow-MCP-Token": token},
        )
        assert resp.status_code == 200
        bridge.call_tool.assert_awaited_once_with("sess-1", "system_info", {})
