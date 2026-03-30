import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.executors.claude_code import ClaudeCodeExecutor
from src.tools.loader import ToolDefinition


@pytest.fixture
def claude_code_tool() -> ToolDefinition:
    return ToolDefinition(
        name="claude_code",
        description="Claude Code agent",
        version="1.0.0",
        session_type="long-running",
        llm_context="session-scoped",
        executor="claude_code",
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "working_directory": {"type": "string"},
            },
            "required": ["prompt", "working_directory"],
        },
        executor_config={
            "claude_code": {
                "binary_path": "claude",
                "default_permission_mode": "bypassPermissions",
                "max_turns": 200,
                "timeout": 1800,
                "use_pty": False,  # tests mock asyncio pipes; PTY mode tested separately
            }
        },
    )


@pytest.fixture
def executor() -> ClaudeCodeExecutor:
    return ClaudeCodeExecutor(binary_path="/usr/bin/claude")


def _make_mock_process(
    stdout_lines: list[str],
    returncode: int | None = None,
    stderr_lines: list[str] | None = None,
) -> MagicMock:
    """Create a mock subprocess with predefined stdout/stderr lines."""
    process = MagicMock()
    process.pid = 999999  # Non-existent PID so os.killpg raises ProcessLookupError
    process.returncode = returncode

    # Build an async readline for stdout
    stdout_data = iter([line.encode("utf-8") + b"\n" for line in stdout_lines] + [b""])
    stdout_reader = MagicMock()
    stdout_reader.readline = AsyncMock(side_effect=stdout_data)
    process.stdout = stdout_reader

    # Build an async readline for stderr (drain task reads this)
    stderr_data = iter([line.encode("utf-8") + b"\n" for line in (stderr_lines or [])] + [b""])
    stderr_reader = MagicMock()
    stderr_reader.readline = AsyncMock(side_effect=stderr_data)
    process.stderr = stderr_reader

    process.stdin = MagicMock()
    process.stdin.write = MagicMock()
    process.stdin.drain = AsyncMock()
    process.kill = MagicMock()
    process.wait = AsyncMock()

    return process


class TestBuildCommand:
    def test_basic_command(self, executor: ClaudeCodeExecutor):
        config = {
            "binary_path": "claude",
            "default_permission_mode": "bypassPermissions",
            "max_turns": 200,
        }
        cmd = executor._build_command({"prompt": "hello"}, config)
        assert cmd[0] == "/usr/bin/claude"
        assert "--print" in cmd
        assert "--input-format" in cmd
        assert "stream-json" in cmd
        assert "--output-format" in cmd
        assert "--permission-mode" in cmd
        assert "bypassPermissions" in cmd
        assert "--max-turns" in cmd
        assert "200" in cmd

    def test_prompt_as_cli_argument(self, executor: ClaudeCodeExecutor):
        config = {}
        cmd = executor._build_command({"prompt": "hello"}, config, prompt="do something")
        assert cmd[-2] == "--"
        assert cmd[-1] == "do something"

    def test_no_prompt_arg_when_none(self, executor: ClaudeCodeExecutor):
        config = {}
        cmd = executor._build_command({"prompt": "hello"}, config)
        assert "do something" not in cmd

    def test_with_model_and_allowed_tools(self, executor: ClaudeCodeExecutor):
        config = {}
        params = {
            "prompt": "hello",
            "model": "opus",
            "allowed_tools": "Bash Edit Read",
        }
        cmd = executor._build_command(params, config)
        assert "--model" in cmd
        assert "opus" in cmd
        assert "--allowedTools" in cmd
        assert "Bash Edit Read" in cmd


class TestBuildEnv:
    def test_removes_claudecode_env(self, executor: ClaudeCodeExecutor):
        with patch.dict("os.environ", {"CLAUDECODE": "1", "CLAUDE_AVAILABLE_MODELS": "sonnet", "PATH": "/usr/bin"}):
            env = executor._build_env()
            assert "CLAUDECODE" not in env
            assert "CLAUDE_AVAILABLE_MODELS" not in env
            assert "PATH" in env

    def test_handles_missing_env_vars(self, executor: ClaudeCodeExecutor):
        with patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            env = executor._build_env()
            assert "CLAUDECODE" not in env
            assert "PATH" in env


class TestExecuteStreaming:
    @pytest.mark.asyncio
    async def test_streams_json_events(self, executor: ClaudeCodeExecutor, claude_code_tool: ToolDefinition):
        events = [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}),
            json.dumps({"type": "result", "result": "done", "cost_usd": 0.01}),
        ]
        mock_process = _make_mock_process(events, returncode=None)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            chunks = []
            async for chunk in executor.execute_streaming(
                claude_code_tool,
                {"prompt": "test task", "working_directory": "/tmp"},
            ):
                chunks.append(chunk)

        assert len(chunks) == 2
        assert all(c.stream == "stdout" for c in chunks)
        # Initial prompt is passed as CLI arg, not via stdin
        mock_process.stdin.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_handles_non_json_output(self, executor: ClaudeCodeExecutor, claude_code_tool: ToolDefinition):
        lines = [
            "some plain text output",
            json.dumps({"type": "result", "result": "done"}),
        ]
        mock_process = _make_mock_process(lines, returncode=None)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            chunks = []
            async for chunk in executor.execute_streaming(
                claude_code_tool,
                {"prompt": "test", "working_directory": "/tmp"},
            ):
                chunks.append(chunk)

        assert len(chunks) == 2
        assert "some plain text output" in chunks[0].content

    @pytest.mark.asyncio
    async def test_stops_on_result_event(self, executor: ClaudeCodeExecutor, claude_code_tool: ToolDefinition):
        events = [
            json.dumps({"type": "assistant", "message": {"content": []}}),
            json.dumps({"type": "result", "result": "done"}),
            json.dumps({"type": "should_not_appear"}),
        ]
        mock_process = _make_mock_process(events, returncode=None)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            chunks = []
            async for chunk in executor.execute_streaming(
                claude_code_tool,
                {"prompt": "test", "working_directory": "/tmp"},
            ):
                chunks.append(chunk)

        # Should stop after result event, not yield the third event
        assert len(chunks) == 2
        assert executor._done is True
        assert executor.got_result is True
        # Process stays alive — wait should NOT have been called
        mock_process.wait.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_stays_alive_after_result(
        self, executor: ClaudeCodeExecutor, claude_code_tool: ToolDefinition
    ):
        events = [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}),
            json.dumps({"type": "result", "result": "done"}),
        ]
        mock_process = _make_mock_process(events, returncode=None)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            async for _ in executor.execute_streaming(
                claude_code_tool,
                {"prompt": "test", "working_directory": "/tmp"},
            ):
                pass

        # After result the process should still be considered running
        assert executor.is_running is True
        assert executor._done is True

    @pytest.mark.asyncio
    async def test_raises_on_missing_prompt(self, executor: ClaudeCodeExecutor, claude_code_tool: ToolDefinition):
        with pytest.raises(ValueError, match="'prompt' parameter is required"):
            async for _ in executor.execute_streaming(
                claude_code_tool,
                {"working_directory": "/tmp"},
            ):
                pass

    @pytest.mark.asyncio
    async def test_stores_tool_def_for_restart(self, executor: ClaudeCodeExecutor, claude_code_tool: ToolDefinition):
        events = [json.dumps({"type": "result", "result": "done"})]
        mock_process = _make_mock_process(events, returncode=None)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            async for _ in executor.execute_streaming(
                claude_code_tool,
                {"prompt": "test", "working_directory": "/tmp"},
            ):
                pass

        assert executor._tool_def is claude_code_tool
        assert executor._last_parameters == {"prompt": "test", "working_directory": "/tmp"}


class TestStderrDrain:
    @pytest.mark.asyncio
    async def test_captures_stderr(self, executor: ClaudeCodeExecutor, claude_code_tool: ToolDefinition):
        events = [json.dumps({"type": "result", "result": "done"})]
        stderr_lines = ["Warning: something happened", "Debug info line"]
        mock_process = _make_mock_process(events, returncode=None, stderr_lines=stderr_lines)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            async for _ in executor.execute_streaming(
                claude_code_tool,
                {"prompt": "test", "working_directory": "/tmp"},
            ):
                pass

        # Wait for the stderr drain task to finish
        if executor._stderr_task:
            await executor._stderr_task

        assert "Warning: something happened" in executor._stderr_output
        assert "Debug info line" in executor._stderr_output


class TestRestartWithPrompt:
    @pytest.mark.asyncio
    async def test_restart_spawns_new_process(self, executor: ClaudeCodeExecutor, claude_code_tool: ToolDefinition):
        # First run
        events1 = [json.dumps({"type": "result", "result": "done"})]
        mock_process1 = _make_mock_process(events1, returncode=0)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process1)):
            async for _ in executor.execute_streaming(
                claude_code_tool,
                {"prompt": "first task", "working_directory": "/tmp"},
            ):
                pass

        original_session_id = executor.session_id

        # Restart with follow-up
        events2 = [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "follow-up"}]}}),
            json.dumps({"type": "result", "result": "done"}),
        ]
        mock_process2 = _make_mock_process(events2, returncode=None)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process2)):
            chunks = []
            async for chunk in executor.restart_with_prompt("do more work"):
                chunks.append(chunk)

        assert len(chunks) == 2
        # Same session-id is reused
        assert executor.session_id == original_session_id
        # Restart prompt is passed as CLI arg, not via stdin
        mock_process2.stdin.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_restart_raises_without_previous_tool_def(self, executor: ClaudeCodeExecutor):
        with pytest.raises(RuntimeError, match="Cannot restart"):
            async for _ in executor.restart_with_prompt("hello"):
                pass


class TestSendInput:
    @pytest.mark.asyncio
    async def test_sends_stream_json_message(self, executor: ClaudeCodeExecutor):
        mock_process = _make_mock_process([], returncode=None)
        executor._process = mock_process

        await executor.send_input("follow up message")

        mock_process.stdin.write.assert_called_once()
        written = mock_process.stdin.write.call_args[0][0]
        msg = json.loads(written.decode("utf-8").strip())
        assert msg["type"] == "user"
        assert msg["message"]["role"] == "user"
        assert msg["message"]["content"] == "follow up message"

    @pytest.mark.asyncio
    async def test_raises_when_no_process(self, executor: ClaudeCodeExecutor):
        with pytest.raises(RuntimeError, match="No running Claude Code process"):
            await executor.send_input("hello")

    @pytest.mark.asyncio
    async def test_raises_when_process_exited(self, executor: ClaudeCodeExecutor):
        mock_process = _make_mock_process([], returncode=0)
        executor._process = mock_process

        with pytest.raises(RuntimeError, match="already exited"):
            await executor.send_input("hello")


class TestCancel:
    @pytest.mark.asyncio
    async def test_kills_process(self, executor: ClaudeCodeExecutor):
        mock_process = _make_mock_process([], returncode=None)
        executor._process = mock_process

        await executor.cancel()

        mock_process.kill.assert_called_once()
        mock_process.wait.assert_awaited_once()
        assert executor._process is None
        assert executor._done is True

    @pytest.mark.asyncio
    async def test_cancel_when_no_process(self, executor: ClaudeCodeExecutor):
        # Should not raise
        await executor.cancel()


class TestExecute:
    @pytest.mark.asyncio
    async def test_collects_all_output(self, executor: ClaudeCodeExecutor, claude_code_tool: ToolDefinition):
        events = [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "line1"}]}}),
            json.dumps({"type": "result", "result": "done"}),
        ]
        mock_process = _make_mock_process(events, returncode=0)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            result = await executor.execute(
                claude_code_tool,
                {"prompt": "test", "working_directory": "/tmp"},
            )

        assert "assistant" in result.output
        assert "result" in result.output


class TestIsRunning:
    def test_not_running_when_no_process(self, executor: ClaudeCodeExecutor):
        assert executor.is_running is False

    def test_running_when_process_active(self, executor: ClaudeCodeExecutor):
        mock_process = MagicMock()
        mock_process.returncode = None
        executor._process = mock_process
        assert executor.is_running is True

    def test_not_running_when_process_exited(self, executor: ClaudeCodeExecutor):
        mock_process = MagicMock()
        mock_process.returncode = 0
        executor._process = mock_process
        assert executor.is_running is False


class TestSessionId:
    def test_session_id_is_uuid(self, executor: ClaudeCodeExecutor):
        # Should not raise
        uuid.UUID(executor.session_id)

    def test_session_id_stable(self, executor: ClaudeCodeExecutor):
        assert executor.session_id == executor.session_id


class TestFollowUpViaStdin:
    """Exercise the persistent-process follow-up path: send_input() then read_more_events()."""

    @pytest.mark.asyncio
    async def test_follow_up_on_alive_process(self, executor: ClaudeCodeExecutor, claude_code_tool: ToolDefinition):
        # --- first turn ---
        first_events = [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}}),
            json.dumps({"type": "result", "result": "turn1"}),
        ]
        mock_process = _make_mock_process(first_events, returncode=None)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            chunks = []
            async for chunk in executor.execute_streaming(
                claude_code_tool,
                {"prompt": "initial task", "working_directory": "/tmp"},
            ):
                chunks.append(chunk)

        assert len(chunks) == 2
        assert executor.is_running is True

        # --- follow-up turn via stdin ---
        follow_up_events = [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "follow-up"}]}}),
            json.dumps({"type": "result", "result": "turn2"}),
        ]
        # Replace stdout with new lines for the follow-up read
        follow_up_data = iter([line.encode("utf-8") + b"\n" for line in follow_up_events] + [b""])
        mock_process.stdout.readline = AsyncMock(side_effect=follow_up_data)

        await executor.send_input("do more work")

        follow_chunks = []
        async for chunk in executor.read_more_events():
            follow_chunks.append(chunk)

        assert len(follow_chunks) == 2
        # Follow-up is sent via stdin (initial prompt was a CLI arg, so this is the first stdin write)
        assert mock_process.stdin.write.call_count == 1
        last_written = mock_process.stdin.write.call_args[0][0]
        msg = json.loads(last_written.decode("utf-8").strip())
        assert msg["type"] == "user"
        assert msg["message"]["role"] == "user"
        assert msg["message"]["content"] == "do more work"

    @pytest.mark.asyncio
    async def test_read_more_events_resets_done_flag(self, executor: ClaudeCodeExecutor):
        """read_more_events() must clear _done so _read_events() enters the loop."""
        events = [
            json.dumps({"type": "result", "result": "turn2"}),
        ]
        mock_process = _make_mock_process(events, returncode=None)
        executor._process = mock_process
        executor._done = True  # leftover from previous turn

        chunks = []
        async for chunk in executor.read_more_events():
            chunks.append(chunk)

        assert len(chunks) == 1
        assert executor._done is True  # set again by result event


class TestGotResult:
    """Tests for the got_result flag that distinguishes normal completion from unexpected exit."""

    @pytest.mark.asyncio
    async def test_got_result_true_on_result_event(
        self, executor: ClaudeCodeExecutor, claude_code_tool: ToolDefinition
    ):
        events = [
            json.dumps({"type": "result", "result": "done"}),
        ]
        mock_process = _make_mock_process(events, returncode=None)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            async for _ in executor.execute_streaming(
                claude_code_tool,
                {"prompt": "test", "working_directory": "/tmp"},
            ):
                pass

        assert executor.got_result is True

    @pytest.mark.asyncio
    async def test_got_result_false_on_eof(
        self, executor: ClaudeCodeExecutor, claude_code_tool: ToolDefinition
    ):
        """When process exits without emitting a result event, got_result should be False."""
        events = [
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "working..."}]}}),
            # No result event — process exits (EOF)
        ]
        mock_process = _make_mock_process(events, returncode=1)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            chunks = []
            async for chunk in executor.execute_streaming(
                claude_code_tool,
                {"prompt": "test", "working_directory": "/tmp"},
            ):
                chunks.append(chunk)

        assert executor.got_result is False
        assert executor._done is True

    @pytest.mark.asyncio
    async def test_got_result_resets_on_read_more(self, executor: ClaudeCodeExecutor):
        """read_more_events() must reset got_result so a new read can set it."""
        events = [
            json.dumps({"type": "result", "result": "turn2"}),
        ]
        mock_process = _make_mock_process(events, returncode=None)
        executor._process = mock_process
        executor._got_result = True  # leftover from previous turn

        chunks = []
        async for chunk in executor.read_more_events():
            chunks.append(chunk)

        assert executor.got_result is True  # set again by result event

    @pytest.mark.asyncio
    async def test_got_result_false_on_eof_after_read_more(self, executor: ClaudeCodeExecutor):
        """When follow-up read ends on EOF without result, got_result is False."""
        # No events — immediate EOF
        mock_process = _make_mock_process([], returncode=0)
        executor._process = mock_process
        executor._got_result = True  # leftover from previous turn

        chunks = []
        async for chunk in executor.read_more_events():
            chunks.append(chunk)

        assert len(chunks) == 0
        assert executor.got_result is False


class TestExitCode:
    def test_exit_code_none_when_no_process(self, executor: ClaudeCodeExecutor):
        assert executor.exit_code is None

    def test_exit_code_when_process_exited(self, executor: ClaudeCodeExecutor):
        mock_process = MagicMock()
        mock_process.returncode = 137
        executor._process = mock_process
        assert executor.exit_code == 137

    def test_exit_code_none_when_still_running(self, executor: ClaudeCodeExecutor):
        mock_process = MagicMock()
        mock_process.returncode = None
        executor._process = mock_process
        assert executor.exit_code is None
