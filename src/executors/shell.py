import asyncio
import logging
import shlex
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from pathlib import PurePath
from typing import Any

from src.executors.base import BaseExecutor, ExecutionChunk, ExecutionResult
from src.tools.loader import ToolDefinition

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"
_POWERSHELL_NAMES = {"powershell.exe", "powershell", "pwsh.exe", "pwsh"}


def _quote_params_for_shell(
    parameters: dict[str, Any],
    template: str,
    *,
    is_powershell: bool,
) -> dict[str, Any]:
    """Return a copy of *parameters* with string values shell-escaped.

    Numeric and boolean values are passed through unchanged — they cannot
    contain shell metacharacters.  All other values are converted to str and
    then quoted so that LLM-supplied content cannot inject shell commands via
    the command_template substitution (F1: command injection mitigation).

    Parameters whose placeholder IS the entire template (e.g.
    ``command_template = "{command}"``) are not quoted: they represent the
    full shell command and must retain shell operators and spaces.  All other
    string parameters are quoted to prevent injection.
    """
    # Params that constitute the entire template value pass through unquoted
    # because they ARE the shell command, not an argument embedded in one.
    raw_params: frozenset[str] = frozenset(k for k in parameters if template.strip() == f"{{{k}}}")

    quoted: dict[str, Any] = {}
    for k, v in parameters.items():
        if k in raw_params or isinstance(v, (int, float, bool)):
            quoted[k] = v
        else:
            s = str(v)
            if is_powershell:
                # PowerShell: wrap in double-quotes and escape internal double-quotes.
                quoted[k] = '"' + s.replace('"', '""') + '"'
            else:
                quoted[k] = shlex.quote(s)
    return quoted


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
        is_ps = self._is_powershell(config.shell)
        quoted = _quote_params_for_shell(parameters, config.command_template, is_powershell=is_ps)
        command = config.command_template.format(**quoted)
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
        is_ps = self._is_powershell(config.shell)
        quoted = _quote_params_for_shell(parameters, config.command_template, is_powershell=is_ps)
        command = config.command_template.format(**quoted)
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
