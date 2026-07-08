from pathlib import Path

import pytest

from src.executors.shell import ShellExecutor
from src.tools.loader import ToolDefinition, load_tool_file


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

        assert len(chunks) >= 2
        output = "".join(c.content for c in chunks)
        assert "line1" in output
        assert "line2" in output


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
