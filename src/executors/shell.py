import asyncio
import logging
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import PurePath
from typing import Any

from src.executors.base import BaseExecutor, ExecutionChunk, ExecutionResult
from src.tools.loader import ToolDefinition

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"
_POWERSHELL_NAMES = {"powershell.exe", "powershell", "pwsh.exe", "pwsh"}


class ShellExecutor(BaseExecutor):
    def __init__(self) -> None:
        self._process: asyncio.subprocess.Process | None = None

    @staticmethod
    def _is_powershell(shell: str) -> bool:
        """Check if the configured shell is a PowerShell variant."""
        return PurePath(shell).name.lower() in _POWERSHELL_NAMES

    async def _create_process(
        self,
        command: str,
        shell: str,
        *,
        capture_stderr: bool = True,
        stdin_pipe: bool = False,
        cwd: str = ".",
    ) -> asyncio.subprocess.Process:
        """Create a subprocess, handling Windows shell differences.

        On Windows, ``create_subprocess_shell`` with ``executable=powershell.exe``
        produces ``powershell.exe /c <command>`` which is invalid (PowerShell uses
        ``-Command``, not ``/c``).  We use ``create_subprocess_exec`` with the
        correct flags instead.
        """
        stderr = asyncio.subprocess.PIPE if capture_stderr else None
        stdin = asyncio.subprocess.PIPE if stdin_pipe else None

        if _IS_WINDOWS and self._is_powershell(shell):
            return await asyncio.create_subprocess_exec(
                shell,
                "-NoProfile",
                "-Command",
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=stderr,
                stdin=stdin,
                cwd=cwd,
            )

        return await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr,
            stdin=stdin,
            cwd=cwd,
            executable=shell if not _IS_WINDOWS else None,
        )

    async def execute(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> ExecutionResult:
        config = tool.get_shell_config()
        command = config.command_template.format(**parameters)
        timeout = parameters.get("timeout", 30)
        working_dir = parameters.get("working_directory", ".")

        try:
            process = await self._create_process(
                command,
                config.shell,
                capture_stderr=config.capture_stderr,
                cwd=working_dir,
            )
            self._process = process

            stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)

            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            output = stdout
            if stderr:
                output += f"\n[stderr]\n{stderr}"

            return ExecutionResult(
                output=output,
                exit_code=process.returncode,
                error=stderr if process.returncode != 0 else None,
            )
        except TimeoutError:
            if self._process:
                self._process.kill()
            return ExecutionResult(
                output="",
                exit_code=-1,
                error=f"Command timed out after {timeout} seconds",
            )
        except Exception as e:
            return ExecutionResult(
                output="",
                exit_code=-1,
                error=str(e),
            )
        finally:
            self._process = None

    async def execute_streaming(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> AsyncGenerator[ExecutionChunk, None]:
        config = tool.get_shell_config()
        command = config.command_template.format(**parameters)
        working_dir = parameters.get("working_directory", ".")

        process = await self._create_process(
            command,
            config.shell,
            capture_stderr=config.capture_stderr,
            stdin_pipe=config.stdin_enabled,
            cwd=working_dir,
        )
        self._process = process

        async def _read_stream(stream: asyncio.StreamReader, name: str) -> AsyncIterator[ExecutionChunk]:
            while True:
                line = await stream.readline()
                if not line:
                    break
                yield ExecutionChunk(
                    stream=name,
                    content=line.decode("utf-8", errors="replace"),
                )

        try:
            if process.stdout:
                async for chunk in _read_stream(process.stdout, "stdout"):
                    yield chunk
            if process.stderr:
                async for chunk in _read_stream(process.stderr, "stderr"):
                    yield chunk
            await process.wait()
        finally:
            self._process = None

    async def send_input(self, data: str) -> None:
        if self._process and self._process.stdin:
            self._process.stdin.write(data.encode("utf-8"))
            await self._process.stdin.drain()
        else:
            raise RuntimeError("No running interactive process or stdin not available")

    async def cancel(self) -> None:
        if self._process:
            self._process.kill()
            await self._process.wait()
            self._process = None
