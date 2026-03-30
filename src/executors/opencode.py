import asyncio
import json
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from src.executors.base import BaseExecutor, ExecutionChunk, ExecutionResult
from src.tools.loader import ToolDefinition
from src.utils.process import kill_process_tree, new_session_kwargs

logger = logging.getLogger(__name__)


class OpenCodeExecutor(BaseExecutor):
    """Executor that manages OpenCode CLI as a one-shot subprocess per turn.

    OpenCode CLI uses a one-shot model: each turn spawns
    ``opencode run --format json <prompt>``, reads JSONL from stdout until
    ``step_finish`` with ``reason == "stop"`` or process exit, and then the
    process naturally terminates.

    Follow-up messages are handled by spawning a new process with
    ``--session SESSION_ID``.  The session ID is extracted from the first
    ``step_start`` event (``sessionID`` field, camelCase).

    Actual JSONL event types emitted by opencode ≥ 1.3:
    - ``step_start``   – start of a reasoning/tool step; carries ``sessionID``
    - ``text``         – assistant text; content in ``part.text``
    - ``tool_use``     – tool call + result; ``part.tool``, ``part.state``
    - ``step_finish``  – end of step; ``part.reason`` is ``"stop"`` on final
    - ``error``        – fatal error during the session
    """

    def __init__(
        self,
        binary_path: str = "opencode",
        session_id: str | None = None,
        extra_env: dict[str, str] | None = None,
        config_overrides: dict[str, Any] | None = None,
    ) -> None:
        self._binary_path = binary_path
        self._process: asyncio.subprocess.Process | None = None
        self._session_id: str | None = session_id
        self._done: bool = False
        self._result_text: str = ""
        # Lock to prevent concurrent reads on the stdout stream
        self._read_lock: asyncio.Lock = asyncio.Lock()
        # Stderr output captured from the last process run
        self._stderr_output: str = ""
        # Background task draining stderr
        self._stderr_task: asyncio.Task[None] | None = None
        # Store tool definition + parameters for restarting on follow-up
        self._tool_def: ToolDefinition | None = None
        self._last_parameters: dict[str, Any] = {}
        # Extra environment variables to inject into the subprocess
        self._extra_env: dict[str, str] = extra_env or {}
        # Overrides from tool settings (managed-only), applied on top of tool_def config
        self._config_overrides: dict[str, Any] = config_overrides or {}

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def opencode_session_id(self) -> str | None:
        return self._session_id

    def _build_command(
        self,
        parameters: dict[str, Any],
        config: dict[str, Any],
        *,
        resume: bool = False,
        prompt: str | None = None,
    ) -> list[str]:
        """Build the command-line arguments for the OpenCode CLI subprocess."""
        cmd = [
            self._binary_path,
            "run",
            "--format",
            "json",
        ]

        # Working directory
        working_dir = parameters.get("working_directory")
        if working_dir:
            cmd.extend(["--dir", working_dir])

        # Model override
        model = parameters.get("model") or config.get("model")
        if model:
            cmd.extend(["--model", model])

        # Resume existing session
        if resume and self._session_id:
            cmd.extend(["--session", self._session_id])

        # Prompt is passed as a positional argument (opencode run [message..])
        if prompt:
            cmd.append(prompt)

        return cmd

    def _build_env(self) -> dict[str, str]:
        """Build environment for the subprocess."""
        env = dict(os.environ)
        env.update(self._extra_env)
        return env

    async def _start_process(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
        *,
        resume: bool = False,
        prompt: str | None = None,
    ) -> asyncio.subprocess.Process:
        """Spawn the OpenCode CLI subprocess.

        Kills any existing process first.  The prompt is passed as a
        positional argument (``opencode run [message..]``).
        """
        await self._cleanup_process()

        config = {**tool.executor_config.get("opencode", {})}
        for k, v in self._config_overrides.items():
            if v not in (None, ""):
                config[k] = v
        cmd = self._build_command(parameters, config, resume=resume, prompt=prompt)
        env = self._build_env()

        self._tool_def = tool
        self._last_parameters = parameters

        logger.info(
            "Starting OpenCode CLI: %s (session=%s, resume=%s)",
            " ".join(cmd),
            self._session_id,
            resume,
        )

        self._stderr_output = ""
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=10 * 1024 * 1024,  # 10 MB buffer
            **new_session_kwargs(),
        )
        self._process = process
        self._done = False

        self._stderr_task = asyncio.create_task(self._drain_stderr())

        await asyncio.sleep(0)
        if process.returncode is not None:
            if self._stderr_task and not self._stderr_task.done():
                try:
                    await asyncio.wait_for(self._stderr_task, timeout=1.0)
                except TimeoutError:
                    self._stderr_task.cancel()
            stderr_hint = self._stderr_output.strip()
            raise RuntimeError(
                f"OpenCode process exited immediately with code {process.returncode}"
                + (f": {stderr_hint}" if stderr_hint else "")
            )

        return process

    _STDERR_MAX_BYTES = 64 * 1024

    async def _drain_stderr(self) -> None:
        """Read stderr to prevent pipe deadlock and capture tail for diagnostics."""
        if not self._process or not self._process.stderr:
            return
        try:
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip("\n")
                self._stderr_output += decoded + "\n"
                if len(self._stderr_output) > self._STDERR_MAX_BYTES:
                    self._stderr_output = self._stderr_output[-self._STDERR_MAX_BYTES :]
                logger.debug("OpenCode stderr [session=%s]: %s", self._session_id, decoded)
        except (asyncio.CancelledError, ConnectionResetError):
            pass

    async def _wait_and_log_exit(self) -> None:
        """Wait for process to exit and log diagnostics."""
        if not self._process:
            return
        returncode = self._process.returncode
        if returncode is None:
            try:
                returncode = await asyncio.wait_for(self._process.wait(), timeout=2.0)
            except TimeoutError:
                return

        if self._stderr_task and not self._stderr_task.done():
            try:
                await asyncio.wait_for(self._stderr_task, timeout=2.0)
            except TimeoutError:
                self._stderr_task.cancel()

        if returncode != 0:
            logger.warning(
                "OpenCode exited with code %d (session=%s). stderr: %s",
                returncode,
                self._session_id,
                self._stderr_output.strip() or "(empty)",
            )
        else:
            logger.info(
                "OpenCode exited normally (session=%s, code=%d)",
                self._session_id,
                returncode,
            )

    async def _write_prompt_and_close(self, prompt: str) -> None:
        """Write the prompt to stdin and close it (OpenCode one-shot model)."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("OpenCode process not started or stdin not available")

        if self._process.returncode is not None:
            if self._stderr_task and not self._stderr_task.done():
                try:
                    await asyncio.wait_for(self._stderr_task, timeout=1.0)
                except TimeoutError:
                    self._stderr_task.cancel()
            stderr_hint = self._stderr_output.strip()
            raise RuntimeError(
                f"OpenCode process exited (code {self._process.returncode}) before prompt could be sent"
                + (f": {stderr_hint}" if stderr_hint else "")
            )

        try:
            self._process.stdin.write(prompt.encode("utf-8"))
            await self._process.stdin.drain()
            self._process.stdin.close()
            await self._process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            if self._stderr_task and not self._stderr_task.done():
                try:
                    await asyncio.wait_for(self._stderr_task, timeout=1.0)
                except TimeoutError:
                    self._stderr_task.cancel()
            stderr_hint = self._stderr_output.strip()
            raise RuntimeError(
                "Failed to write prompt to OpenCode stdin (transport closed)"
                + (f": {stderr_hint}" if stderr_hint else "")
            ) from exc

        logger.debug("Sent prompt to OpenCode stdin and closed [session=%s]", self._session_id)

    async def _read_events(self) -> AsyncGenerator[ExecutionChunk, None]:
        """Read and yield parsed JSONL events from stdout.

        Each line from stdout is expected to be a JSON object.  Events are
        yielded as ``ExecutionChunk`` instances with ``stream="stdout"`` and the
        raw JSON line as content.  When a ``session.complete`` event is received,
        ``_done`` is set to True and we stop reading.
        """
        if not self._process or not self._process.stdout:
            return

        async with self._read_lock:
            while True:
                try:
                    line = await self._process.stdout.readline()
                except (asyncio.CancelledError, ConnectionResetError):
                    break

                if not line:
                    self._done = True
                    await self._wait_and_log_exit()
                    break

                decoded = line.decode("utf-8", errors="replace").rstrip("\n")
                if not decoded:
                    continue

                try:
                    event = json.loads(decoded)
                except json.JSONDecodeError:
                    yield ExecutionChunk(stream="stdout", content=decoded + "\n")
                    continue

                yield ExecutionChunk(stream="stdout", content=decoded)

                if not isinstance(event, dict):
                    continue

                event_type = event.get("type")

                # Capture session ID from step_start (sessionID is camelCase)
                if event_type == "step_start":
                    part = event.get("part") or {}
                    sid = event.get("sessionID") or part.get("sessionID")
                    if sid:
                        self._session_id = sid

                # Final step_finish (reason "stop") signals session completion
                if event_type == "step_finish":
                    part = event.get("part") or {}
                    if part.get("reason") == "stop":
                        self._result_text = json.dumps(event)
                        self._done = True
                        await self._wait_and_log_exit()
                        break
                elif event_type in ("error", "session.error"):
                    self._result_text = json.dumps(event)
                    self._done = True
                    await self._wait_and_log_exit()
                    break

    async def execute_streaming(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> AsyncGenerator[ExecutionChunk, None]:
        """Start OpenCode CLI and stream output events.

        Spawns the subprocess with the prompt as a positional argument, then
        yields parsed JSON events until session completion or process exit.
        """
        prompt = parameters.get("prompt", "")
        if not prompt:
            raise ValueError("'prompt' parameter is required for opencode")

        await self._start_process(tool, parameters, prompt=prompt)

        async for chunk in self._read_events():
            yield chunk

    async def restart_with_prompt(self, prompt: str) -> AsyncGenerator[ExecutionChunk, None]:
        """Spawn a new OpenCode process with ``--session`` and a follow-up prompt.

        Each turn is a separate process invocation that resumes the session.
        """
        if self._tool_def is None:
            raise RuntimeError("Cannot restart: no previous tool definition stored")

        if self._session_id is None:
            raise RuntimeError("Cannot restart: no session ID from previous turn")

        logger.info(
            "Restarting OpenCode for follow-up (session=%s)",
            self._session_id,
        )

        await self._cleanup_process()

        await self._start_process(self._tool_def, self._last_parameters, resume=True, prompt=prompt)

        async for chunk in self._read_events():
            yield chunk

    async def read_more_events(self) -> AsyncGenerator[ExecutionChunk, None]:
        """Not supported — OpenCode CLI uses one-shot processes.

        Raises RuntimeError because OpenCode does not support reading more
        events from a completed turn. Use ``restart_with_prompt()`` instead.
        """
        raise RuntimeError(
            "OpenCode CLI does not support reading more events from a completed turn; use restart_with_prompt() instead"
        )
        yield  # type: ignore[misc]  # pragma: no cover

    async def execute(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> ExecutionResult:
        """Non-streaming execution: collect all chunks and return final result."""
        collected: list[str] = []
        async for chunk in self.execute_streaming(tool, parameters):
            collected.append(chunk.content)

        output = "\n".join(collected)
        exit_code = self._process.returncode if self._process else None
        return ExecutionResult(output=output, exit_code=exit_code)

    async def send_input(self, data: str) -> None:
        """Not supported — OpenCode CLI uses one-shot processes.

        Raises RuntimeError because stdin is closed after the initial prompt.
        Use ``restart_with_prompt()`` for follow-up messages.
        """
        raise RuntimeError(
            "OpenCode CLI does not support interactive stdin input; use restart_with_prompt() for follow-up messages"
        )

    async def _cleanup_process(self) -> None:
        """Kill the entire process tree and wait for exit, cancel stderr drain."""
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            self._stderr_task = None
        if self._process:
            await kill_process_tree(self._process)
            self._process = None

    async def stop_process(self) -> None:
        """Kill the subprocess to free resources while keeping executor state for restart."""
        await self._cleanup_process()

    async def cancel(self) -> None:
        """Kill the OpenCode subprocess."""
        if self._process:
            logger.info("Cancelling OpenCode session (session=%s)", self._session_id)
        await self._cleanup_process()
        self._done = True
