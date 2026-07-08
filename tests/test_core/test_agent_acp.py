"""Tests for the AcpAgent collaborator (src/core/agent_acp.py)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.core.agent_acp as agent_acp_mod
from src.core.agent_acp import AcpAgent, resolve_mcp_proxy_command, worker_loopback_url
from src.core.buffer import MessageType
from src.core.permissions import PermissionDecision
from src.core.session import ActiveSession, SessionType
from src.executors.base import ExecutionChunk


def _session() -> ActiveSession:
    return ActiveSession("sid-1", SessionType.LONG_RUNNING)


def _router() -> MagicMock:
    router = MagicMock()
    router._handle_permission_check = AsyncMock(return_value=PermissionDecision.ALLOW)
    router._mcp_bridge = MagicMock()
    router._mcp_bridge.tokens.issue.return_value = "tok-abc"
    router._settings = MagicMock(RCFLOW_PORT=53890, WSS_ENABLED=False, SSL_CERTFILE="", SSL_KEYFILE="")
    router._session_manager = None
    return router


def _buffer_types(session: ActiveSession) -> list[str]:
    return [m.message_type for m in session.buffer.text_history]


async def _chunks(events: list[dict[str, Any]]):
    for e in events:
        yield ExecutionChunk(stream="stdout", content=json.dumps(e))


class TestWorkerLoopbackUrl:
    def test_http_when_no_tls(self) -> None:
        settings = MagicMock(RCFLOW_PORT=1234, WSS_ENABLED=False, SSL_CERTFILE="", SSL_KEYFILE="")
        assert worker_loopback_url(settings) == "http://127.0.0.1:1234"

    def test_https_when_wss_enabled(self) -> None:
        settings = MagicMock(RCFLOW_PORT=1234, WSS_ENABLED=True, SSL_CERTFILE="", SSL_KEYFILE="")
        assert worker_loopback_url(settings) == "https://127.0.0.1:1234"

    def test_https_when_explicit_certs(self) -> None:
        settings = MagicMock(RCFLOW_PORT=1234, WSS_ENABLED=False, SSL_CERTFILE="/c.pem", SSL_KEYFILE="/k.pem")
        assert worker_loopback_url(settings) == "https://127.0.0.1:1234"


class TestResolveMcpProxyCommand:
    def test_frozen_uses_rcflow_subcommand(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.paths.is_frozen", lambda: True)
        monkeypatch.setattr(agent_acp_mod.sys, "executable", "/opt/rcflow/rcflow")
        assert resolve_mcp_proxy_command() == ("/opt/rcflow/rcflow", ["mcp-proxy"])

    def test_venv_uses_console_script(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.setattr("src.paths.is_frozen", lambda: False)
        (tmp_path / "rcflow-mcp").write_text("#!/bin/sh\n")
        monkeypatch.setattr(agent_acp_mod.sys, "executable", str(tmp_path / "python"))
        assert resolve_mcp_proxy_command() == (str(tmp_path / "rcflow-mcp"), [])

    def test_missing_everywhere_returns_none(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        monkeypatch.setattr("src.paths.is_frozen", lambda: False)
        monkeypatch.setattr(agent_acp_mod.sys, "executable", str(tmp_path / "python"))
        assert resolve_mcp_proxy_command() is None


class TestPermissionCallback:
    @pytest.mark.asyncio
    async def test_allow_selects_allow_option(self) -> None:
        router = _router()
        agent = AcpAgent(router)
        session = _session()
        cb = agent._make_permission_callback(session)

        chosen = await cb(
            {"title": "write", "raw_input": {"a": 1}},
            [
                {"option_id": "opt-allow", "name": "Allow", "kind": "allow_once"},
                {"option_id": "opt-reject", "name": "Reject", "kind": "reject_once"},
            ],
        )

        assert chosen == "opt-allow"
        router._handle_permission_check.assert_awaited_once_with(session, "write", {"a": 1})
        assert session.permission_manager is not None

    @pytest.mark.asyncio
    async def test_deny_selects_reject_option(self) -> None:
        router = _router()
        router._handle_permission_check = AsyncMock(return_value=PermissionDecision.DENY)
        agent = AcpAgent(router)
        cb = agent._make_permission_callback(_session())

        chosen = await cb(
            {"title": "rm"},
            [
                {"option_id": "opt-allow", "name": "Allow", "kind": "allow_once"},
                {"option_id": "opt-reject", "name": "Reject", "kind": "reject_once"},
            ],
        )
        assert chosen == "opt-reject"

    @pytest.mark.asyncio
    async def test_deny_without_reject_option_cancels(self) -> None:
        router = _router()
        router._handle_permission_check = AsyncMock(return_value=PermissionDecision.DENY)
        agent = AcpAgent(router)
        cb = agent._make_permission_callback(_session())

        chosen = await cb({"title": "rm"}, [{"option_id": "opt-allow", "name": "Allow", "kind": "allow_once"}])
        assert chosen is None


class TestMcpServersParam:
    def test_enabled_builds_param_with_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        router = _router()
        router._get_managed_config_overrides.return_value = {"expose_rcflow_tools": True}
        agent = AcpAgent(router)
        session = _session()
        monkeypatch.setattr("src.core.agent_acp.Path.is_file", lambda self: True)

        servers = agent._build_mcp_servers_param(session, "opencode")

        assert len(servers) == 1
        assert servers[0]["name"] == "rcflow"
        env = {e["name"]: e["value"] for e in servers[0]["env"]}
        assert env["RCFLOW_MCP_TOKEN"] == "tok-abc"
        assert env["RCFLOW_MCP_URL"] == "http://127.0.0.1:53890"
        router._mcp_bridge.tokens.issue.assert_called_once_with("sid-1")

    def test_disabled_returns_empty(self) -> None:
        router = _router()
        router._get_managed_config_overrides.return_value = {}
        agent = AcpAgent(router)
        assert agent._build_mcp_servers_param(_session(), "opencode") == []
        router._mcp_bridge.tokens.issue.assert_not_called()

    def test_no_bridge_returns_empty(self) -> None:
        router = _router()
        router._mcp_bridge = None
        agent = AcpAgent(router)
        assert agent._build_mcp_servers_param(_session(), "opencode") == []


class TestRelay:
    @pytest.mark.asyncio
    async def test_full_turn_translation(self) -> None:
        router = _router()
        agent = AcpAgent(router)
        session = _session()
        session.metadata["acp_working_directory"] = "/repo"

        completed = await agent._relay_acp_stream(
            session,
            _chunks(
                [
                    {"type": "thought", "text": "thinking"},
                    {"type": "text", "text": "working on it"},
                    {
                        "type": "tool_call",
                        "title": "write",
                        "raw_input": {"filePath": "/repo/x.txt"},
                        "locations": ["/repo/x.txt"],
                    },
                    {
                        "type": "tool_call_update",
                        "title": "write",
                        "status": "completed",
                        "content_texts": ["wrote it"],
                        "locations": [],
                    },
                    {"type": "plan", "entries": [{"content": "step", "status": "pending"}]},
                    {"type": "usage", "used": 5, "size": 10, "cost_amount": 0.1, "cost_currency": "USD"},
                    {"type": "available_commands", "commands": [{"name": "c", "description": "d"}]},
                    {"type": "turn_end", "stop_reason": "end_turn"},
                ]
            ),
        )

        assert completed
        types = _buffer_types(session)
        assert MessageType.THINKING in types
        assert MessageType.TEXT_CHUNK in types
        assert MessageType.TOOL_START in types
        assert MessageType.TOOL_OUTPUT in types
        assert MessageType.TODO_UPDATE in types
        assert session.todos == [{"content": "step", "status": "pending"}]
        assert session.metadata["acp_usage"]["used"] == 5
        assert session.metadata["acp_available_commands"] == [{"name": "c", "description": "d"}]
        router._fire_summary_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_error_event_pushes_error(self) -> None:
        router = _router()
        agent = AcpAgent(router)
        session = _session()

        completed = await agent._relay_acp_stream(session, _chunks([{"type": "error", "message": "boom"}]))

        assert not completed
        errors = [m for m in session.buffer.text_history if m.message_type == MessageType.ERROR]
        assert errors and "boom" in errors[0].data["content"]

    @pytest.mark.asyncio
    async def test_cancelled_turn_completes_without_summary(self) -> None:
        router = _router()
        agent = AcpAgent(router)
        session = _session()

        completed = await agent._relay_acp_stream(session, _chunks([{"type": "turn_end", "stop_reason": "cancelled"}]))

        assert completed
        router._fire_summary_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_refusal_stop_reason_surfaces_error(self) -> None:
        router = _router()
        agent = AcpAgent(router)
        session = _session()

        completed = await agent._relay_acp_stream(session, _chunks([{"type": "turn_end", "stop_reason": "refusal"}]))

        assert completed
        errors = [m for m in session.buffer.text_history if m.message_type == MessageType.ERROR]
        assert errors and "refusal" in errors[0].data["content"]


class TestEndSession:
    @pytest.mark.asyncio
    async def test_end_revokes_tokens_and_completes(self) -> None:
        router = _router()
        agent = AcpAgent(router)
        session = _session()
        executor = MagicMock()
        executor.stop_process = AsyncMock()
        session.acp_executor = executor

        await agent._end_acp_session(session)

        executor.stop_process.assert_awaited_once()
        assert session.acp_executor is None
        router._mcp_bridge.tokens.revoke_session.assert_called_once_with("sid-1")
        ends = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_END]
        assert ends and ends[0].data["reason"] == "acp_finished"
