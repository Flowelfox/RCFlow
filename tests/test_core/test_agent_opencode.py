"""Tests for OpenCodeAgent stream relay and lifecycle methods.

Covers ``_relay_opencode_stream`` event translation (step_start, text, tool_use
start/result, step_finish completion + tokens, error/session.error, non-JSON),
plus the background wrappers (``_stream_opencode_events``,
``_restart_opencode_with_prompt``), ``_end_opencode_session``, and
``_forward_to_opencode``.

Mirrors the fake-executor / mock-session style of the Claude Code agent tests.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.agent_opencode import OpenCodeAgent
from src.core.buffer import MessageType
from src.core.session import ActivityState, SessionStatus

if TYPE_CHECKING:
    from collections.abc import Iterable


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session() -> MagicMock:
    session = MagicMock()
    session.id = "oc-session"
    session.metadata = {}
    session.opencode_executor = MagicMock()
    session.subprocess_started_at = MagicMock()  # truthy → ephemeral status pushes
    session.subprocess_started_at_iso = "2026-06-16T00:00:00+00:00"
    session.subprocess_type = "opencode"
    session.subprocess_display_name = "OpenCode"
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


def _make_agent() -> OpenCodeAgent:
    agent = OpenCodeAgent.__new__(OpenCodeAgent)
    agent._r = MagicMock()
    agent._r._fire_text_artifact_scan = MagicMock()
    agent._r._fire_summary_task = MagicMock()
    agent._r._fire_task_update_task = MagicMock()
    agent._r._session_manager = None
    agent._r.schedule_pending_drain = MagicMock()
    return agent


def _chunks(events: Iterable[dict | str]):
    async def _gen():
        for e in events:
            chunk = MagicMock()
            chunk.content = e if isinstance(e, str) else json.dumps(e)
            yield chunk

    return _gen()


def _pushes(session: MagicMock, msg_type: MessageType) -> list[dict]:
    return [c[0][1] for c in session.buffer.push_text.call_args_list if c[0][0] == msg_type]


# ---------------------------------------------------------------------------
# _relay_opencode_stream
# ---------------------------------------------------------------------------


class TestRelayOpenCodeStream:
    @pytest.mark.asyncio
    async def test_step_start_persists_session_id(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_opencode_stream(session, _chunks([{"type": "step_start", "sessionID": "sid-1"}]))
        assert session.metadata["opencode_session_id"] == "sid-1"

    @pytest.mark.asyncio
    async def test_step_start_session_id_from_part(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_opencode_stream(
            session, _chunks([{"type": "step_start", "part": {"sessionID": "sid-part"}}])
        )
        assert session.metadata["opencode_session_id"] == "sid-part"

    @pytest.mark.asyncio
    async def test_text_event_emits_chunk_and_scans(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_opencode_stream(session, _chunks([{"type": "text", "part": {"text": "Hello there"}}]))
        texts = _pushes(session, MessageType.TEXT_CHUNK)
        assert texts[0]["content"] == "Hello there"
        agent._r._fire_text_artifact_scan.assert_called_with(session, ["Hello there"])

    @pytest.mark.asyncio
    async def test_non_json_line_becomes_text_chunk(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_opencode_stream(session, _chunks(["plain text line"]))
        texts = _pushes(session, MessageType.TEXT_CHUNK)
        assert texts[0]["content"] == "plain text line"

    @pytest.mark.asyncio
    async def test_blank_and_non_dict_skipped(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_opencode_stream(session, _chunks(["", "  ", "[1,2]"]))
        assert session.buffer.push_text.call_count == 0

    @pytest.mark.asyncio
    async def test_tool_use_pending_emits_start_only(self) -> None:
        session = _make_session()
        agent = _make_agent()
        event = {
            "type": "tool_use",
            "part": {"tool": "read", "state": {"input": {"path": "x.py"}, "status": "running"}},
        }
        await agent._relay_opencode_stream(session, _chunks([event]))
        starts = _pushes(session, MessageType.TOOL_START)
        assert starts[0]["tool_name"] == "read"
        assert starts[0]["tool_input"] == {"path": "x.py"}
        assert not _pushes(session, MessageType.TOOL_OUTPUT)

    @pytest.mark.asyncio
    async def test_tool_use_completed_emits_output(self) -> None:
        session = _make_session()
        agent = _make_agent()
        event = {
            "type": "tool_use",
            "part": {
                "tool": "read",
                "state": {"input": {"path": "x.py"}, "status": "completed", "output": "file body"},
            },
        }
        await agent._relay_opencode_stream(session, _chunks([event]))
        outputs = _pushes(session, MessageType.TOOL_OUTPUT)
        assert outputs[0]["tool_name"] == "read"
        assert outputs[0]["content"] == "file body"
        agent._r._fire_text_artifact_scan.assert_called()

    @pytest.mark.asyncio
    async def test_tool_use_completed_dict_output_serialized(self) -> None:
        session = _make_session()
        agent = _make_agent()
        event = {
            "type": "tool_use",
            "part": {"tool": "grep", "state": {"status": "completed", "output": {"matches": 3}}},
        }
        await agent._relay_opencode_stream(session, _chunks([event]))
        outputs = _pushes(session, MessageType.TOOL_OUTPUT)
        assert '"matches": 3' in outputs[0]["content"]

    @pytest.mark.asyncio
    async def test_bash_tool_cwd_change_broadcasts(self) -> None:
        session = _make_session()
        agent = _make_agent()
        agent._r._session_manager = MagicMock()
        event = {
            "type": "tool_use",
            "part": {"tool": "bash", "state": {"input": {"command": "cd /tmp/here"}, "status": "running"}},
        }
        await agent._relay_opencode_stream(session, _chunks([event]))
        agent._r._session_manager.broadcast_session_update.assert_called_once_with(session)

    @pytest.mark.asyncio
    async def test_step_finish_accumulates_tokens(self) -> None:
        session = _make_session()
        agent = _make_agent()
        event = {"type": "step_finish", "part": {"tokens": {"input": 12, "output": 7}, "reason": "tool"}}
        completed = await agent._relay_opencode_stream(session, _chunks([event]))
        assert completed is False
        assert session.tool_input_tokens == 12
        assert session.tool_output_tokens == 7
        session._on_update.assert_called()

    @pytest.mark.asyncio
    async def test_step_finish_stop_marks_completed_and_summary(self) -> None:
        session = _make_session()
        agent = _make_agent()
        events = [
            {"type": "text", "part": {"text": "Final answer"}},
            {"type": "step_finish", "part": {"tokens": {}, "reason": "stop"}},
        ]
        completed = await agent._relay_opencode_stream(session, _chunks(events))
        assert completed is True
        session.set_activity.assert_called_with(ActivityState.IDLE)
        assert agent._r._fire_summary_task.call_args[0][1] == "Final answer"
        agent._r._fire_task_update_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_step_finish_stop_default_summary(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_opencode_stream(session, _chunks([{"type": "step_finish", "part": {"reason": "stop"}}]))
        assert agent._r._fire_summary_task.call_args[0][1] == "OpenCode task completed"

    @pytest.mark.asyncio
    async def test_error_event_string_message(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_opencode_stream(session, _chunks([{"type": "error", "error": "boom string"}]))
        errors = _pushes(session, MessageType.ERROR)
        assert errors[0]["content"] == "boom string"
        assert errors[0]["code"] == "OPENCODE_ERROR"

    @pytest.mark.asyncio
    async def test_session_error_nested_data_message(self) -> None:
        session = _make_session()
        agent = _make_agent()
        event = {"type": "session.error", "error": {"data": {"message": "nested fail"}}}
        await agent._relay_opencode_stream(session, _chunks([event]))
        errors = _pushes(session, MessageType.ERROR)
        assert errors[0]["content"] == "nested fail"

    @pytest.mark.asyncio
    async def test_error_event_top_level_message(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_opencode_stream(session, _chunks([{"type": "error", "error": {"message": "top msg"}}]))
        errors = _pushes(session, MessageType.ERROR)
        assert errors[0]["content"] == "top msg"

    @pytest.mark.asyncio
    async def test_unknown_event_skipped(self) -> None:
        session = _make_session()
        agent = _make_agent()
        await agent._relay_opencode_stream(session, _chunks([{"type": "weird.thing"}]))
        assert session.buffer.push_text.call_count == 0


# ---------------------------------------------------------------------------
# _stream_opencode_events / _restart_opencode_with_prompt / _end_opencode_session
# ---------------------------------------------------------------------------


class TestStreamOpenCodeEvents:
    @pytest.mark.asyncio
    async def test_completed_stream_drains(self) -> None:
        session = _make_session()
        agent = _make_agent()
        executor = MagicMock()
        executor.execute_streaming = MagicMock(
            return_value=_chunks([{"type": "step_finish", "part": {"reason": "stop"}}])
        )
        executor.stop_process = AsyncMock()

        await agent._stream_opencode_events(session, executor, MagicMock(), MagicMock(tool_input={}))

        executor.stop_process.assert_awaited_once()
        assert _pushes(session, MessageType.AGENT_GROUP_END)
        agent._r.schedule_pending_drain.assert_called_once_with(session)

    @pytest.mark.asyncio
    async def test_incomplete_stream_ends_session(self) -> None:
        session = _make_session()
        session.opencode_executor = MagicMock()
        session.opencode_executor.stop_process = AsyncMock()
        agent = _make_agent()
        agent._r._fire_archive_task = MagicMock()
        executor = MagicMock()
        # no step_finish stop → completed == False
        executor.execute_streaming = MagicMock(return_value=_chunks([{"type": "text", "part": {"text": "hi"}}]))
        executor.stop_process = AsyncMock()

        await agent._stream_opencode_events(session, executor, MagicMock(), MagicMock(tool_input={}))

        # Did NOT drain; instead ended the session
        agent._r.schedule_pending_drain.assert_not_called()
        session.complete.assert_called_once()
        assert _pushes(session, MessageType.SESSION_END)

    @pytest.mark.asyncio
    async def test_exception_pushes_error_and_ends(self) -> None:
        session = _make_session()
        session.opencode_executor = MagicMock()
        session.opencode_executor.stop_process = AsyncMock()
        agent = _make_agent()
        agent._r._fire_archive_task = MagicMock()

        def _boom(_tool_def, _tool_input):
            async def _gen():
                raise RuntimeError("explode")
                yield  # pragma: no cover

            return _gen()

        executor = MagicMock()
        executor.execute_streaming = _boom

        await agent._stream_opencode_events(session, executor, MagicMock(), MagicMock(tool_input={}))

        errors = _pushes(session, MessageType.ERROR)
        assert any("explode" in e["content"] for e in errors)
        session.complete.assert_called_once()


class TestRestartOpenCodeWithPrompt:
    @pytest.mark.asyncio
    async def test_restart_completed_drains(self) -> None:
        session = _make_session()
        agent = _make_agent()
        executor = MagicMock()
        executor.restart_with_prompt = MagicMock(
            return_value=_chunks([{"type": "step_finish", "part": {"reason": "stop"}}])
        )
        executor.stop_process = AsyncMock()

        await agent._restart_opencode_with_prompt(session, executor, "again")

        executor.restart_with_prompt.assert_called_once_with("again")
        executor.stop_process.assert_awaited_once()
        agent._r.schedule_pending_drain.assert_called_once_with(session)

    @pytest.mark.asyncio
    async def test_restart_incomplete_ends_session(self) -> None:
        session = _make_session()
        session.opencode_executor = MagicMock()
        session.opencode_executor.stop_process = AsyncMock()
        agent = _make_agent()
        agent._r._fire_archive_task = MagicMock()
        executor = MagicMock()
        executor.restart_with_prompt = MagicMock(return_value=_chunks([{"type": "text", "part": {"text": "x"}}]))

        await agent._restart_opencode_with_prompt(session, executor, "go")

        agent._r.schedule_pending_drain.assert_not_called()
        session.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_restart_exception_ends_session(self) -> None:
        session = _make_session()
        session.opencode_executor = MagicMock()
        session.opencode_executor.stop_process = AsyncMock()
        agent = _make_agent()
        agent._r._fire_archive_task = MagicMock()

        def _boom(_prompt):
            async def _gen():
                raise RuntimeError("restart fail")
                yield  # pragma: no cover

            return _gen()

        executor = MagicMock()
        executor.restart_with_prompt = _boom

        await agent._restart_opencode_with_prompt(session, executor, "go")

        errors = _pushes(session, MessageType.ERROR)
        assert any("restart fail" in e["content"] for e in errors)
        session.complete.assert_called_once()


class TestEndOpenCodeSession:
    @pytest.mark.asyncio
    async def test_active_session_pushes_session_end(self) -> None:
        session = _make_session()
        session.status = SessionStatus.ACTIVE
        session.opencode_executor = MagicMock()
        session.opencode_executor.stop_process = AsyncMock()
        agent = _make_agent()
        agent._r._fire_archive_task = MagicMock()

        await agent._end_opencode_session(session)

        assert session.opencode_executor is None
        session.clear_subprocess_tracking.assert_called_once()
        assert _pushes(session, MessageType.SESSION_END)
        session.complete.assert_called_once()
        agent._r._fire_archive_task.assert_called_once_with(session.id)

    @pytest.mark.asyncio
    async def test_paused_session_no_session_end(self) -> None:
        session = _make_session()
        session.status = SessionStatus.PAUSED
        session.opencode_executor = MagicMock()
        session.opencode_executor.stop_process = AsyncMock()
        agent = _make_agent()
        agent._r._fire_archive_task = MagicMock()

        await agent._end_opencode_session(session)

        assert not _pushes(session, MessageType.SESSION_END)
        session.complete.assert_called_once()
        agent._r._fire_archive_task.assert_not_called()


class TestForwardToOpenCode:
    @pytest.mark.asyncio
    async def test_no_executor_returns_early(self) -> None:
        session = _make_session()
        session.opencode_executor = None
        agent = _make_agent()
        await agent._forward_to_opencode(session, "hi")
        session.set_activity.assert_not_called()

    @pytest.mark.asyncio
    async def test_paused_returns_early(self) -> None:
        session = _make_session()
        session.opencode_executor = MagicMock()
        session.status = SessionStatus.PAUSED
        agent = _make_agent()
        await agent._forward_to_opencode(session, "hi")
        session.set_activity.assert_not_called()

    @pytest.mark.asyncio
    async def test_forward_opens_group_and_spawns_task(self) -> None:
        session = _make_session()
        session.opencode_executor = MagicMock()
        session.opencode_executor.restart_with_prompt = MagicMock(
            return_value=_chunks([{"type": "step_finish", "part": {"reason": "stop"}}])
        )
        session.opencode_executor.stop_process = AsyncMock()
        session.subprocess_started_at = None  # exercise the re-broadcast branch
        agent = _make_agent()
        agent._r._tool_registry.get.return_value = MagicMock(display_name="OpenCode")

        await agent._forward_to_opencode(session, "follow up")

        session.set_activity.assert_called_with(ActivityState.RUNNING_SUBPROCESS)
        assert _pushes(session, MessageType.AGENT_GROUP_START)
        assert session._opencode_stream_task is not None
        # Let the spawned restart task run to completion to avoid pending-task warnings.
        await session._opencode_stream_task
