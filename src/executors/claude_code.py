import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from src.executors.base import BaseExecutor, ExecutionChunk, ExecutionResult
from src.tools.loader import ToolDefinition
from src.utils.process import kill_process_tree, new_session_kwargs

logger = logging.getLogger(__name__)


class ClaudeCodeExecutor(BaseExecutor):
    """Executor that manages a persistent Claude Code subprocess with bidirectional stream-json I/O.

    Spawns ``claude --input-format stream-json --output-format stream-json``
    as a long-lived subprocess.  The initial prompt is sent via stdin;
    subsequent follow-up messages are delivered through :meth:`send_input`
    while the process stays alive between turns.  Stdout is read line-by-line
    and parsed as JSON events.

    The process remains running after emitting a ``result`` event, ready for
    the next user message.  :meth:`restart_with_prompt` is a fallback for
    unexpected crashes — it respawns the process with the same
    ``--session-id`` so Claude Code can resume its conversation.
    """

    # How long to wait for a single stdout line before declaring a hang (seconds).
    _READLINE_TIMEOUT: float = 600.0  # 10 minutes — generous for slow API calls

    def __init__(
        self,
        binary_path: str = "claude",
        session_id: str | None = None,
        extra_env: dict[str, str] | None = None,
        config_overrides: dict[str, Any] | None = None,
    ) -> None:
        self._binary_path = binary_path
        self._process: asyncio.subprocess.Process | None = None
        self._session_id: str = session_id or str(uuid.uuid4())
        self._done: bool = False
        self._result_text: str = ""
        self._got_result: bool = False
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
    def session_id(self) -> str:
        return self._session_id

    @property
    def got_result(self) -> bool:
        """Whether the last read loop received a ``result`` event before stopping.

        When False after a stream has ended, the process exited without
        producing a result — likely due to a timeout, crash, or being killed.
        """
        return self._got_result

    @property
    def exit_code(self) -> int | None:
        """Return code of the subprocess, or None if still running / not started."""
        if self._process is None:
            return None
        return self._process.returncode

    def _build_command(self, parameters: dict[str, Any], config: dict[str, Any], *, resume: bool = False) -> list[str]:
        """Build the command-line arguments for the Claude Code subprocess.

        When *resume* is True, uses ``--resume`` instead of ``--session-id``
        so that Claude Code reconnects to an existing session rather than
        trying to create a new one (which would fail with "Session ID already
        in use").
        """
        cmd = [
            self._binary_path,
            "--verbose",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
        ]

        if resume:
            cmd.extend(["--resume", self._session_id])
        else:
            cmd.extend(["--session-id", self._session_id])

        permission_mode = config.get("default_permission_mode")
        if permission_mode and permission_mode != "interactive":
            # Pass standard Claude Code permission modes as-is.
            # "interactive" is handled by RCFlow server-side (permission checks
            # in _relay_claude_code_stream) so we use bypassPermissions on the
            # CLI and intercept tool_use events before execution.
            cmd.extend(["--permission-mode", permission_mode])
        elif permission_mode == "interactive":
            # Use bypassPermissions on the CLI; RCFlow handles approval via
            # the PERMISSION_REQUEST buffer messages before tool execution.
            cmd.extend(["--permission-mode", "bypassPermissions"])

        max_turns = config.get("max_turns")
        if max_turns is not None:
            cmd.extend(["--max-turns", str(max_turns)])

        allowed_tools = parameters.get("allowed_tools")
        if allowed_tools:
            cmd.extend(["--allowedTools", allowed_tools])

        model = parameters.get("model") or config.get("model")
        if model:
            cmd.extend(["--model", model])

        return cmd

    def _build_env(self) -> dict[str, str]:
        """Build environment for the subprocess, removing vars that prevent nesting."""
        env = dict(os.environ)
        # Remove CLAUDECODE to allow nesting Claude Code inside RCFlow
        env.pop("CLAUDECODE", None)
        # Remove CLAUDE_AVAILABLE_MODELS to avoid inheriting model restrictions
        env.pop("CLAUDE_AVAILABLE_MODELS", None)
        # Inject extra env vars (e.g. ANTHROPIC_API_KEY from Settings)
        env.update(self._extra_env)
        return env

    async def _start_process(
        self, tool: ToolDefinition, parameters: dict[str, Any], *, resume: bool = False
    ) -> asyncio.subprocess.Process:
        """Spawn the Claude Code subprocess.

        Kills any existing process first to ensure the session lock is released.
        When *resume* is True, uses ``--resume`` to reconnect to an existing session.
        """
        await self._cleanup_process()

        config = {**tool.executor_config.get("claude_code", {})}
        # Apply managed-tool settings overrides (non-empty values only)
        for k, v in self._config_overrides.items():
            if v not in (None, ""):
                config[k] = v
        cmd = self._build_command(parameters, config, resume=resume)
        working_directory = str(Path(parameters.get("working_directory", ".")).expanduser())
        env = self._build_env()

        timeout = config.get("timeout")
        if timeout:
            env["CLAUDE_CODE_TIMEOUT"] = str(timeout)

        # Store for potential restart on follow-up
        self._tool_def = tool
        self._last_parameters = parameters

        logger.info(
            "Starting Claude Code: %s (cwd=%s, session=%s)",
            " ".join(cmd),
            working_directory,
            self._session_id,
        )

        self._stderr_output = ""
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_directory,
            env=env,
            limit=10 * 1024 * 1024,  # 10 MB — Claude Code can emit large JSON lines
            **new_session_kwargs(),
        )
        self._process = process
        self._done = False
        self._got_result = False

        # Start draining stderr in background to prevent pipe deadlock
        self._stderr_task = asyncio.create_task(self._drain_stderr())

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
                logger.debug("Claude Code stderr [session=%s]: %s", self._session_id, decoded)
        except (asyncio.CancelledError, ConnectionResetError):
            pass

    async def _wait_and_log_exit(self) -> None:
        """Wait for process to exit and log diagnostics."""
        if not self._process:
            return
        returncode = self._process.returncode
        if returncode is None:
            # Process hasn't exited yet during our read loop — wait briefly
            try:
                returncode = await asyncio.wait_for(self._process.wait(), timeout=2.0)
            except TimeoutError:
                return  # still running, that's fine

        # Ensure stderr task finishes
        if self._stderr_task and not self._stderr_task.done():
            try:
                await asyncio.wait_for(self._stderr_task, timeout=2.0)
            except TimeoutError:
                self._stderr_task.cancel()

        if returncode != 0:
            logger.warning(
                "Claude Code exited with code %d (session=%s). stderr: %s",
                returncode,
                self._session_id,
                self._stderr_output.strip() or "(empty)",
            )
        else:
            logger.info(
                "Claude Code exited normally (session=%s, code=%d)",
                self._session_id,
                returncode,
            )

    async def _send_message(self, msg_type: str, content: str) -> None:
        """Write a stream-json message to the process stdin."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Claude Code process not started or stdin not available")
        message = json.dumps(
            {
                "type": msg_type,
                "message": {"role": msg_type, "content": content},
            }
        )
        self._process.stdin.write((message + "\n").encode("utf-8"))
        await self._process.stdin.drain()
        logger.debug("Sent to Claude Code stdin [session=%s]: %s", self._session_id, message)

    async def _read_events(self) -> AsyncGenerator[ExecutionChunk, None]:
        """Read and yield parsed stream-json events from stdout.

        Each line from stdout is expected to be a JSON object.  Events are
        yielded as ``ExecutionChunk`` instances with ``stream="stdout"`` and the
        raw JSON line as content.  When a ``{"type": "result", ...}`` event is
        received, ``_done`` and ``_got_result`` are set to True and we stop
        reading.

        If the process exits without emitting a ``result`` event (e.g. timeout,
        crash, OOM-kill), ``_done`` is set but ``_got_result`` stays False so
        the caller can detect the abnormal termination.
        """
        async with self._read_lock:
            if not self._process or not self._process.stdout:
                return
            stdout = self._process.stdout
            self._got_result = False
            while True:
                try:
                    line = await asyncio.wait_for(
                        stdout.readline(),
                        timeout=self._READLINE_TIMEOUT,
                    )
                except TimeoutError:
                    logger.warning(
                        "Claude Code stdout read timed out after %ds (session=%s)",
                        self._READLINE_TIMEOUT,
                        self._session_id,
                    )
                    self._done = True
                    break
                except (asyncio.CancelledError, ConnectionResetError):
                    break

                if not line:
                    # EOF — process exited
                    self._done = True
                    await self._wait_and_log_exit()
                    break

                decoded = line.decode("utf-8", errors="replace").rstrip("\n")
                if not decoded:
                    continue

                try:
                    event = json.loads(decoded)
                except json.JSONDecodeError:
                    # Non-JSON output — emit as-is
                    yield ExecutionChunk(stream="stdout", content=decoded + "\n")
                    continue

                yield ExecutionChunk(stream="stdout", content=decoded)

                # Check for result event (marks end of a turn)
                if isinstance(event, dict) and event.get("type") == "result":
                    self._result_text = json.dumps(event)
                    self._done = True
                    self._got_result = True
                    # Process stays alive for follow-up messages; no exit cleanup here.
                    break

    async def execute_streaming(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> AsyncGenerator[ExecutionChunk, None]:
        """Start Claude Code and stream output events.

        Spawns the subprocess, sends the initial prompt, then yields parsed
        stream-json events until the ``result`` event or process exit.
        """
        prompt = parameters.get("prompt", "")
        if not prompt:
            raise ValueError("'prompt' parameter is required for claude_code")

        await self._start_process(tool, parameters)
        await self._send_message("user", prompt)

        async for chunk in self._read_events():
            yield chunk

    async def restart_with_prompt(self, prompt: str) -> AsyncGenerator[ExecutionChunk, None]:
        """Restart the Claude Code process with ``--resume`` and a new prompt.

        This is a **fallback** for when the process has unexpectedly exited.
        Normal follow-ups use :meth:`send_input` + :meth:`read_more_events`
        on the still-running process.  Uses ``--resume`` instead of
        ``--session-id`` so Claude Code reconnects to the existing session.
        """
        if self._tool_def is None:
            raise RuntimeError("Cannot restart: no previous tool definition stored")

        logger.info(
            "Restarting Claude Code for follow-up (session=%s)",
            self._session_id,
        )

        await self._cleanup_process()

        await self._start_process(self._tool_def, self._last_parameters, resume=True)
        await self._send_message("user", prompt)

        async for chunk in self._read_events():
            yield chunk

    async def read_more_events(self) -> AsyncGenerator[ExecutionChunk, None]:
        """Continue reading events after a follow-up send_input.

        Only valid when the process is still alive.
        """
        self._done = False
        self._got_result = False
        async for chunk in self._read_events():
            yield chunk

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
        """Send a follow-up message to the running Claude Code subprocess.

        Raises RuntimeError if the process is not running.
        """
        if not self._process or not self._process.stdin:
            raise RuntimeError("No running Claude Code process or stdin not available")
        if self._process.returncode is not None:
            raise RuntimeError("Claude Code process has already exited")

        await self._send_message("user", data)

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
        """Kill the Claude Code subprocess."""
        if self._process:
            logger.info("Cancelling Claude Code session %s", self._session_id)
        await self._cleanup_process()
        self._done = True
