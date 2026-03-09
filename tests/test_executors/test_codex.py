import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.executors.codex import CodexExecutor
from src.tools.loader import ToolDefinition


@pytest.fixture
def codex_tool() -> ToolDefinition:
    return ToolDefinition(
        name="codex",
        description="OpenAI Codex agent",
        version="1.0.0",
        session_type="long-running",
        llm_context="session-scoped",
        executor="codex",
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "working_directory": {"type": "string"},
            },
            "required": ["prompt", "working_directory"],
        },
        executor_config={
            "codex": {
                "binary_path": "codex",
                "approval_mode": "full-auto",
                "model": "",
                "timeout": 600,
            }
        },
    )


@pytest.fixture
def executor() -> CodexExecutor:
    return CodexExecutor(binary_path="/usr/bin/codex")


def _make_mock_process(
    stdout_lines: list[str],
    returncode: int | None = None,
    stderr_lines: list[str] | None = None,
) -> MagicMock:
    """Create a mock subprocess with predefined stdout/stderr lines.

    The mock starts with ``returncode = None`` (process still running) so that
    the ``_start_process`` early-exit check passes.  Once stdout is fully
    drained (readline returns ``b""``), returncode switches to the final value.
    """
    process = MagicMock()
    process.pid = 999999  # Non-existent PID so os.killpg raises ProcessLookupError
    # Start as None (running); will be set to final value when stdout is exhausted
    process.returncode = None
    _final_returncode = returncode

    # Build an async readline for stdout that sets returncode on EOF
    encoded_lines = [line.encode("utf-8") + b"\n" for line in stdout_lines]

    async def _stdout_readline() -> bytes:
        if encoded_lines:
            return encoded_lines.pop(0)
        # EOF — process has exited
        process.returncode = _final_returncode
        return b""

    stdout_reader = MagicMock()
    stdout_reader.readline = _stdout_readline
    process.stdout = stdout_reader

    # Build an async readline for stderr (drain task reads this)
    stderr_data = iter([line.encode("utf-8") + b"\n" for line in (stderr_lines or [])] + [b""])
    stderr_reader = MagicMock()
    stderr_reader.readline = AsyncMock(side_effect=stderr_data)
    process.stderr = stderr_reader

    process.stdin = MagicMock()
    process.stdin.write = MagicMock()
    process.stdin.close = MagicMock()
    process.stdin.drain = AsyncMock()
    process.stdin.wait_closed = AsyncMock()
    process.kill = MagicMock()
    process.wait = AsyncMock()

    return process


class TestBuildCommand:
    def test_basic_command(self, executor: CodexExecutor):
        config = {
            "binary_path": "codex",
            "approval_mode": "full-auto",
            "model": "",
            "timeout": 600,
        }
        cmd = executor._build_command({"prompt": "hello"}, config)
        assert cmd[0] == "/usr/bin/codex"
        assert "exec" in cmd
        assert "--json" in cmd
        assert "--skip-git-repo-check" in cmd
        assert "--full-auto" in cmd

    def test_with_model_override(self, executor: CodexExecutor):
        config = {"approval_mode": "full-auto"}
        params = {"prompt": "hello", "model": "o3"}
        cmd = executor._build_command(params, config)
        assert "--model" in cmd
        assert "o3" in cmd

    def test_with_working_directory(self, executor: CodexExecutor):
        config = {"approval_mode": "full-auto"}
        params = {"prompt": "hello", "working_directory": "/home/user/project"}
        cmd = executor._build_command(params, config)
        assert "--cd" in cmd
        assert "/home/user/project" in cmd

    def test_with_resume(self, executor: CodexExecutor):
        executor._thread_id = "test-thread-123"
        config = {"approval_mode": "full-auto"}
        params = {"prompt": "follow up"}
        cmd = executor._build_command(params, config, resume=True)
        assert "resume" in cmd
        assert "test-thread-123" in cmd

    def test_resume_without_thread_id(self, executor: CodexExecutor):
        config = {"approval_mode": "full-auto"}
        params = {"prompt": "follow up"}
        cmd = executor._build_command(params, config, resume=True)
        # Should not include resume args when thread_id is None
        assert "resume" not in cmd

    def test_yolo_mode(self, executor: CodexExecutor):
        config = {"approval_mode": "yolo"}
        params = {"prompt": "hello"}
        cmd = executor._build_command(params, config)
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd
        assert "--full-auto" not in cmd

    def test_config_model_fallback(self, executor: CodexExecutor):
        config = {"approval_mode": "full-auto", "model": "gpt-5-codex"}
        params = {"prompt": "hello"}
        cmd = executor._build_command(params, config)
        assert "--model" in cmd
        assert "gpt-5-codex" in cmd

    def test_params_model_overrides_config(self, executor: CodexExecutor):
        config = {"approval_mode": "full-auto", "model": "gpt-5-codex"}
        params = {"prompt": "hello", "model": "o3"}
        cmd = executor._build_command(params, config)
        assert "--model" in cmd
        assert "o3" in cmd
        assert "gpt-5-codex" not in cmd


class TestBuildEnv:
    def test_injects_extra_env(self, executor: CodexExecutor):
        executor._extra_env = {"CODEX_API_KEY": "test-key"}
        with patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            env = executor._build_env()
            assert env["CODEX_API_KEY"] == "test-key"
            assert env["PATH"] == "/usr/bin"

    def test_handles_missing_env_vars(self, executor: CodexExecutor):
        with patch.dict("os.environ", {"PATH": "/usr/bin"}, clear=True):
            env = executor._build_env()
            assert "PATH" in env


class TestExecuteStreaming:
    @pytest.mark.asyncio
    async def test_streams_jsonl_events(self, executor: CodexExecutor, codex_tool: ToolDefinition):
        events = [
            json.dumps({"type": "thread.started", "thread_id": "abc-123"}),
            json.dumps({"type": "turn.started"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_1", "type": "agent_message", "text": "hello"},
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 50}}),
        ]
        mock_process = _make_mock_process(events, returncode=0)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            chunks = []
            async for chunk in executor.execute_streaming(
                codex_tool,
                {"prompt": "test task", "working_directory": "/tmp"},
            ):
                chunks.append(chunk)

        assert len(chunks) == 4
        assert all(c.stream == "stdout" for c in chunks)
        # Verify prompt was written to stdin and stdin was closed
        mock_process.stdin.write.assert_called_once()
        written = mock_process.stdin.write.call_args[0][0]
        assert written == b"test task"
        mock_process.stdin.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_extracts_thread_id(self, executor: CodexExecutor, codex_tool: ToolDefinition):
        events = [
            json.dumps({"type": "thread.started", "thread_id": "my-thread-id"}),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
        mock_process = _make_mock_process(events, returncode=0)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            async for _ in executor.execute_streaming(
                codex_tool,
                {"prompt": "test", "working_directory": "/tmp"},
            ):
                pass

        assert executor.thread_id == "my-thread-id"

    @pytest.mark.asyncio
    async def test_handles_non_json_output(self, executor: CodexExecutor, codex_tool: ToolDefinition):
        lines = [
            "some plain text output",
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
        mock_process = _make_mock_process(lines, returncode=0)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            chunks = []
            async for chunk in executor.execute_streaming(
                codex_tool,
                {"prompt": "test", "working_directory": "/tmp"},
            ):
                chunks.append(chunk)

        assert len(chunks) == 2
        assert "some plain text output" in chunks[0].content

    @pytest.mark.asyncio
    async def test_stops_on_turn_completed(self, executor: CodexExecutor, codex_tool: ToolDefinition):
        events = [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "turn.completed", "usage": {}}),
            json.dumps({"type": "should_not_appear"}),
        ]
        mock_process = _make_mock_process(events, returncode=0)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            chunks = []
            async for chunk in executor.execute_streaming(
                codex_tool,
                {"prompt": "test", "working_directory": "/tmp"},
            ):
                chunks.append(chunk)

        # Should stop after turn.completed, not yield the third event
        assert len(chunks) == 2
        assert executor._done is True

    @pytest.mark.asyncio
    async def test_stops_on_turn_failed(self, executor: CodexExecutor, codex_tool: ToolDefinition):
        events = [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps({"type": "turn.failed", "error": {"message": "something broke"}}),
        ]
        mock_process = _make_mock_process(events, returncode=1)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            chunks = []
            async for chunk in executor.execute_streaming(
                codex_tool,
                {"prompt": "test", "working_directory": "/tmp"},
            ):
                chunks.append(chunk)

        assert len(chunks) == 2
        assert executor._done is True

    @pytest.mark.asyncio
    async def test_raises_on_missing_prompt(self, executor: CodexExecutor, codex_tool: ToolDefinition):
        with pytest.raises(ValueError, match="'prompt' parameter is required"):
            async for _ in executor.execute_streaming(
                codex_tool,
                {"working_directory": "/tmp"},
            ):
                pass

    @pytest.mark.asyncio
    async def test_stores_tool_def_for_restart(self, executor: CodexExecutor, codex_tool: ToolDefinition):
        events = [json.dumps({"type": "turn.completed", "usage": {}})]
        mock_process = _make_mock_process(events, returncode=0)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            async for _ in executor.execute_streaming(
                codex_tool,
                {"prompt": "test", "working_directory": "/tmp"},
            ):
                pass

        assert executor._tool_def is codex_tool
        assert executor._last_parameters == {"prompt": "test", "working_directory": "/tmp"}


class TestStderrDrain:
    @pytest.mark.asyncio
    async def test_captures_stderr(self, executor: CodexExecutor, codex_tool: ToolDefinition):
        events = [json.dumps({"type": "turn.completed", "usage": {}})]
        stderr_lines = ["Warning: something happened", "Debug info line"]
        mock_process = _make_mock_process(events, returncode=0, stderr_lines=stderr_lines)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            async for _ in executor.execute_streaming(
                codex_tool,
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
    async def test_restart_spawns_new_process(self, executor: CodexExecutor, codex_tool: ToolDefinition):
        # First run
        events1 = [
            json.dumps({"type": "thread.started", "thread_id": "thread-abc"}),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
        mock_process1 = _make_mock_process(events1, returncode=0)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process1)):
            async for _ in executor.execute_streaming(
                codex_tool,
                {"prompt": "first task", "working_directory": "/tmp"},
            ):
                pass

        assert executor.thread_id == "thread-abc"

        # Restart with follow-up
        events2 = [
            json.dumps({"type": "thread.started", "thread_id": "thread-abc"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_1", "type": "agent_message", "text": "follow-up done"},
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
        mock_process2 = _make_mock_process(events2, returncode=0)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process2)) as mock_exec:
            chunks = []
            async for chunk in executor.restart_with_prompt("do more work"):
                chunks.append(chunk)

        assert len(chunks) == 3
        # Same thread-id is reused
        assert executor.thread_id == "thread-abc"
        # The new prompt was sent to stdin
        mock_process2.stdin.write.assert_called_once()
        written = mock_process2.stdin.write.call_args[0][0]
        assert written == b"do more work"
        mock_process2.stdin.close.assert_called_once()
        # Verify resume was used in command
        cmd_args = mock_exec.call_args[0]
        assert "resume" in cmd_args
        assert "thread-abc" in cmd_args

    @pytest.mark.asyncio
    async def test_restart_raises_without_previous_tool_def(self, executor: CodexExecutor):
        with pytest.raises(RuntimeError, match="Cannot restart"):
            async for _ in executor.restart_with_prompt("hello"):
                pass

    @pytest.mark.asyncio
    async def test_restart_raises_without_thread_id(self, executor: CodexExecutor, codex_tool: ToolDefinition):
        executor._tool_def = codex_tool
        executor._thread_id = None
        with pytest.raises(RuntimeError, match="no thread ID"):
            async for _ in executor.restart_with_prompt("hello"):
                pass


class TestSendInput:
    @pytest.mark.asyncio
    async def test_raises_not_supported(self, executor: CodexExecutor):
        with pytest.raises(RuntimeError, match="does not support interactive stdin"):
            await executor.send_input("hello")


class TestReadMoreEvents:
    @pytest.mark.asyncio
    async def test_raises_not_supported(self, executor: CodexExecutor):
        with pytest.raises(RuntimeError, match="does not support reading more events"):
            async for _ in executor.read_more_events():
                pass


class TestCancel:
    @pytest.mark.asyncio
    async def test_kills_process(self, executor: CodexExecutor):
        mock_process = _make_mock_process([], returncode=None)
        executor._process = mock_process

        await executor.cancel()

        mock_process.kill.assert_called_once()
        mock_process.wait.assert_awaited_once()
        assert executor._process is None
        assert executor._done is True

    @pytest.mark.asyncio
    async def test_cancel_when_no_process(self, executor: CodexExecutor):
        # Should not raise
        await executor.cancel()


class TestExecute:
    @pytest.mark.asyncio
    async def test_collects_all_output(self, executor: CodexExecutor, codex_tool: ToolDefinition):
        events = [
            json.dumps({"type": "thread.started", "thread_id": "t1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"id": "item_1", "type": "agent_message", "text": "done"},
                }
            ),
            json.dumps({"type": "turn.completed", "usage": {}}),
        ]
        mock_process = _make_mock_process(events, returncode=0)

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_process)):
            result = await executor.execute(
                codex_tool,
                {"prompt": "test", "working_directory": "/tmp"},
            )

        assert "thread.started" in result.output
        assert "turn.completed" in result.output


class TestIsRunning:
    def test_not_running_when_no_process(self, executor: CodexExecutor):
        assert executor.is_running is False

    def test_running_when_process_active(self, executor: CodexExecutor):
        mock_process = MagicMock()
        mock_process.returncode = None
        executor._process = mock_process
        assert executor.is_running is True

    def test_not_running_when_process_exited(self, executor: CodexExecutor):
        mock_process = MagicMock()
        mock_process.returncode = 0
        executor._process = mock_process
        assert executor.is_running is False


class TestThreadId:
    def test_thread_id_none_initially(self, executor: CodexExecutor):
        assert executor.thread_id is None

    def test_thread_id_preserved_from_constructor(self):
        executor = CodexExecutor(thread_id="existing-thread")
        assert executor.thread_id == "existing-thread"
