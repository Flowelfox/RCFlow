"""Tests for the rcflow-mcp stdio proxy (src/mcp_proxy.py).

The JSON-RPC loop is exercised end-to-end with a fake stdin and a patched
worker-request function — no sockets, no subprocesses.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from src import mcp_proxy


@pytest.fixture
def responses(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Run the loop against fake stdin; collect every JSON-RPC response written."""
    out: list[dict[str, Any]] = []
    monkeypatch.setattr(mcp_proxy, "_write_message", out.append)
    return out


def _serve(lines: list[dict[str, Any] | str]) -> None:
    text = "\n".join(m if isinstance(m, str) else json.dumps(m) for m in lines) + "\n"
    mcp_proxy.serve(stdin=io.StringIO(text))


def _by_id(responses: list[dict[str, Any]], request_id: Any) -> dict[str, Any]:
    return next(r for r in responses if r.get("id") == request_id)


class TestProtocol:
    def test_initialize_echoes_client_protocol_version(self, responses: list[dict[str, Any]]) -> None:
        _serve([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}])
        result = _by_id(responses, 1)["result"]
        assert result["protocolVersion"] == "2025-03-26"
        assert result["serverInfo"]["name"] == "rcflow"
        assert "tools" in result["capabilities"]

    def test_notification_gets_no_response(self, responses: list[dict[str, Any]]) -> None:
        _serve([{"jsonrpc": "2.0", "method": "notifications/initialized"}])
        assert responses == []

    def test_unknown_method_error(self, responses: list[dict[str, Any]]) -> None:
        _serve([{"jsonrpc": "2.0", "id": 5, "method": "resources/list"}])
        assert _by_id(responses, 5)["error"]["code"] == -32601

    def test_parse_error(self, responses: list[dict[str, Any]]) -> None:
        _serve(["{not json"])
        assert responses[0]["error"]["code"] == -32700

    def test_ping(self, responses: list[dict[str, Any]]) -> None:
        _serve([{"jsonrpc": "2.0", "id": 9, "method": "ping"}])
        assert _by_id(responses, 9)["result"] == {}


class TestToolsProxying:
    def test_tools_list_proxies_to_worker(
        self, responses: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[tuple[str, str]] = []

        def fake_request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
            seen.append((method, path))
            return {"tools": [{"name": "system_info", "description": "d", "inputSchema": {}}]}

        monkeypatch.setattr(mcp_proxy, "_worker_request", fake_request)
        _serve([{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])

        assert seen == [("GET", "/api/mcp/tools")]
        assert _by_id(responses, 2)["result"]["tools"][0]["name"] == "system_info"

    def test_tools_call_proxies_and_maps_result(
        self, responses: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
            assert (method, path) == ("POST", "/api/mcp/call")
            assert body == {"tool": "system_info", "arguments": {"category": "os"}}
            return {"content": "linux stuff", "is_error": False}

        monkeypatch.setattr(mcp_proxy, "_worker_request", fake_request)
        _serve(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "system_info", "arguments": {"category": "os"}},
                }
            ]
        )

        result = _by_id(responses, 3)["result"]
        assert result == {"content": [{"type": "text", "text": "linux stuff"}], "isError": False}

    def test_worker_error_becomes_in_band_tool_error(
        self, responses: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
            raise RuntimeError("RCFlow worker returned 401: Invalid MCP bridge token")

        monkeypatch.setattr(mcp_proxy, "_worker_request", fake_request)
        _serve([{"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "x"}}])

        result = _by_id(responses, 4)["result"]
        assert result["isError"] is True
        assert "401" in result["content"][0]["text"]

    def test_worker_error_on_list_is_rpc_error(
        self, responses: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
            raise RuntimeError("RCFlow worker unreachable")

        monkeypatch.setattr(mcp_proxy, "_worker_request", fake_request)
        _serve([{"jsonrpc": "2.0", "id": 6, "method": "tools/list"}])
        assert "unreachable" in _by_id(responses, 6)["error"]["message"]


class TestMain:
    def test_refuses_to_start_without_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RCFLOW_MCP_TOKEN", raising=False)
        with pytest.raises(SystemExit) as exc:
            mcp_proxy.main()
        assert exc.value.code == 2
