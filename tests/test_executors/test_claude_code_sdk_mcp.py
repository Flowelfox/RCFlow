"""Tests for the in-process rcflow MCP server wiring in ClaudeCodeSdkExecutor."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.executors.claude_code_sdk import ClaudeCodeSdkExecutor
from src.services.mcp_bridge import McpToolSpec, ToolCallOutcome

_SCHEMA = {"type": "object", "properties": {"category": {"type": "string"}}, "required": ["category"]}


def _bridge(specs: list[McpToolSpec] | None = None) -> MagicMock:
    bridge = MagicMock()
    bridge.list_agent_tools.return_value = (
        specs if specs is not None else [McpToolSpec("system_info", "System info", _SCHEMA)]
    )
    bridge.call_tool = AsyncMock(return_value=ToolCallOutcome(text="ok", is_error=False))
    return bridge


class TestBuildOptions:
    def test_mcp_server_present_when_enabled(self) -> None:
        executor = ClaudeCodeSdkExecutor(
            mcp_bridge=_bridge(),
            config_overrides={"expose_rcflow_tools": True},
        )
        options = executor._build_options({}, resume=None)
        assert "rcflow" in options.mcp_servers

    def test_no_server_when_setting_off(self) -> None:
        executor = ClaudeCodeSdkExecutor(mcp_bridge=_bridge(), config_overrides={})
        assert executor._build_options({}, resume=None).mcp_servers == {}

    def test_no_server_without_bridge(self) -> None:
        executor = ClaudeCodeSdkExecutor(config_overrides={"expose_rcflow_tools": True})
        assert executor._build_options({}, resume=None).mcp_servers == {}

    def test_no_server_when_no_tools_exposed(self) -> None:
        executor = ClaudeCodeSdkExecutor(
            mcp_bridge=_bridge(specs=[]),
            config_overrides={"expose_rcflow_tools": True},
        )
        assert executor._build_options({}, resume=None).mcp_servers == {}


class TestServerContents:
    def test_tools_built_from_bridge_specs(self) -> None:
        """Registry-driven: every bridge spec becomes an SDK tool, schema verbatim."""
        specs = [
            McpToolSpec("system_info", "System info", _SCHEMA),
            McpToolSpec("weather", "Weather lookup", {"type": "object"}),
        ]
        executor = ClaudeCodeSdkExecutor(
            mcp_bridge=_bridge(specs=specs),
            config_overrides={"expose_rcflow_tools": True},
        )
        sdk_tools = executor._build_rcflow_sdk_tools()
        assert [t.name for t in sdk_tools] == ["system_info", "weather"]
        assert [t.description for t in sdk_tools] == ["System info", "Weather lookup"]
        assert sdk_tools[0].input_schema == _SCHEMA

        config = executor._build_rcflow_mcp_server()
        assert config["type"] == "sdk"
        assert config["name"] == "rcflow"
        assert config["instance"] is not None

    @pytest.mark.asyncio
    async def test_handler_dispatches_to_bridge_with_session_id(self) -> None:
        """The real handler routes through the bridge, bound to the executor's session."""
        bridge = _bridge()
        executor = ClaudeCodeSdkExecutor(
            session_id="sess-42",
            mcp_bridge=bridge,
            config_overrides={"expose_rcflow_tools": True},
        )
        (sdk_tool,) = executor._build_rcflow_sdk_tools()

        result = await sdk_tool.handler({"category": "os"})

        bridge.call_tool.assert_awaited_once_with("sess-42", "system_info", {"category": "os"})
        assert result == {"content": [{"type": "text", "text": "ok"}], "is_error": False}

    @pytest.mark.asyncio
    async def test_handler_relays_bridge_error(self) -> None:
        bridge = _bridge()
        bridge.call_tool = AsyncMock(return_value=ToolCallOutcome(text="failed", is_error=True))
        executor = ClaudeCodeSdkExecutor(
            mcp_bridge=bridge,
            config_overrides={"expose_rcflow_tools": True},
        )
        (sdk_tool,) = executor._build_rcflow_sdk_tools()

        result = await sdk_tool.handler({})

        assert result == {"content": [{"type": "text", "text": "failed"}], "is_error": True}
