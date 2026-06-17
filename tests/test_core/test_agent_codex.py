"""Tests for CodexAgent stream relay and lifecycle methods.

Covers ``_relay_codex_stream`` event translation (thread.started, item.started,
item.updated, item.completed, turn.completed/failed, error, non-JSON), plus the
background streaming wrappers (``_stream_codex_events``,
``_restart_codex_with_prompt``) and ``_end_codex_session``.

Mirrors the fake-executor / mock-session style of the Claude Code agent tests.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.agent_codex import CodexAgent
from src.core.buffer import MessageType
from src.core.session import ActivityState, SessionStatus

if TYPE_CHECKING:
    from collections.abc import Iterable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session() -> MagicMock:
    """Minimal mock ActiveSession with the attributes the relay reads/writes."""
    session = MagicMock()
    session.id = "codex-session"
    session.metadata = {}
    session.codex_executor = MagicMock()
    session.subprocess_started_at = MagicMock()  # truthy → ephemeral status pushes
    session.subprocess_started_at_iso = "2026-06-16T00:00:00+00:00"
    session.subprocess_type = "codex"
    session.subprocess_display_name = "Codex"
    session.subprocess_working_directory = "/work"
    session.subprocess_current_tool = None
    session.tool_input_tokens = 0
    session.tool_output_tokens = 0
    session.status = SessionStatus.ACTIVE
    session._on_update = MagicMock()
    session.buffer = MagicMock()
    session.buffer.push_text = MagicMock()
    session.buffer.push_ephemeral = MagicMock()
    session.set_activity = MagicMock()
    return session


def _make_agent() -> CodexAgent:
    agent = CodexAgent.__new__(CodexAgent)
    agent._r = MagicMock()
    agent._r._fire_text_artifact_scan = MagicMock()
    agent._r._fire_summary_task = MagicMock()
    agent._r._fire_task_update_task = MagicMock()
    agent._r._session_manager = None
    agent._r.schedule_pending_drain = MagicMock()
    return agent


def _chunks(events: Iterable[dict | str]):
    """Async generator yielding ExecutionChunk-like objects."""

    async def _gen():
        for e in events:
            chunk = MagicMock()
            chunk.content = e if isinstance(e, str) else json.dumps(e)
            yield chunk

    return _gen()


def _pushes(session: MagicMock, msg_type: MessageType) -> list[dict]:
    return [c[0][1] for c in session.buffer.push_text.call_args_list if c[0][0] == msg_type]


# ---------------------------------------------------------------------------
# _relay_codex_stream
# ---------------------------------------------------------------------------


class TestRelayCodexStream:
    @pytest.mark.asyncio
    async def test_thread_started_persists_thread_id(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_codex_stream(session, _chunks([{"type": "thread.started", "thread_id": "th-1"}]))
        assert session.metadata["codex_thread_id"] == "th-1"

    @pytest.mark.asyncio
    async def test_blank_and_non_dict_lines_skipped(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_codex_stream(session, _chunks(["", "   ", "[1, 2, 3]"]))
        assert session.buffer.push_text.call_count == 0

    @pytest.mark.asyncio
    async def test_non_json_line_becomes_text_chunk(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_codex_stream(session, _chunks(["not json at all"]))
        texts = _pushes(session, MessageType.TEXT_CHUNK)
        assert len(texts) == 1
        assert texts[0]["content"] == "not json at all"

    @pytest.mark.asyncio
    async def test_command_execution_start_and_completed(self) -> None:
        session = _make_session()
        agent = _make_agent()
        events = [
            {"type": "item.started", "item": {"type": "command_execution", "command": "ls -la"}},
            {
                "type": "item.completed",
                "item": {"type": "command_execution", "aggregated_output": "file.txt", "exit_code": 0},
            },
        ]
        await agent._relay_codex_stream(session, _chunks(events))

        starts = _pushes(session, MessageType.TOOL_START)
        assert starts[0]["tool_name"] == "command_execution"
        assert starts[0]["tool_input"] == {"command": "ls -la"}

        outputs = _pushes(session, MessageType.TOOL_OUTPUT)
        assert outputs[0]["content"] == "file.txt"
        assert outputs[0]["is_error"] is False
        agent._r._fire_text_artifact_scan.assert_called()

    @pytest.mark.asyncio
    async def test_command_execution_nonzero_exit_is_error(self) -> None:
        session = _make_session()
        agent = _make_agent()
        events = [
            {
                "type": "item.completed",
                "item": {"type": "command_execution", "aggregated_output": "boom", "exit_code": 2},
            },
        ]
        await agent._relay_codex_stream(session, _chunks(events))
        outputs = _pushes(session, MessageType.TOOL_OUTPUT)
        assert outputs[0]["is_error"] is True

    @pytest.mark.asyncio
    async def test_file_change_start_and_completed_with_diff(self) -> None:
        session = _make_session()
        agent = _make_agent()
        events = [
            {"type": "item.started", "item": {"type": "file_change"}},
            {"type": "item.completed", "item": {"type": "file_change", "diff": "@@ -1 +1 @@\n-a\n+b"}},
        ]
        await agent._relay_codex_stream(session, _chunks(events))
        starts = _pushes(session, MessageType.TOOL_START)
        assert starts[0]["tool_name"] == "file_change"
        outputs = _pushes(session, MessageType.TOOL_OUTPUT)
        assert "+b" in outputs[0]["content"]

    @pytest.mark.asyncio
    async def test_file_change_completed_without_diff_uses_file_path(self) -> None:
        session = _make_session()
        agent = _make_agent()
        events = [
            {"type": "item.completed", "item": {"type": "file_change", "file_path": "src/x.py"}},
        ]
        await agent._relay_codex_stream(session, _chunks(events))
        outputs = _pushes(session, MessageType.TOOL_OUTPUT)
        assert outputs[0]["content"] == "File changed: src/x.py"

    @pytest.mark.asyncio
    async def test_mcp_tool_call_start_and_completed(self) -> None:
        session = _make_session()
        agent = _make_agent()
        events = [
            {
                "type": "item.started",
                "item": {"type": "mcp_tool_call", "server": "srv", "tool": "fetch", "arguments": {"q": "1"}},
            },
            {
                "type": "item.completed",
                "item": {"type": "mcp_tool_call", "server": "srv", "tool": "fetch", "output": {"ok": True}},
            },
        ]
        await agent._relay_codex_stream(session, _chunks(events))
        starts = _pushes(session, MessageType.TOOL_START)
        assert starts[0]["tool_name"] == "mcp:srv:fetch"
        assert starts[0]["tool_input"] == {"q": "1"}
        outputs = _pushes(session, MessageType.TOOL_OUTPUT)
        assert outputs[0]["tool_name"] == "mcp:srv:fetch"
        assert '"ok": true' in outputs[0]["content"]

    @pytest.mark.asyncio
    async def test_agent_message_incremental_then_completed(self) -> None:
        session = _make_session()
        agent = _make_agent()
        events = [
            {"type": "item.updated", "item": {"type": "agent_message", "id": "m1", "text": "Hello"}},
            {"type": "item.updated", "item": {"type": "agent_message", "id": "m1", "text": "Hello world"}},
            {"type": "item.completed", "item": {"type": "agent_message", "id": "m1", "text": "Hello world!"}},
        ]
        await agent._relay_codex_stream(session, _chunks(events))
        texts = [t["content"] for t in _pushes(session, MessageType.TEXT_CHUNK)]
        assert texts == ["Hello", " world", "!"]
        agent._r._fire_text_artifact_scan.assert_called_with(session, ["Hello world!"])

    @pytest.mark.asyncio
    async def test_turn_completed_sets_idle_and_fires_summary(self) -> None:
        session = _make_session()
        agent = _make_agent()
        events = [
            {"type": "item.completed", "item": {"type": "agent_message", "id": "m1", "text": "All done."}},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}},
        ]
        await agent._relay_codex_stream(session, _chunks(events))
        session.set_activity.assert_called_with(ActivityState.IDLE)
        assert session.tool_input_tokens == 10
        assert session.tool_output_tokens == 5
        session._on_update.assert_called()
        agent._r._fire_summary_task.assert_called_once()
        # Summary text comes from the post-tool agent text
        assert agent._r._fire_summary_task.call_args[0][1] == "All done."
        agent._r._fire_task_update_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_turn_completed_default_summary_when_no_text(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_codex_stream(session, _chunks([{"type": "turn.completed", "usage": {}}]))
        assert agent._r._fire_summary_task.call_args[0][1] == "Codex task completed"

    @pytest.mark.asyncio
    async def test_turn_failed_pushes_error(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_codex_stream(session, _chunks([{"type": "turn.failed", "error": {"message": "kaboom"}}]))
        errors = _pushes(session, MessageType.ERROR)
        assert errors[0]["content"] == "kaboom"
        assert errors[0]["code"] == "CODEX_TURN_FAILED"

    @pytest.mark.asyncio
    async def test_error_event_pushes_error(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_codex_stream(session, _chunks([{"type": "error", "message": "fatal"}]))
        errors = _pushes(session, MessageType.ERROR)
        assert errors[0]["content"] == "fatal"
        assert errors[0]["code"] == "CODEX_ERROR"

    @pytest.mark.asyncio
    async def test_unknown_event_type_skipped(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_codex_stream(session, _chunks([{"type": "mystery.event"}]))
        assert session.buffer.push_text.call_count == 0

    @pytest.mark.asyncio
    async def test_command_execution_cwd_change_broadcasts(self) -> None:
        session = _make_session()
        agent = _make_agent()
        agent._r._session_manager = MagicMock()
        events = [
            {"type": "item.started", "item": {"type": "command_execution", "command": "cd /tmp/new"}},
        ]
        await agent._relay_codex_stream(session, _chunks(events))
        agent._r._session_manager.broadcast_session_update.assert_called_once_with(session)


# ---------------------------------------------------------------------------
# _stream_codex_events / _restart_codex_with_prompt / _end_codex_session
# ---------------------------------------------------------------------------


class TestStreamCodexEvents:
    @pytest.mark.asyncio
    async def test_normal_finish_pushes_group_end_and_drains(self) -> None:
        session = _make_session()
        agent = _make_agent()
        executor = MagicMock()
        executor.execute_streaming = MagicMock(return_value=_chunks([{"type": "turn.completed", "usage": {}}]))
        executor.stop_process = AsyncMock()

        await agent._stream_codex_events(session, executor, MagicMock(), MagicMock(tool_input={}))

        executor.stop_process.assert_awaited_once()
        group_ends = _pushes(session, MessageType.AGENT_GROUP_END)
        assert len(group_ends) == 1
        agent._r.schedule_pending_drain.assert_called_once_with(session)

    @pytest.mark.asyncio
    async def test_exception_pushes_error_and_ends_session(self) -> None:
        session = _make_session()
        session.codex_executor = MagicMock()
        session.codex_executor.stop_process = AsyncMock()
        agent = _make_agent()
        agent._r._fire_archive_task = MagicMock()

        def _boom(_tool_def, _tool_input):
            async def _gen():
                raise RuntimeError("explode")
                yield  # pragma: no cover

            return _gen()

        executor = MagicMock()
        executor.execute_streaming = _boom

        await agent._stream_codex_events(session, executor, MagicMock(), MagicMock(tool_input={}))

        errors = _pushes(session, MessageType.ERROR)
        assert any("explode" in e["content"] for e in errors)
        # _end_codex_session ran → SESSION_END pushed and complete() called
        session.complete.assert_called_once()
        assert _pushes(session, MessageType.SESSION_END)


class TestRestartCodexWithPrompt:
    @pytest.mark.asyncio
    async def test_restart_normal_finish(self) -> None:
        session = _make_session()
        agent = _make_agent()
        executor = MagicMock()
        executor.restart_with_prompt = MagicMock(return_value=_chunks([{"type": "turn.completed", "usage": {}}]))
        executor.stop_process = AsyncMock()

        await agent._restart_codex_with_prompt(session, executor, "do more")

        executor.restart_with_prompt.assert_called_once_with("do more")
        executor.stop_process.assert_awaited_once()
        assert _pushes(session, MessageType.AGENT_GROUP_END)
        agent._r.schedule_pending_drain.assert_called_once_with(session)

    @pytest.mark.asyncio
    async def test_restart_exception_ends_session(self) -> None:
        session = _make_session()
        session.codex_executor = MagicMock()
        session.codex_executor.stop_process = AsyncMock()
        agent = _make_agent()
        agent._r._fire_archive_task = MagicMock()

        def _boom(_prompt):
            async def _gen():
                raise RuntimeError("restart fail")
                yield  # pragma: no cover

            return _gen()

        executor = MagicMock()
        executor.restart_with_prompt = _boom

        await agent._restart_codex_with_prompt(session, executor, "go")

        errors = _pushes(session, MessageType.ERROR)
        assert any("restart fail" in e["content"] for e in errors)
        session.complete.assert_called_once()


class TestEndCodexSession:
    @pytest.mark.asyncio
    async def test_active_session_pushes_session_end(self) -> None:
        session = _make_session()
        session.status = SessionStatus.ACTIVE
        session.codex_executor = MagicMock()
        session.codex_executor.stop_process = AsyncMock()
        agent = _make_agent()
        agent._r._fire_archive_task = MagicMock()

        await agent._end_codex_session(session)

        session.codex_executor_was = session.codex_executor
        assert session.codex_executor is None
        session.clear_subprocess_tracking.assert_called_once()
        assert _pushes(session, MessageType.SESSION_END)
        session.complete.assert_called_once()
        agent._r._fire_archive_task.assert_called_once_with(session.id)

    @pytest.mark.asyncio
    async def test_paused_session_completes_without_session_end(self) -> None:
        session = _make_session()
        session.status = SessionStatus.PAUSED
        session.codex_executor = MagicMock()
        session.codex_executor.stop_process = AsyncMock()
        agent = _make_agent()
        agent._r._fire_archive_task = MagicMock()

        await agent._end_codex_session(session)

        assert not _pushes(session, MessageType.SESSION_END)
        session.complete.assert_called_once()
        agent._r._fire_archive_task.assert_not_called()


class TestForwardToCodex:
    @pytest.mark.asyncio
    async def test_no_executor_returns_early(self) -> None:
        session = _make_session()
        session.codex_executor = None
        agent = _make_agent()
        await agent._forward_to_codex(session, "hi")
        session.set_activity.assert_not_called()

    @pytest.mark.asyncio
    async def test_paused_returns_early(self) -> None:
        session = _make_session()
        session.codex_executor = MagicMock()
        session.status = SessionStatus.PAUSED
        agent = _make_agent()
        await agent._forward_to_codex(session, "hi")
        session.set_activity.assert_not_called()

    @pytest.mark.asyncio
    async def test_forward_opens_group_and_spawns_task(self) -> None:
        session = _make_session()
        session.codex_executor = MagicMock()
        session.codex_executor.restart_with_prompt = MagicMock(
            return_value=_chunks([{"type": "turn.completed", "usage": {}}])
        )
        session.codex_executor.stop_process = AsyncMock()
        session.subprocess_started_at = None  # exercise the re-broadcast branch
        agent = _make_agent()
        agent._r._tool_registry.get.return_value = MagicMock(display_name="Codex")

        await agent._forward_to_codex(session, "follow up")

        session.set_activity.assert_called_with(ActivityState.RUNNING_SUBPROCESS)
        assert _pushes(session, MessageType.AGENT_GROUP_START)
        assert session._codex_stream_task is not None
        await session._codex_stream_task
