"""Integration tests for AcpExecutor against the scripted fake ACP agent.

The fake agent (tests/fixtures/fake_acp_agent.py) speaks raw JSON-RPC over
stdio, so these tests exercise the executor's real protocol path: spawn,
initialize, session/new (with mcp_servers), prompt turns, permission
round-trips, resume via session/load, and teardown.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from src.executors.acp import AcpExecutor
from src.tools.loader import ToolDefinition

FAKE_AGENT = str(Path(__file__).parent.parent / "fixtures" / "fake_acp_agent.py")

TOOL_DEF = ToolDefinition(
    name="fake_acp",
    description="fake",
    session_type="long-running",
    llm_context="session-scoped",
    executor="acp",
    parameters={"type": "object", "properties": {}},
    executor_config={"acp": {"binary_path": sys.executable, "args": [FAKE_AGENT]}},
)


def _executor(**kwargs: Any) -> AcpExecutor:
    return AcpExecutor(binary_path=sys.executable, args=[FAKE_AGENT], **kwargs)


async def _run(executor: AcpExecutor, prompt: str, *, follow_up: bool = False) -> list[dict[str, Any]]:
    events = []
    stream = (
        executor.restart_with_prompt(prompt)
        if follow_up
        else executor.execute_streaming(TOOL_DEF, {"prompt": prompt, "working_directory": "."})
    )
    async for chunk in stream:
        events.append(json.loads(chunk.content))
    return events


@pytest.fixture
async def executor():
    ex = _executor()
    yield ex
    await ex.stop_process()


class TestPromptTurn:
    @pytest.mark.asyncio
    async def test_basic_turn(self, executor: AcpExecutor) -> None:
        events = await _run(executor, "hello")
        types = [e["type"] for e in events]
        assert types[0] == "text"
        assert events[0]["text"] == "hello from fake agent"
        assert types[-1] == "turn_end"
        assert events[-1]["stop_reason"] == "end_turn"
        assert executor.got_result
        assert executor.acp_session_id == "fake-sess-1"
        assert executor.is_running

    @pytest.mark.asyncio
    async def test_all_update_kinds_flow(self, executor: AcpExecutor) -> None:
        events = await _run(executor, "EMIT_ALL")
        types = {e["type"] for e in events}
        assert {"text", "thought", "plan", "usage", "available_commands", "turn_end"} <= types
        usage = next(e for e in events if e["type"] == "usage")
        assert usage["used"] == 123
        assert usage["cost_amount"] == 0.5

    @pytest.mark.asyncio
    async def test_tool_call_lifecycle(self, executor: AcpExecutor) -> None:
        events = await _run(executor, "USE_TOOL")
        tool_events = [e for e in events if e["type"].startswith("tool_call")]
        assert tool_events[0]["status"] == "pending"
        assert tool_events[0]["locations"] == ["/tmp/fake.txt"]
        assert tool_events[-1]["status"] == "completed"
        assert tool_events[-1]["content_texts"] == ["wrote the file"]

    @pytest.mark.asyncio
    async def test_follow_up_turn_same_process(self, executor: AcpExecutor) -> None:
        await _run(executor, "hello")
        events = await _run(executor, "again", follow_up=True)
        assert events[-1]["stop_reason"] == "end_turn"
        assert executor.acp_session_id == "fake-sess-1"  # same ACP session

    @pytest.mark.asyncio
    async def test_failed_turn_yields_error(self, executor: AcpExecutor) -> None:
        events = await _run(executor, "FAIL_TURN")
        assert events[-1]["type"] == "error"
        assert "scripted failure" in events[-1]["message"]
        assert not executor.got_result


class TestPermissions:
    @pytest.mark.asyncio
    async def test_permission_round_trip_allow(self, executor: AcpExecutor) -> None:
        seen: dict[str, Any] = {}

        async def allow(tool_call: dict[str, Any], options: list[dict[str, Any]]) -> str | None:
            seen["tool_call"] = tool_call
            seen["options"] = options
            return "opt-allow"

        executor.set_permission_callback(allow)
        events = await _run(executor, "ASK_PERM")

        assert seen["tool_call"]["title"] == "dangerous_tool"
        assert seen["tool_call"]["raw_input"] == {"arg": 1}
        assert [o["option_id"] for o in seen["options"]] == ["opt-allow", "opt-reject"]
        perm_echo = next(e for e in events if e["type"] == "text" and e["text"].startswith("PERM:"))
        outcome = json.loads(perm_echo["text"][len("PERM:") :])
        assert outcome == {"outcome": {"outcome": "selected", "optionId": "opt-allow"}}

    @pytest.mark.asyncio
    async def test_permission_cancel(self, executor: AcpExecutor) -> None:
        async def deny(tool_call: dict[str, Any], options: list[dict[str, Any]]) -> str | None:
            return None

        executor.set_permission_callback(deny)
        events = await _run(executor, "ASK_PERM")
        perm_echo = next(e for e in events if e["type"] == "text" and e["text"].startswith("PERM:"))
        outcome = json.loads(perm_echo["text"][len("PERM:") :])
        assert outcome == {"outcome": {"outcome": "cancelled"}}

    @pytest.mark.asyncio
    async def test_no_callback_fails_closed(self, executor: AcpExecutor) -> None:
        events = await _run(executor, "ASK_PERM")
        perm_echo = next(e for e in events if e["type"] == "text" and e["text"].startswith("PERM:"))
        outcome = json.loads(perm_echo["text"][len("PERM:") :])
        assert outcome == {"outcome": {"outcome": "cancelled"}}


class TestMcpPassthrough:
    @pytest.mark.asyncio
    async def test_mcp_servers_reach_agent(self) -> None:
        executor = _executor(
            mcp_servers=[
                {
                    "name": "rcflow",
                    "command": "/bin/rcflow-mcp",
                    "args": [],
                    "env": [{"name": "RCFLOW_MCP_TOKEN", "value": "tok-1"}],
                }
            ]
        )
        try:
            events = await _run(executor, "SHOW_MCP")
            echo = next(e for e in events if e["type"] == "text" and e["text"].startswith("MCP:"))
            servers = json.loads(echo["text"][len("MCP:") :])
            assert servers[0]["name"] == "rcflow"
            assert servers[0]["env"] == [{"name": "RCFLOW_MCP_TOKEN", "value": "tok-1"}]
        finally:
            await executor.stop_process()


class TestResume:
    @pytest.mark.asyncio
    async def test_resume_uses_session_load(self) -> None:
        executor = _executor()
        try:
            executor.set_resume_target("fake-sess-99")
            executor._cwd = "."
            events = await _run(executor, "after resume", follow_up=True)
            # The fake agent replays history on session/load before the turn.
            replay = [e for e in events if e["type"] == "text" and e["text"].startswith("REPLAY:")]
            assert replay, f"expected replay event, got {events}"
            assert executor.acp_session_id == "fake-sess-99"
        finally:
            await executor.stop_process()


class TestTeardown:
    @pytest.mark.asyncio
    async def test_stop_process_kills_agent(self) -> None:
        executor = _executor()
        await _run(executor, "hello")
        assert executor.is_running
        await executor.stop_process()
        assert not executor.is_running

    @pytest.mark.asyncio
    async def test_spawn_failure_yields_error(self) -> None:
        executor = AcpExecutor(binary_path="/nonexistent/agent-binary")
        events = await _run(executor, "hello")
        assert events[-1]["type"] == "error"
        assert "failed to start" in events[-1]["message"]
