"""Executor that runs shell-command tools."""

import asyncio
import logging
import shlex
import sys
from collections.abc import AsyncGenerator
from pathlib import PurePath
from typing import Any

from src.executors.base import BaseExecutor, ExecutionChunk, ExecutionResult
from src.tools.loader import ToolDefinition
from src.utils.process import kill_process_tree, new_session_kwargs

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"
_POWERSHELL_NAMES = {"powershell.exe", "powershell", "pwsh.exe", "pwsh"}


def _self_invocation() -> str:
    """Shell command prefix that re-invokes this RCFlow installation.

    Substituted for the built-in ``{rcflow}`` placeholder in shell
    command templates, so a tool like system_info can call back into
    RCFlow's own code on any install type: the single frozen binary on
    PyInstaller installs, ``python -m src`` in dev/venv runs.
    """
    from src.paths import is_frozen  # noqa: PLC0415

    exe = f'"{sys.executable}"' if _IS_WINDOWS else shlex.quote(sys.executable)
    return exe if is_frozen() else f"{exe} -m src"


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
                # PowerShell single-quoted strings are literal — no $(...) subexpression,
                # $var, or backtick expansion — so single-quoting (doubling embedded
                # single-quotes) is what actually blocks injection. Double-quoted strings
                # would still evaluate $(...) even with the quotes escaped.
                quoted[k] = "'" + s.replace("'", "''") + "'"
            else:
                quoted[k] = shlex.quote(s)
    return quoted


class ShellExecutor(BaseExecutor):
    """Shell Executor."""

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
                **new_session_kwargs(),
            )

        return await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=stderr,
            stdin=stdin,
            cwd=cwd,
            executable=shell if not _IS_WINDOWS else None,
            **new_session_kwargs(),
        )

    async def execute(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> ExecutionResult:
        """Execute the tool."""
        config = tool.get_shell_config()
        is_ps = self._is_powershell(config.shell)
        quoted = _quote_params_for_shell(parameters, config.command_template, is_powershell=is_ps)
        # Built-in placeholder — force the trusted value; RESERVED_PARAM_NAMES
        # (loader) guarantees no tool parameter can shadow it, but assign
        # unconditionally so caller input can never occupy this token.
        quoted["rcflow"] = _self_invocation()
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
                await kill_process_tree(self._process)
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
        """Execute the tool, streaming output chunks."""
        config = tool.get_shell_config()
        is_ps = self._is_powershell(config.shell)
        quoted = _quote_params_for_shell(parameters, config.command_template, is_powershell=is_ps)
        # Built-in placeholder — force the trusted value; RESERVED_PARAM_NAMES
        # (loader) guarantees no tool parameter can shadow it, but assign
        # unconditionally so caller input can never occupy this token.
        quoted["rcflow"] = _self_invocation()
        command = config.command_template.format(**quoted)
        working_dir = parameters.get("working_directory", ".")

        timeout = parameters.get("timeout")
        process = await self._create_process(
            command,
            config.shell,
            capture_stderr=config.capture_stderr,
            stdin_pipe=config.stdin_enabled,
            cwd=working_dir,
        )
        self._process = process

        # Read stdout and stderr CONCURRENTLY via a merged queue. Reading one to
        # EOF before the other deadlocks: a child that fills the stderr pipe
        # buffer while stdout is still open blocks forever (neither side drains).
        queue: asyncio.Queue[ExecutionChunk | None] = asyncio.Queue()

        async def _pump(stream: asyncio.StreamReader, name: str) -> None:
            # Fixed-size reads (not readline) — a single line larger than the
            # StreamReader limit (64 KiB) would make readline raise; chunked
            # reads stream arbitrary output without that ceiling.
            while True:
                data = await stream.read(65536)
                if not data:
                    break
                await queue.put(ExecutionChunk(stream=name, content=data.decode("utf-8", errors="replace")))

        readers = [
            asyncio.create_task(_pump(s, name))
            for s, name in ((process.stdout, "stdout"), (process.stderr, "stderr"))
            if s is not None
        ]

        async def _sentinel_when_eof() -> None:
            await asyncio.gather(*readers)
            await queue.put(None)  # all streams hit EOF

        sentinel = asyncio.create_task(_sentinel_when_eof())

        loop = asyncio.get_running_loop()
        deadline = (loop.time() + timeout) if timeout else None

        try:
            while True:
                remaining = None if deadline is None else deadline - loop.time()
                if remaining is not None and remaining <= 0:
                    break  # overall timeout exceeded
                try:
                    chunk = await asyncio.wait_for(queue.get(), timeout=remaining)
                except TimeoutError:
                    break  # timed out waiting for more output
                if chunk is None:
                    break  # EOF sentinel — command finished
                yield chunk
        finally:
            for t in (*readers, sentinel):
                t.cancel()
            # Reap the child; kill the whole tree if it is still running (timeout
            # or consumer cancellation) so no orphan keeps executing after we stop.
            if process.returncode is None:
                await kill_process_tree(process)
            self._process = None

    async def send_input(self, data: str) -> None:
        """Send input."""
        if self._process and self._process.stdin:
            self._process.stdin.write(data.encode("utf-8"))
            await self._process.stdin.drain()
        else:
            raise RuntimeError("No running interactive process or stdin not available")

    async def cancel(self) -> None:
        """Cancel."""
        if self._process:
            await kill_process_tree(self._process)
            self._process = None
