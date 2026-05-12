"""Tests for Claude Code ``Monitor`` tool integration in ClaudeCodeAgentMixin.

Covers:
- ``_is_monitor_terminal`` — terminal vs. intermediate event detection.
- ``_classify_monitor_termination`` — reason + exit code parsing.
- Integration: Monitor tool_use + N intermediate events + terminal event.
- Integration: ``is_error=True`` always closes a monitor.
- Integration: two concurrent monitors keep separate state.
- Integration: Edit interleaved with Monitor — diff still computed correctly.
- Integration: ``_terminate_active_monitors`` closes leftovers on session end.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from src.core.agent_claude_code import (
    ClaudeCodeAgentMixin,
    _classify_monitor_termination,
    _is_monitor_terminal,
)
from src.core.buffer import MessageType
from src.core.session import MonitorState, SessionStatus

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Pure helper unit tests
# ---------------------------------------------------------------------------


class TestIsMonitorTerminal:
    def test_intermediate_event_is_not_terminal(self) -> None:
        assert _is_monitor_terminal("ERROR: connection refused", False) is False

    def test_is_error_forces_terminal(self) -> None:
        assert _is_monitor_terminal("ERROR: connection refused", True) is True

    def test_exit_summary_prefix_is_terminal(self) -> None:
        assert _is_monitor_terminal("Monitor exited with exit code 0", False) is True

    def test_timeout_summary_is_terminal(self) -> None:
        assert _is_monitor_terminal("Monitor timed out after 300s", False) is True

    def test_stopped_summary_is_terminal(self) -> None:
        assert _is_monitor_terminal("Monitor stopped by TaskStop", False) is True

    def test_case_insensitive(self) -> None:
        assert _is_monitor_terminal("MONITOR EXITED with exit code 1", False) is True


class TestClassifyMonitorTermination:
    def test_exit_with_code(self) -> None:
        assert _classify_monitor_termination("Monitor exited with exit code 0", False) == ("exit", 0)

    def test_exit_nonzero(self) -> None:
        assert _classify_monitor_termination("Monitor exited with exit code 7", False) == ("exit", 7)

    def test_timeout(self) -> None:
        reason, code = _classify_monitor_termination("Monitor timed out after 300s", False)
        assert reason == "timeout"
        assert code is None

    def test_cancelled(self) -> None:
        reason, _ = _classify_monitor_termination("Monitor stopped by TaskStop", False)
        assert reason == "cancelled"

    def test_error_fallback(self) -> None:
        reason, _ = _classify_monitor_termination("connection refused", True)
        assert reason == "error"


# ---------------------------------------------------------------------------
# Integration helpers (mirror test_agent_diff.py)
# ---------------------------------------------------------------------------


def _make_session(tmp_path: Path) -> MagicMock:
    session = MagicMock()
    session.id = "test-session"
    session.subprocess_working_directory = str(tmp_path)
    session._pending_snapshots = []
    session._active_monitors = {}
    session.permission_manager = None
    session.subprocess_current_tool = None
    session.subprocess_started_at = None
    session.subprocess_type = None
    session.subprocess_display_name = None
    session.buffer = MagicMock()
    session.buffer.push_text = MagicMock()
    session.buffer.push_ephemeral = MagicMock()
    return session


def _make_executor_chunks(events: list[dict]):
    async def _gen():
        for e in events:
            chunk = MagicMock()
            chunk.content = json.dumps(e)
            yield chunk

    return _gen()


def _make_mixin() -> ClaudeCodeAgentMixin:
    mixin = ClaudeCodeAgentMixin.__new__(ClaudeCodeAgentMixin)
    mixin._tool_settings = None
    mixin._fire_text_artifact_scan = MagicMock()  # type: ignore[attr-defined]
    return mixin


def _types(session: MagicMock) -> list[str]:
    return [c[0][0].name for c in session.buffer.push_text.call_args_list]


def _payloads_of(session: MagicMock, mt: MessageType) -> list[dict]:
    return [c[0][1] for c in session.buffer.push_text.call_args_list if c[0][0] == mt]


# ---------------------------------------------------------------------------
# Integration tests
# ---------------------------------------------------------------------------


class TestMonitorIntegration:
    @pytest.mark.asyncio
    async def test_single_event_then_exit(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "mon1",
                            "name": "Monitor",
                            "input": {
                                "description": "errors in deploy.log",
                                "command": "tail -f deploy.log",
                                "timeout_ms": 60_000,
                                "persistent": False,
                            },
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "mon1",
                            "content": "ERROR: connection refused\n",
                            "is_error": False,
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "mon1",
                            "content": "Monitor exited with exit code 0",
                            "is_error": False,
                        }
                    ]
                },
            },
        ]

        session = _make_session(tmp_path)
        mixin = _make_mixin()
        await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        types = _types(session)
        assert "MONITOR_START" in types
        assert types.count("MONITOR_EVENT") == 1
        assert types.count("MONITOR_END") == 1
        assert "TOOL_OUTPUT" not in types  # never routed through normal path

        end = _payloads_of(session, MessageType.MONITOR_END)[0]
        assert end["reason"] == "exit"
        assert end["exit_code"] == 0
        assert end["total_events"] == 1
        assert end["monitor_id"] == "mon1"

    @pytest.mark.asyncio
    async def test_is_error_closes_monitor(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "mon1",
                            "name": "Monitor",
                            "input": {"description": "x", "command": "true", "timeout_ms": 1000, "persistent": False},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "mon1",
                            "content": "boom",
                            "is_error": True,
                        }
                    ]
                },
            },
        ]
        session = _make_session(tmp_path)
        mixin = _make_mixin()
        await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        types = _types(session)
        assert types.count("MONITOR_EVENT") == 0
        assert types.count("MONITOR_END") == 1
        end = _payloads_of(session, MessageType.MONITOR_END)[0]
        assert end["reason"] == "error"

    @pytest.mark.asyncio
    async def test_two_monitors_route_independently(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "monA",
                            "name": "Monitor",
                            "input": {"description": "A", "command": "a", "timeout_ms": 1000, "persistent": False},
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "monB",
                            "name": "Monitor",
                            "input": {"description": "B", "command": "b", "timeout_ms": 1000, "persistent": False},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "monA",
                            "content": "A-line",
                            "is_error": False,
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "monB",
                            "content": "B-line",
                            "is_error": False,
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "monA",
                            "content": "Monitor exited with exit code 0",
                            "is_error": False,
                        }
                    ]
                },
            },
        ]
        session = _make_session(tmp_path)
        mixin = _make_mixin()
        await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        # monA closed; monB still live
        assert "monA" not in session._active_monitors
        assert "monB" in session._active_monitors

        events_payloads = _payloads_of(session, MessageType.MONITOR_EVENT)
        assert {p["monitor_id"] for p in events_payloads} == {"monA", "monB"}
        ends = _payloads_of(session, MessageType.MONITOR_END)
        assert len(ends) == 1
        assert ends[0]["monitor_id"] == "monA"

    @pytest.mark.asyncio
    async def test_monitor_does_not_consume_pending_snapshots(self, tmp_path: Path) -> None:
        """Edit's pre/post snapshot stack must not be polluted by Monitor's tool_results."""
        target = tmp_path / "f.py"
        target.write_text("a\n", encoding="utf-8")

        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "mon1",
                            "name": "Monitor",
                            "input": {"description": "x", "command": "true", "timeout_ms": 1000, "persistent": False},
                        }
                    ]
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "edit1",
                            "name": "Edit",
                            "input": {"file_path": str(target)},
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "mon1",
                            "content": "log line",
                            "is_error": False,
                        }
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "edit1",
                            "content": "File edited.",
                            "is_error": False,
                        }
                    ]
                },
            },
        ]

        session = _make_session(tmp_path)
        mixin = _make_mixin()

        # Mock file snapshot helper to simulate file change
        call_count = 0

        async def _mock_read(path):
            nonlocal call_count
            call_count += 1
            return "before\n" if call_count == 1 else "after\n"

        with patch("src.core.agent_claude_code._read_file_snapshot", _mock_read):
            await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        # The monitor block should still be live (no terminal yet)
        assert "mon1" in session._active_monitors

        # Edit's TOOL_OUTPUT must carry a diff — proves snapshot stack alignment
        tool_output_calls = _payloads_of(session, MessageType.TOOL_OUTPUT)
        assert len(tool_output_calls) == 1
        assert "diff" in tool_output_calls[0]
        assert "+after" in tool_output_calls[0]["diff"]

    @pytest.mark.asyncio
    async def test_terminate_active_monitors_helper(self, tmp_path: Path) -> None:
        """``_terminate_active_monitors`` flushes all remaining live monitors."""
        session = _make_session(tmp_path)
        session._active_monitors = {
            "m1": MonitorState("d1", "c1", 1000, False, datetime.now(UTC), event_count=3),
            "m2": MonitorState("d2", "c2", 1000, True, datetime.now(UTC), event_count=0),
        }
        mixin = _make_mixin()
        await mixin._terminate_active_monitors(session, reason="session_end")

        ends = _payloads_of(session, MessageType.MONITOR_END)
        assert len(ends) == 2
        assert {p["monitor_id"] for p in ends} == {"m1", "m2"}
        assert all(p["reason"] == "session_end" for p in ends)
        assert session._active_monitors == {}


# ---------------------------------------------------------------------------
# Drain phase tests — covers the post-"result" reader keep-alive that
# delivers Monitor terminal events emitted between user turns.
# ---------------------------------------------------------------------------


class TestDrainMonitorEvents:
    """Verify ``_drain_monitor_events`` keeps reading until monitors close.

    Without the drain, Claude Code's deferred Monitor tool_result blocks
    sit in the OS pipe buffer between turns and the client never sees
    MONITOR_END.  These tests check the drain loop directly without
    spawning a real subprocess.
    """

    @pytest.mark.asyncio
    async def test_no_drain_when_no_monitors(self, tmp_path: Path) -> None:
        """Drain is a no-op when no monitors are tracked."""
        session = _make_session(tmp_path)
        session.status = SessionStatus.ACTIVE
        session._active_monitors = {}
        executor = MagicMock()
        executor.is_running = True
        executor.read_more_events = MagicMock()

        mixin = _make_mixin()
        await mixin._drain_monitor_events(session, executor)

        executor.read_more_events.assert_not_called()

    @pytest.mark.asyncio
    async def test_drain_loops_until_monitors_close(self, tmp_path: Path) -> None:
        """Drain calls ``read_more_events`` repeatedly until monitors empty."""
        session = _make_session(tmp_path)
        session.status = SessionStatus.ACTIVE
        session._active_monitors = {
            "m1": MonitorState("d", "c", 1000, False, datetime.now(UTC)),
        }

        # Mock relay so it pretends to clear the monitor on the first call,
        # mimicking a MONITOR_END being processed mid-read.
        call_count = {"n": 0}

        async def fake_relay(s, _stream):
            call_count["n"] += 1
            s._active_monitors.clear()

        executor = MagicMock()
        executor.is_running = True
        executor.got_result = True
        executor.read_more_events = MagicMock(return_value=MagicMock())

        mixin = _make_mixin()
        with patch.object(mixin, "_relay_claude_code_stream", side_effect=fake_relay):
            await mixin._drain_monitor_events(session, executor)

        assert call_count["n"] == 1
        assert session._active_monitors == {}

    @pytest.mark.asyncio
    async def test_drain_stops_on_process_exit_and_terminates_leftovers(self, tmp_path: Path) -> None:
        """If CC dies mid-drain with monitors live, leftover are flushed."""
        session = _make_session(tmp_path)
        session.status = SessionStatus.ACTIVE
        session._active_monitors = {
            "m1": MonitorState("d", "c", 1000, False, datetime.now(UTC), event_count=2),
        }

        async def fake_relay(_s, _stream):
            return

        # Process becomes "not running" after the first relay call.
        running_state = {"alive": True}

        def is_running_get():
            value = running_state["alive"]
            running_state["alive"] = False
            return value

        executor = MagicMock()
        type(executor).is_running = property(lambda _self: is_running_get())
        executor.got_result = False  # mimics abnormal exit
        executor.read_more_events = MagicMock(return_value=MagicMock())

        mixin = _make_mixin()
        with patch.object(mixin, "_relay_claude_code_stream", side_effect=fake_relay):
            await mixin._drain_monitor_events(session, executor)

        # Leftover monitor was force-ended.
        ends = _payloads_of(session, MessageType.MONITOR_END)
        assert len(ends) == 1
        assert ends[0]["reason"] == "executor_exit"
        assert session._active_monitors == {}
