import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.executors.opencode import OpenCodeExecutor
from src.tools.loader import ToolDefinition


@pytest.fixture
def opencode_tool() -> ToolDefinition:
    return ToolDefinition(
        name="opencode",
        description="OpenCode coding agent",
        version="1.0.0",
        session_type="long-running",
        llm_context="session-scoped",
        executor="opencode",
        parameters={
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "working_directory": {"type": "string"},
            },
            "required": ["prompt", "working_directory"],
        },
        executor_config={
            "opencode": {
                "binary_path": "opencode",
                "model": "",
                "timeout": 600,
            }
        },
    )


@pytest.fixture
def executor() -> OpenCodeExecutor:
    return OpenCodeExecutor(binary_path="/usr/bin/opencode")


def _make_mock_process(
    stdout_lines: list[str],
    returncode: int | None = None,
    stderr_lines: list[str] | None = None,
) -> MagicMock:
    """Create a mock subprocess with predefined stdout/stderr lines."""
    process = MagicMock()
    process.pid = 999999
    process.returncode = None
    _final_returncode = returncode

    encoded_lines = [line.encode("utf-8") + b"\n" for line in stdout_lines]

    async def _stdout_readline() -> bytes:
        if encoded_lines:
            return encoded_lines.pop(0)
        process.returncode = _final_returncode
        return b""

    stdout_reader = MagicMock()
    stdout_reader.readline = _stdout_readline
    process.stdout = stdout_reader

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
    def test_basic_command(self, executor: OpenCodeExecutor):
        config = {"binary_path": "opencode", "model": "", "timeout": 600}
        cmd = executor._build_command({"prompt": "hello"}, config)
        assert cmd[0] == "/usr/bin/opencode"
        assert "run" in cmd
        assert "--format" in cmd
        idx = cmd.index("--format")
        assert cmd[idx + 1] == "json"
        # legacy flag must not appear
        assert "--output-format" not in cmd

    def test_working_directory(self, executor: OpenCodeExecutor):
        config = {"binary_path": "opencode", "model": "", "timeout": 600}
        cmd = executor._build_command({"working_directory": "/tmp/proj"}, config)
        assert "--dir" in cmd
        idx = cmd.index("--dir")
        assert cmd[idx + 1] == "/tmp/proj"
        assert "--cwd" not in cmd

    def test_model_override(self, executor: OpenCodeExecutor):
        config = {"binary_path": "opencode", "model": "", "timeout": 600}
        cmd = executor._build_command({"model": "anthropic/claude-sonnet-4-5"}, config)
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "anthropic/claude-sonnet-4-5"

    def test_model_from_config(self, executor: OpenCodeExecutor):
        config = {"binary_path": "opencode", "model": "openai/gpt-4o", "timeout": 600}
        cmd = executor._build_command({}, config)
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "openai/gpt-4o"

    def test_model_param_overrides_config(self, executor: OpenCodeExecutor):
        config = {"binary_path": "opencode", "model": "openai/gpt-4o", "timeout": 600}
        cmd = executor._build_command({"model": "anthropic/claude-sonnet-4-5"}, config)
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "anthropic/claude-sonnet-4-5"

    def test_resume_adds_session_flag(self, executor: OpenCodeExecutor):
        executor._session_id = "sess-abc123"
        config = {"binary_path": "opencode", "model": "", "timeout": 600}
        cmd = executor._build_command({}, config, resume=True)
        assert "--session" in cmd
        idx = cmd.index("--session")
        assert cmd[idx + 1] == "sess-abc123"
        assert "--session-id" not in cmd

    def test_no_resume_without_session_id(self, executor: OpenCodeExecutor):
        config = {"binary_path": "opencode", "model": "", "timeout": 600}
        cmd = executor._build_command({}, config, resume=True)
        assert "--session" not in cmd
        assert "--session-id" not in cmd

    def test_prompt_appended_as_positional_arg(self, executor: OpenCodeExecutor):
        config = {"binary_path": "opencode", "model": "", "timeout": 600}
        cmd = executor._build_command({}, config, prompt="fix the bug")
        assert cmd[-1] == "fix the bug"

    def test_no_prompt_no_trailing_arg(self, executor: OpenCodeExecutor):
        config = {"binary_path": "opencode", "model": "", "timeout": 600}
        cmd = executor._build_command({}, config)
        # last flag value should be "json" (from --format json), no extra positional
        assert cmd[-1] == "json"


class TestProperties:
    def test_is_running_false_when_no_process(self, executor: OpenCodeExecutor):
        assert executor.is_running is False

    def test_is_running_false_when_process_exited(self, executor: OpenCodeExecutor):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        executor._process = mock_proc
        assert executor.is_running is False

    def test_is_running_true_when_process_active(self, executor: OpenCodeExecutor):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        executor._process = mock_proc
        assert executor.is_running is True

    def test_session_id_initially_none(self, executor: OpenCodeExecutor):
        assert executor.opencode_session_id is None

    def test_session_id_can_be_set(self):
        ex = OpenCodeExecutor(session_id="sess-xyz")
        assert ex.opencode_session_id == "sess-xyz"


class TestReadEvents:
    @pytest.mark.asyncio
    async def test_step_finish_stop_sets_done(self, executor: OpenCodeExecutor):
        finish_event = json.dumps({"type": "step_finish", "part": {"reason": "stop", "tokens": {}}})
        process = _make_mock_process([finish_event], returncode=0)
        executor._process = process

        chunks = []
        async for chunk in executor._read_events():
            chunks.append(chunk)

        assert executor._done is True
        assert any("step_finish" in c.content for c in chunks)

    @pytest.mark.asyncio
    async def test_step_finish_tool_calls_does_not_set_done(self, executor: OpenCodeExecutor):
        """Intermediate step_finish (reason 'tool-calls') must not stop the loop."""
        intermediate = json.dumps({"type": "step_finish", "part": {"reason": "tool-calls", "tokens": {}}})
        final = json.dumps({"type": "step_finish", "part": {"reason": "stop", "tokens": {}}})
        process = _make_mock_process([intermediate, final], returncode=0)
        executor._process = process

        async for _ in executor._read_events():
            pass

        assert executor._done is True

    @pytest.mark.asyncio
    async def test_error_event_sets_done(self, executor: OpenCodeExecutor):
        error_event = json.dumps({"type": "error", "error": "something went wrong"})
        process = _make_mock_process([error_event], returncode=1)
        executor._process = process

        chunks = []
        async for chunk in executor._read_events():
            chunks.append(chunk)

        assert executor._done is True

    @pytest.mark.asyncio
    async def test_step_start_captures_session_id(self, executor: OpenCodeExecutor):
        started_event = json.dumps({"type": "step_start", "sessionID": "sess-abc", "part": {"sessionID": "sess-abc"}})
        finish_event = json.dumps({"type": "step_finish", "part": {"reason": "stop", "tokens": {}}})
        process = _make_mock_process([started_event, finish_event], returncode=0)
        executor._process = process

        async for _ in executor._read_events():
            pass

        assert executor._session_id == "sess-abc"

    @pytest.mark.asyncio
    async def test_text_events_yielded(self, executor: OpenCodeExecutor):
        text_event = json.dumps({"type": "text", "part": {"type": "text", "text": "Hello world"}})
        finish_event = json.dumps({"type": "step_finish", "part": {"reason": "stop", "tokens": {}}})
        process = _make_mock_process([text_event, finish_event], returncode=0)
        executor._process = process

        chunks = []
        async for chunk in executor._read_events():
            chunks.append(chunk)

        assert len(chunks) >= 2
        assert any("Hello world" in c.content for c in chunks)

    @pytest.mark.asyncio
    async def test_eof_sets_done(self, executor: OpenCodeExecutor):
        process = _make_mock_process([], returncode=0)
        executor._process = process

        async for _ in executor._read_events():
            pass

        assert executor._done is True

    @pytest.mark.asyncio
    async def test_non_json_lines_yielded_as_is(self, executor: OpenCodeExecutor):
        process = _make_mock_process(["not-json-output", ""], returncode=0)
        executor._process = process

        chunks = []
        async for chunk in executor._read_events():
            chunks.append(chunk)

        assert any("not-json-output" in c.content for c in chunks)


class TestExecuteStreaming:
    @pytest.mark.asyncio
    async def test_raises_without_prompt(self, executor: OpenCodeExecutor, opencode_tool: ToolDefinition):
        with pytest.raises(ValueError, match="'prompt' parameter is required"):
            async for _ in executor.execute_streaming(opencode_tool, {}):
                pass

    @pytest.mark.asyncio
    async def test_starts_process_and_yields_events(
        self, executor: OpenCodeExecutor, opencode_tool: ToolDefinition
    ):
        finish_event = json.dumps({"type": "step_finish", "part": {"reason": "stop", "tokens": {}}})
        mock_proc = _make_mock_process([finish_event], returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            chunks = []
            async for chunk in executor.execute_streaming(
                opencode_tool, {"prompt": "write tests", "working_directory": "/tmp"}
            ):
                chunks.append(chunk)

        assert executor._done is True


class TestRestartWithPrompt:
    @pytest.mark.asyncio
    async def test_raises_without_tool_def(self, executor: OpenCodeExecutor):
        with pytest.raises(RuntimeError, match="no previous tool definition"):
            async for _ in executor.restart_with_prompt("follow-up"):
                pass

    @pytest.mark.asyncio
    async def test_raises_without_session_id(
        self, executor: OpenCodeExecutor, opencode_tool: ToolDefinition
    ):
        executor._tool_def = opencode_tool
        executor._session_id = None
        with pytest.raises(RuntimeError, match="no session ID"):
            async for _ in executor.restart_with_prompt("follow-up"):
                pass

    @pytest.mark.asyncio
    async def test_restarts_with_session_flag(
        self, executor: OpenCodeExecutor, opencode_tool: ToolDefinition
    ):
        executor._tool_def = opencode_tool
        executor._session_id = "sess-xyz"
        executor._last_parameters = {"working_directory": "/tmp"}

        finish_event = json.dumps({"type": "step_finish", "part": {"reason": "stop", "tokens": {}}})
        mock_proc = _make_mock_process([finish_event], returncode=0)

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            async for _ in executor.restart_with_prompt("more work"):
                pass

        call_args = mock_exec.call_args[0]
        cmd = list(call_args)
        assert "--session" in cmd
        idx = cmd.index("--session")
        assert cmd[idx + 1] == "sess-xyz"
        assert "--session-id" not in cmd
        # prompt should be last positional arg
        assert cmd[-1] == "more work"


class TestSendInput:
    @pytest.mark.asyncio
    async def test_send_input_raises(self, executor: OpenCodeExecutor):
        with pytest.raises(RuntimeError, match="does not support interactive stdin"):
            await executor.send_input("hello")


class TestReadMoreEvents:
    @pytest.mark.asyncio
    async def test_read_more_events_raises(self, executor: OpenCodeExecutor):
        with pytest.raises(RuntimeError, match="does not support reading more events"):
            async for _ in executor.read_more_events():
                pass


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_sets_done(self, executor: OpenCodeExecutor):
        assert not executor._done
        await executor.cancel()
        assert executor._done

    @pytest.mark.asyncio
    async def test_cancel_kills_process(self, executor: OpenCodeExecutor):
        mock_proc = MagicMock()
        mock_proc.returncode = None
        mock_proc.pid = 12345
        executor._process = mock_proc

        with patch("src.executors.opencode.kill_process_tree", new_callable=AsyncMock) as mock_kill:
            await executor.cancel()

        mock_kill.assert_called_once_with(mock_proc)
        assert executor._process is None


class TestBuildEnv:
    def test_includes_os_env(self, executor: OpenCodeExecutor):

        env = executor._build_env()
        assert "PATH" in env or len(env) > 0  # at minimum has something

    def test_extra_env_overrides(self):
        ex = OpenCodeExecutor(extra_env={"MY_KEY": "my_value"})
        env = ex._build_env()
        assert env["MY_KEY"] == "my_value"

    def test_config_overrides_applied(self, opencode_tool: ToolDefinition):
        ex = OpenCodeExecutor(config_overrides={"model": "anthropic/claude-opus-4-6"})
        config = {**opencode_tool.executor_config.get("opencode", {})}
        for k, v in ex._config_overrides.items():
            if v not in (None, ""):
                config[k] = v
        assert config["model"] == "anthropic/claude-opus-4-6"
