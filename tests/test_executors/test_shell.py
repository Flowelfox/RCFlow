import asyncio
import time
from pathlib import Path

import pytest

from src.executors.shell import ShellExecutor, _quote_params_for_shell
from src.tools.loader import ToolDefinition, load_tool_file


class TestQuoteParamsForShell:
    def test_powershell_neutralizes_subexpression_injection(self) -> None:
        # A $(...) subexpression must be rendered inert. Single-quoting (the fix)
        # makes PowerShell treat the value literally; the old double-quote path
        # would still evaluate $(...).
        payload = "$(Remove-Item C:\\ -Recurse)"
        quoted = _quote_params_for_shell({"path": payload}, "Get-Item {path}", is_powershell=True)
        assert quoted["path"] == "'" + payload + "'"
        assert not quoted["path"].startswith('"')

    def test_powershell_doubles_embedded_single_quotes(self) -> None:
        quoted = _quote_params_for_shell({"p": "a'b"}, "echo {p}", is_powershell=True)
        assert quoted["p"] == "'a''b'"

    def test_posix_uses_shlex_quote(self) -> None:
        quoted = _quote_params_for_shell({"p": "a b;rm -rf /"}, "echo {p}", is_powershell=False)
        # shlex.quote wraps the whole thing so the ; is inert
        assert quoted["p"].startswith("'") and ";" in quoted["p"]

    def test_whole_template_param_passes_through_unquoted(self) -> None:
        quoted = _quote_params_for_shell({"command": "ls -la | grep x"}, "{command}", is_powershell=True)
        assert quoted["command"] == "ls -la | grep x"


@pytest.fixture
def shell_executor() -> ShellExecutor:
    return ShellExecutor()


@pytest.fixture
def shell_exec_tool():
    tools_dir = Path(__file__).parent.parent.parent / "tools"
    return load_tool_file(tools_dir / "shell_exec.json")


class TestShellExecutor:
    @pytest.mark.asyncio
    async def test_execute_simple_command(self, shell_executor: ShellExecutor, shell_exec_tool):
        result = await shell_executor.execute(shell_exec_tool, {"command": "echo hello"})
        assert result.exit_code == 0
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_execute_failing_command(self, shell_executor: ShellExecutor, shell_exec_tool):
        result = await shell_executor.execute(shell_exec_tool, {"command": "false"})
        assert result.exit_code != 0

    @pytest.mark.asyncio
    async def test_execute_timeout(self, shell_executor: ShellExecutor, shell_exec_tool):
        result = await shell_executor.execute(shell_exec_tool, {"command": "sleep 10", "timeout": 1})
        assert result.exit_code == -1
        assert "timed out" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_execute_streaming(self, shell_executor: ShellExecutor, shell_exec_tool):
        chunks = []
        async for chunk in shell_executor.execute_streaming(shell_exec_tool, {"command": "echo line1 && echo line2"}):
            chunks.append(chunk)

        assert chunks
        output = "".join(c.content for c in chunks)
        assert "line1" in output
        assert "line2" in output

    @pytest.mark.asyncio
    async def test_streaming_large_stderr_does_not_deadlock(self, shell_executor: ShellExecutor, shell_exec_tool):
        # Child writes 300 KB to stderr *before* touching stdout. With sequential
        # reads (stdout to EOF first) this deadlocks; concurrent reads must not.
        cmd = 'python3 -c \'import sys; sys.stderr.write("e"*300000); sys.stdout.write("OUT\\n")\''

        async def _collect():
            out = []
            async for chunk in shell_executor.execute_streaming(shell_exec_tool, {"command": cmd}):
                out.append(chunk)
            return out

        chunks = await asyncio.wait_for(_collect(), timeout=15)
        assert any("OUT" in c.content for c in chunks)

    @pytest.mark.asyncio
    async def test_streaming_timeout_stops_and_kills(self, shell_executor: ShellExecutor, shell_exec_tool):
        async def _collect():
            out = []
            async for chunk in shell_executor.execute_streaming(shell_exec_tool, {"command": "sleep 10", "timeout": 1}):
                out.append(chunk)
            return out

        start = time.monotonic()
        await asyncio.wait_for(_collect(), timeout=8)
        # The stream honors the 1s timeout instead of running the full sleep.
        assert time.monotonic() - start < 6


class TestRcflowPlaceholder:
    @pytest.mark.asyncio
    async def test_rcflow_placeholder_resolves_to_self_invocation(self):
        tool = ToolDefinition(
            name="selfcall",
            description="d",
            session_type="one-shot",
            llm_context="stateless",
            executor="shell",
            parameters={"type": "object", "properties": {}},
            executor_config={"shell": {"command_template": "{rcflow} system-info os", "stream_output": False}},
        )
        result = await ShellExecutor().execute(tool, {})
        assert result.exit_code == 0, result.error
        assert '"system"' in result.output
