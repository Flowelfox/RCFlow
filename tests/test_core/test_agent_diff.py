"""Tests for pre/post file snapshot diff tracking in ClaudeCodeAgentMixin.

Covers:
- ``_read_file_snapshot`` — missing file, binary file, oversized file
- ``_compute_diff`` — no change, pure additions, truncation, no double-newlines
- Integration: Edit tool_use → tool_result carries 'diff' key
- Integration: Bash tool_use → tool_result has no 'diff' key
- Integration: sentinel misalignment guard (TodoWrite then Edit)
- Integration: _pending_snapshots empty after tool_result processing
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.agent_claude_code import (
    _MAX_DIFF_LINES,
    _MAX_SNAPSHOT_BYTES,
    ClaudeCodeAgentMixin,
    _compute_diff,
    _read_file_snapshot,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# _read_file_snapshot unit tests
# ---------------------------------------------------------------------------


class TestReadFileSnapshot:
    @pytest.mark.asyncio
    async def test_missing_file_returns_empty_string(self, tmp_path: Path) -> None:
        path = tmp_path / "nonexistent.py"
        result = await _read_file_snapshot(path)
        assert result == ""

    @pytest.mark.asyncio
    async def test_binary_file_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "image.bin"
        path.write_bytes(bytes(range(256)))  # non-UTF-8 bytes
        result = await _read_file_snapshot(path)
        assert result is None

    @pytest.mark.asyncio
    async def test_oversized_file_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "big.txt"
        # Write content just over the limit
        path.write_bytes(b"x" * (_MAX_SNAPSHOT_BYTES + 1))
        result = await _read_file_snapshot(path)
        assert result is None

    @pytest.mark.asyncio
    async def test_normal_file_returns_content(self, tmp_path: Path) -> None:
        path = tmp_path / "hello.py"
        path.write_text("print('hello')\n", encoding="utf-8")
        result = await _read_file_snapshot(path)
        assert result == "print('hello')\n"

    @pytest.mark.asyncio
    async def test_file_at_size_limit_is_read(self, tmp_path: Path) -> None:
        path = tmp_path / "exactly.txt"
        path.write_bytes(b"a" * _MAX_SNAPSHOT_BYTES)
        result = await _read_file_snapshot(path)
        assert result is not None
        assert len(result) == _MAX_SNAPSHOT_BYTES


# ---------------------------------------------------------------------------
# _compute_diff unit tests
# ---------------------------------------------------------------------------


class TestComputeDiff:
    def test_identical_content_returns_none(self) -> None:
        assert _compute_diff("foo\nbar\n", "foo\nbar\n", "test.py") is None

    def test_pure_additions_when_before_empty(self) -> None:
        result = _compute_diff("", "line1\nline2\n", "new.py")
        assert result is not None
        lines = result.split("\n")
        added = [line for line in lines if line.startswith("+") and not line.startswith("+++")]
        assert len(added) == 2
        assert not any(line.startswith("-") and not line.startswith("---") for line in lines)

    def test_truncates_at_max_diff_lines(self) -> None:
        before = "\n".join(f"line{i}" for i in range(_MAX_DIFF_LINES + 50))
        after = "\n".join(f"changed{i}" for i in range(_MAX_DIFF_LINES + 50))
        result = _compute_diff(before, after, "big.py")
        assert result is not None
        lines = result.split("\n")
        # Should be truncated: _MAX_DIFF_LINES payload lines + 1 truncation notice
        assert len(lines) == _MAX_DIFF_LINES + 1
        assert lines[-1].startswith("... diff truncated")

    def test_no_double_newlines_between_diff_lines(self) -> None:
        """Regression: keepends=False + lineterm='' must not produce blank lines."""
        before = "a\nb\nc\n"
        after = "a\nB\nc\n"
        result = _compute_diff(before, after, "file.py")
        assert result is not None
        assert "\n\n" not in result

    def test_hunk_header_present(self) -> None:
        before = "old\n"
        after = "new\n"
        result = _compute_diff(before, after, "x.py")
        assert result is not None
        assert any(line.startswith("@@") for line in result.split("\n"))


# ---------------------------------------------------------------------------
# Integration tests — synthetic event stream through _relay_claude_code_stream
# ---------------------------------------------------------------------------


def _make_session(tmp_path: Path) -> MagicMock:
    """Build a minimal mock ActiveSession with the attributes the mixin reads."""
    session = MagicMock()
    session.id = "test-session"
    session.subprocess_working_directory = str(tmp_path)
    session._pending_snapshots = []
    session.permission_manager = None
    session.subprocess_current_tool = None
    session.subprocess_started_at = None
    session.subprocess_type = None
    session.subprocess_display_name = None
    session.buffer = MagicMock()
    session.buffer.push_text = MagicMock()
    return session


def _json_lines(*events: dict) -> bytes:
    return b"".join(json.dumps(e).encode() + b"\n" for e in events)


def _make_executor_chunks(events: list[dict]):
    """Async generator yielding ExecutionChunk-like objects for each event dict."""

    async def _gen():
        for e in events:
            chunk = MagicMock()
            chunk.content = json.dumps(e)
            yield chunk

    return _gen()


def _make_mixin():
    mixin = ClaudeCodeAgentMixin.__new__(ClaudeCodeAgentMixin)
    mixin._tool_settings = None
    mixin._fire_text_artifact_scan = MagicMock()
    return mixin


class TestDiffIntegration:
    @pytest.mark.asyncio
    async def test_edit_tool_produces_diff_key(self, tmp_path: Path) -> None:
        """Edit tool_use + tool_result → TOOL_OUTPUT message carries 'diff' key."""
        target = tmp_path / "foo.py"
        target.write_text("old_content\n", encoding="utf-8")

        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu1",
                            "name": "Edit",
                            "input": {"file_path": str(target)},
                        }
                    ]
                },
            },
            {
                "type": "tool_result",
                "tool_use_id": "tu1",
                "content": "File edited.",
                "is_error": False,
            },
        ]

        session = _make_session(tmp_path)
        mixin = _make_mixin()

        # Simulate file changing between pre and post snapshot
        call_count = 0

        async def _mock_read(path: Path) -> str | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "old_content\n"  # pre-snapshot
            return "new_content\n"  # post-snapshot

        with patch("src.core.agent_claude_code._read_file_snapshot", _mock_read):
            await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        calls = session.buffer.push_text.call_args_list
        tool_output_calls = [c for c in calls if c[0][0].name == "TOOL_OUTPUT"]
        assert len(tool_output_calls) == 1
        msg_data = tool_output_calls[0][0][1]
        assert "diff" in msg_data
        assert "+new_content" in msg_data["diff"]

    @pytest.mark.asyncio
    async def test_edit_tool_empty_content_still_sends_diff(self, tmp_path: Path) -> None:
        """Edit tool with empty tool_result content must still emit TOOL_OUTPUT with diff."""
        target = tmp_path / "empty.py"
        target.write_text("old\n", encoding="utf-8")

        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu_empty",
                            "name": "Edit",
                            "input": {"file_path": str(target)},
                        }
                    ]
                },
            },
            {
                "type": "tool_result",
                "tool_use_id": "tu_empty",
                "content": "",
                "is_error": False,
            },
        ]

        session = _make_session(tmp_path)
        mixin = _make_mixin()

        call_count = 0

        async def _mock_read(path: Path) -> str | None:
            nonlocal call_count
            call_count += 1
            return "old\n" if call_count == 1 else "new\n"

        with patch("src.core.agent_claude_code._read_file_snapshot", _mock_read):
            await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        calls = session.buffer.push_text.call_args_list
        tool_output_calls = [c for c in calls if c[0][0].name == "TOOL_OUTPUT"]
        assert len(tool_output_calls) == 1
        msg_data = tool_output_calls[0][0][1]
        assert "diff" in msg_data
        assert "+new" in msg_data["diff"]

    @pytest.mark.asyncio
    async def test_bash_tool_produces_no_diff(self, tmp_path: Path) -> None:
        """Bash tool_use + tool_result → TOOL_OUTPUT has no 'diff' key."""
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu2",
                            "name": "Bash",
                            "input": {"command": "echo hello"},
                        }
                    ]
                },
            },
            {
                "type": "tool_result",
                "tool_use_id": "tu2",
                "content": "hello",
                "is_error": False,
            },
        ]

        session = _make_session(tmp_path)
        mixin = _make_mixin()

        await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        calls = session.buffer.push_text.call_args_list
        tool_output_calls = [c for c in calls if c[0][0].name == "TOOL_OUTPUT"]
        assert len(tool_output_calls) == 1
        msg_data = tool_output_calls[0][0][1]
        assert "diff" not in msg_data

    @pytest.mark.asyncio
    async def test_sentinel_alignment_todo_then_edit(self, tmp_path: Path) -> None:
        """TodoWrite sentinel + Edit real snapshot: stack empty after both results.

        Matches Claude Code's real protocol: each assistant event has one tool_use,
        immediately followed by its tool_result, before the next tool_use is issued.
        """
        target = tmp_path / "bar.py"
        target.write_text("before\n", encoding="utf-8")

        # Claude Code emits tool_use and tool_result in 1:1 interleaved order.
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu3",
                            "name": "TodoWrite",
                            "input": {"todos": []},
                        }
                    ]
                },
            },
            {
                "type": "tool_result",
                "tool_use_id": "tu3",
                "content": "",
                "is_error": False,
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu4",
                            "name": "Edit",
                            "input": {"file_path": str(target)},
                        }
                    ]
                },
            },
            {
                "type": "tool_result",
                "tool_use_id": "tu4",
                "content": "Edited.",
                "is_error": False,
            },
        ]

        session = _make_session(tmp_path)
        mixin = _make_mixin()

        call_count = 0

        async def _mock_read(path: Path) -> str | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "before\n"  # pre-snapshot for Edit
            return "after\n"  # post-snapshot for Edit

        with patch("src.core.agent_claude_code._read_file_snapshot", _mock_read):
            await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        # Stack must be empty — no leaks
        assert session._pending_snapshots == []

        # The Edit's TOOL_OUTPUT should carry a diff
        calls = session.buffer.push_text.call_args_list
        tool_output_calls = [c for c in calls if c[0][0].name == "TOOL_OUTPUT"]
        edit_output = next(
            (c for c in tool_output_calls if c[0][1].get("content") == "Edited."),
            None,
        )
        assert edit_output is not None
        assert "diff" in edit_output[0][1]

    @pytest.mark.asyncio
    async def test_stack_empty_after_single_tool_result(self, tmp_path: Path) -> None:
        """_pending_snapshots is empty after a single Bash tool_use + tool_result."""
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tu5",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        }
                    ]
                },
            },
            {
                "type": "tool_result",
                "tool_use_id": "tu5",
                "content": "file.txt",
                "is_error": False,
            },
        ]

        session = _make_session(tmp_path)
        mixin = _make_mixin()

        await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        assert session._pending_snapshots == []


# ---------------------------------------------------------------------------
# _split_into_chunks unit tests (improvement C)
# ---------------------------------------------------------------------------


from src.core.agent_claude_code import _TOOL_OUTPUT_CHUNK_SIZE, _split_into_chunks  # noqa: E402


class TestSplitIntoChunks:
    def test_small_content_returns_single_chunk(self) -> None:
        content = "hello world"
        result = _split_into_chunks(content, 100)
        assert result == ["hello world"]

    def test_content_exactly_at_limit_is_single_chunk(self) -> None:
        content = "a" * 8192
        result = _split_into_chunks(content, 8192)
        assert len(result) == 1
        assert result[0] == content

    def test_large_content_splits_into_multiple_chunks(self) -> None:
        content = "x" * 5000 + "\n" + "y" * 5000
        result = _split_into_chunks(content, 8192)
        assert len(result) > 1
        assert "".join(result) == content

    def test_line_boundary_split_preserves_content(self) -> None:
        lines = [f"line{i}" for i in range(200)]
        content = "\n".join(lines)
        chunks = _split_into_chunks(content, 256)
        assert "".join(chunks) == content

    def test_oversized_single_line_is_hard_split(self) -> None:
        long_line = "z" * 20_000
        result = _split_into_chunks(long_line, 8192)
        assert len(result) > 1
        assert "".join(result) == long_line
        for chunk in result:
            assert len(chunk) <= 8192

    def test_empty_content_returns_single_empty_chunk(self) -> None:
        result = _split_into_chunks("", 8192)
        assert result == [""]

    def test_chunk_size_constant_is_8192(self) -> None:
        assert _TOOL_OUTPUT_CHUNK_SIZE == 8_192


class TestChunkedToolOutput:
    """Integration: large tool_result content produces multiple TOOL_OUTPUT messages."""

    @pytest.mark.asyncio
    async def test_large_tool_result_produces_multiple_tool_output_messages(self, tmp_path: Path) -> None:
        large_content = ("x" * 200 + "\n") * 50  # ~10 KB → 2+ chunks at 8 KB
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "big"}}]
                },
            },
            {"type": "tool_result", "tool_use_id": "tu1", "content": large_content, "is_error": False},
        ]
        session = _make_session(tmp_path)
        mixin = _make_mixin()
        await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        calls = session.buffer.push_text.call_args_list
        output_calls = [c for c in calls if c[0][0].name == "TOOL_OUTPUT"]
        assert len(output_calls) > 1
        for i, call in enumerate(output_calls):
            data = call[0][1]
            assert data["chunk_index"] == i
            assert data["total_chunks"] == len(output_calls)

    @pytest.mark.asyncio
    async def test_small_tool_result_has_no_chunk_fields(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}]},
            },
            {"type": "tool_result", "tool_use_id": "tu1", "content": "file.txt", "is_error": False},
        ]
        session = _make_session(tmp_path)
        mixin = _make_mixin()
        await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        calls = session.buffer.push_text.call_args_list
        output_calls = [c for c in calls if c[0][0].name == "TOOL_OUTPUT"]
        assert len(output_calls) == 1
        data = output_calls[0][0][1]
        assert "chunk_index" not in data
        assert "total_chunks" not in data

    @pytest.mark.asyncio
    async def test_diff_attached_to_last_chunk_only(self, tmp_path: Path) -> None:
        target = tmp_path / "big.py"
        target.write_text("old\n", encoding="utf-8")
        large_content = ("x" * 200 + "\n") * 50  # >8 KB

        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "tu1", "name": "Edit", "input": {"file_path": str(target)}}]
                },
            },
            {"type": "tool_result", "tool_use_id": "tu1", "content": large_content, "is_error": False},
        ]
        session = _make_session(tmp_path)
        mixin = _make_mixin()

        call_count = 0

        async def _mock_read(path: Path) -> str | None:
            nonlocal call_count
            call_count += 1
            return "old\n" if call_count == 1 else "new\n"

        with patch("src.core.agent_claude_code._read_file_snapshot", _mock_read):
            await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        calls = session.buffer.push_text.call_args_list
        output_calls = [c for c in calls if c[0][0].name == "TOOL_OUTPUT"]
        assert len(output_calls) > 1
        for i, call in enumerate(output_calls):
            data = call[0][1]
            if i < len(output_calls) - 1:
                assert "diff" not in data
            else:
                assert "diff" in data


# ---------------------------------------------------------------------------
# AGENT_LOG routing tests (improvement E)
# ---------------------------------------------------------------------------


from src.core.buffer import MessageType  # noqa: E402


class TestAgentLogRouting:
    @pytest.mark.asyncio
    async def test_non_json_stdout_produces_agent_log_not_text_chunk(self, tmp_path: Path) -> None:
        async def _non_json_gen():
            chunk = MagicMock()
            chunk.content = "startup banner: Claude Code v1.0"
            yield chunk

        session = _make_session(tmp_path)
        mixin = _make_mixin()
        await mixin._relay_claude_code_stream(session, _non_json_gen())

        calls = session.buffer.push_text.call_args_list
        text_chunk_calls = [c for c in calls if c[0][0] == MessageType.TEXT_CHUNK]
        assert len(text_chunk_calls) == 0
        log_calls = [c for c in calls if c[0][0] == MessageType.AGENT_LOG]
        assert len(log_calls) == 1
        data = log_calls[0][0][1]
        assert data["source"] == "stdout"
        assert "startup banner" in data["content"]
        assert data["level"] in ("debug", "warn", "error", "info")

    @pytest.mark.asyncio
    async def test_non_json_error_line_classified_with_elevated_level(self, tmp_path: Path) -> None:
        async def _error_gen():
            chunk = MagicMock()
            chunk.content = "Error: authentication failed"
            yield chunk

        session = _make_session(tmp_path)
        mixin = _make_mixin()
        await mixin._relay_claude_code_stream(session, _error_gen())

        calls = session.buffer.push_text.call_args_list
        log_calls = [c for c in calls if c[0][0] == MessageType.AGENT_LOG]
        assert len(log_calls) == 1
        assert log_calls[0][0][1]["level"] in ("warn", "error")

    def test_agent_log_message_type_exists_in_enum(self) -> None:
        assert MessageType.AGENT_LOG == "agent_log"


# ---------------------------------------------------------------------------
# Plan mode / plan review timeout tests (improvement A)
# ---------------------------------------------------------------------------


class TestPlanModeTimeout:
    @pytest.mark.asyncio
    async def test_enter_plan_mode_timeout_terminates_session(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "id": "tu1", "name": "EnterPlanMode", "input": {}}]},
            },
        ]
        session = _make_session(tmp_path)
        session.set_activity = MagicMock()
        session._plan_mode_approved = False

        mixin = _make_mixin()
        mixin._end_claude_code_session = AsyncMock()

        async def _timeout(*args, **kwargs):
            raise TimeoutError()

        with patch("asyncio.wait_for", side_effect=_timeout):
            await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        calls = session.buffer.push_text.call_args_list
        error_calls = [c for c in calls if c[0][0].name == "ERROR"]
        assert any("PLAN_MODE_DENIED" in str(c) for c in error_calls)
        mixin._end_claude_code_session.assert_called_once_with(session)

    @pytest.mark.asyncio
    async def test_exit_plan_mode_timeout_terminates_session(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [{"type": "tool_use", "id": "tu1", "name": "ExitPlanMode", "input": {"plan": "step 1"}}]
                },
            },
        ]
        session = _make_session(tmp_path)
        session.set_activity = MagicMock()
        session._plan_review_approved = False
        session._plan_review_feedback = None

        mixin = _make_mixin()
        mixin._end_claude_code_session = AsyncMock()

        async def _timeout(*args, **kwargs):
            raise TimeoutError()

        with patch("asyncio.wait_for", side_effect=_timeout):
            await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        calls = session.buffer.push_text.call_args_list
        error_calls = [c for c in calls if c[0][0].name == "ERROR"]
        assert any("PLAN_REVIEW_TIMEOUT" in str(c) for c in error_calls)
        mixin._end_claude_code_session.assert_called_once_with(session)

    @pytest.mark.asyncio
    async def test_enter_plan_mode_approved_does_not_terminate(self, tmp_path: Path) -> None:
        events = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "id": "tu1", "name": "EnterPlanMode", "input": {}}]},
            },
            {"type": "tool_result", "tool_use_id": "tu1", "content": "", "is_error": False},
        ]
        session = _make_session(tmp_path)
        session.set_activity = MagicMock()
        session._plan_mode_approved = True

        mixin = _make_mixin()
        mixin._end_claude_code_session = AsyncMock()

        async def _fast_wait(coro, *, timeout):
            # Simulate client approving plan mode before timeout fires.
            # The relay resets _plan_mode_approved=False right before waiting,
            # so we must re-set it here to represent a real client approval.
            session._plan_mode_approved = True

        with patch("asyncio.wait_for", side_effect=_fast_wait):
            await mixin._relay_claude_code_stream(session, _make_executor_chunks(events))

        mixin._end_claude_code_session.assert_not_called()
