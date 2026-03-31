import asyncio
import contextlib
import json
import logging
import os
import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from src.executors.base import BaseExecutor, ExecutionChunk, ExecutionResult
from src.tools.loader import ToolDefinition
from src.utils.process import kill_process_tree, new_session_kwargs

logger = logging.getLogger(__name__)

# PTY support is available on all non-Windows platforms.
_HAS_PTY = sys.platform != "win32"

if _HAS_PTY:
    import pty as _pty

    from src.utils.pty_utils import PtyLineReader, configure_raw, set_winsize, strip_ansi


class ClaudeCodeExecutor(BaseExecutor):
    """Executor that manages a persistent Claude Code subprocess with bidirectional stream-json I/O.

    On Unix (Linux/macOS), the subprocess is backed by a **PTY** by default so
    that Claude Code detects a real terminal on both stdin and stdout.  This
    preserves interactive behaviours — e.g. ``AskUserQuestion`` prompts,
    ``EnterPlanMode``/``ExitPlanMode`` flows, and tool-permission dialogs —
    that Claude Code may suppress or simplify when it detects a non-TTY pipe.

    Despite using a PTY, the I/O *protocol* remains ``stream-json`` (via
    ``--input-format stream-json --output-format stream-json``), so all
    downstream event-translation logic in ``_relay_claude_code_stream`` is
    unchanged.  The PTY slave is configured in **raw mode** (no echo, no line
    discipline) before the child is spawned so that:

    * JSON written to the master fd is not echoed back as spurious output.
    * Claude Code's ``\\n``-terminated JSON lines are not translated to
      ``\\r\\n`` (``OPOST`` disabled).
    * Signal generation (Ctrl+C → SIGINT) is disabled, leaving signal
      delivery to :func:`kill_process_tree`.

    On Windows, PTY support is unavailable; the executor transparently falls
    back to the original ``asyncio`` pipe mode.

    PTY mode can be disabled per-tool by setting ``use_pty: false`` in the
    tool's ``executor_config.claude_code`` block.

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
        # Lock to prevent concurrent reads on the stdout/PTY stream
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
        # PTY state — set during _start_process, cleared in _cleanup_process
        self._use_pty: bool = False  # resolved per-invocation from config
        self._master_fd: int | None = None
        self._pty_reader: PtyLineReader | None = None  # type: ignore[name-defined]

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

    def _build_command(
        self,
        parameters: dict[str, Any],
        config: dict[str, Any],
        *,
        resume: bool = False,
        prompt: str | None = None,
    ) -> list[str]:
        """Build the command-line arguments for the Claude Code subprocess.

        When *resume* is True, uses ``--resume`` instead of ``--session-id``
        so that Claude Code reconnects to an existing session rather than
        trying to create a new one (which would fail with "Session ID already
        in use").

        *prompt* is passed as a positional CLI argument for the initial turn
        (required by ``--print`` mode).  Follow-up messages use stdin via
        ``--input-format stream-json``.
        """
        cmd = [
            self._binary_path,
            "--print",
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
            # Pass standard Claude Code permission modes as-is
            # (e.g. bypassPermissions, allowEdits, plan).
            cmd.extend(["--permission-mode", permission_mode])
        # When "interactive" (or not set), don't pass --permission-mode so
        # Claude Code uses its default behavior.  This lets it emit interactive
        # prompts (AskUserQuestion, plan mode, tool permissions) via
        # stream-json, which the relay intercepts and forwards to the client.

        max_turns = config.get("max_turns")
        if max_turns is not None:
            cmd.extend(["--max-turns", str(max_turns)])

        allowed_tools = parameters.get("allowed_tools")
        if allowed_tools:
            cmd.extend(["--allowedTools", allowed_tools])

        model = parameters.get("model") or config.get("model")
        if model:
            cmd.extend(["--model", model])

        if prompt:
            cmd.append("--")
            cmd.append(prompt)

        return cmd

    def _build_env(self) -> dict[str, str]:
        """Build environment for the subprocess, removing vars that prevent nesting."""
        env = dict(os.environ)
        # Remove CLAUDECODE to allow nesting Claude Code inside RCFlow
        env.pop("CLAUDECODE", None)
        # Remove CLAUDE_AVAILABLE_MODELS to avoid inheriting model restrictions
        env.pop("CLAUDE_AVAILABLE_MODELS", None)
        # Remove ANTHROPIC_MODEL — the CLI reads it as a model override,
        # but it may contain a Bedrock model ID from server settings.
        # Model selection is handled via --model flag or CLI defaults.
        env.pop("ANTHROPIC_MODEL", None)
        # Inject extra env vars (e.g. ANTHROPIC_API_KEY from Settings).
        # Empty-string values mean "remove from env" (e.g. anthropic_login
        # clears ANTHROPIC_API_KEY so OAuth tokens are used instead).
        for k, v in self._extra_env.items():
            if v:
                env[k] = v
            else:
                env.pop(k, None)
        return env

    # ------------------------------------------------------------------
    # Process startup — dispatches to PTY or pipe mode

    async def _start_process(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
        *,
        resume: bool = False,
        prompt: str | None = None,
    ) -> asyncio.subprocess.Process:
        """Spawn the Claude Code subprocess (PTY-backed on Unix, pipe on Windows).

        Kills any existing process first to ensure the session lock is released.
        When *resume* is True, uses ``--resume`` to reconnect to an existing session.
        *prompt* is passed as a CLI argument for the initial turn (required by
        ``--print`` mode).

        PTY mode is the default on Unix and can be disabled by setting
        ``use_pty: false`` in the tool's ``executor_config.claude_code`` block.
        """
        await self._cleanup_process()

        config = {**tool.executor_config.get("claude_code", {})}
        # Apply managed-tool settings overrides (non-empty values only)
        for k, v in self._config_overrides.items():
            if v not in (None, ""):
                config[k] = v

        # Resolve PTY mode: default True on Unix, False on Windows or if disabled.
        self._use_pty = _HAS_PTY and bool(config.get("use_pty", True))

        if self._use_pty:
            return await self._start_process_pty(tool, parameters, config, resume=resume, prompt=prompt)
        return await self._start_process_pipe(tool, parameters, config, resume=resume, prompt=prompt)

    async def _start_process_pipe(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
        config: dict[str, Any],
        *,
        resume: bool = False,
        prompt: str | None = None,
    ) -> asyncio.subprocess.Process:
        """Spawn using ``asyncio`` pipes (original behaviour; used on Windows or when PTY is disabled)."""
        cmd = self._build_command(parameters, config, resume=resume, prompt=prompt)
        working_directory = str(Path(parameters.get("working_directory", ".")).expanduser())
        env = self._build_env()

        timeout = config.get("timeout")
        if timeout:
            env["CLAUDE_CODE_TIMEOUT"] = str(timeout)

        self._tool_def = tool
        self._last_parameters = parameters

        logger.info(
            "Starting Claude Code (pipe): %s (cwd=%s, session=%s, CLAUDE_CONFIG_DIR=%s, ANTHROPIC_API_KEY=%s)",
            " ".join(cmd),
            working_directory,
            self._session_id,
            env.get("CLAUDE_CONFIG_DIR", "(not set)"),
            "SET" if env.get("ANTHROPIC_API_KEY") else "(not set)",
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

    async def _start_process_pty(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
        config: dict[str, Any],
        *,
        resume: bool = False,
        prompt: str | None = None,
    ) -> asyncio.subprocess.Process:
        """Spawn using a PTY so Claude Code detects a real terminal on stdin/stdout.

        Architecture
        ------------
        * A PTY pair is opened with ``pty.openpty()`` → ``(master_fd, slave_fd)``.
        * The **slave fd** is passed as *both* stdin *and* stdout for the child
          process so ``isatty(0)`` and ``isatty(1)`` return ``True`` inside
          Claude Code.
        * The slave is configured in **raw mode** (``configure_raw``) before
          spawning, which disables echo and all line-discipline transformations.
          This keeps the JSON stream clean:

          - Our JSON writes to ``master_fd`` are NOT echoed back as output.
          - Claude Code's ``\\n`` line endings are NOT translated to ``\\r\\n``
            (``OPOST`` is off).

        * ``stderr`` is kept as a standard ``asyncio`` pipe so it can be drained
          separately for diagnostics without mixing into the JSON stream.
        * A :class:`~src.utils.pty_utils.PtyLineReader` wraps ``master_fd`` for
          async line-by-line reading via the event loop I/O callback.
        * Terminal dimensions are set to 24 rows x 220 cols so Claude Code has
          room for tool output without wrapping artifacts.
        * ``TERM=xterm-256color`` is set so Claude Code uses full colour output
          within the TUI; ``--output-format stream-json`` suppresses the visual
          TUI in favour of structured JSON regardless of ``TERM``.
        """
        cmd = self._build_command(parameters, config, resume=resume, prompt=prompt)
        working_directory = str(Path(parameters.get("working_directory", ".")).expanduser())
        env = self._build_env()
        # Use TERM=dumb to prevent Claude Code from sending terminal capability
        # queries (DA, OSC) that would hang waiting for a response from the PTY.
        # The PTY still makes isatty() return True (enabling interactive events),
        # but TERM=dumb tells it not to probe for terminal features.
        env["TERM"] = "dumb"
        env.pop("COLORTERM", None)

        timeout = config.get("timeout")
        if timeout:
            env["CLAUDE_CODE_TIMEOUT"] = str(timeout)

        self._tool_def = tool
        self._last_parameters = parameters

        # Open PTY pair and configure slave to raw mode.
        master_fd, slave_fd = _pty.openpty()  # type: ignore[name-defined]
        configure_raw(slave_fd)
        set_winsize(master_fd, rows=24, cols=220)

        logger.info(
            "Starting Claude Code (PTY): %s (cwd=%s, session=%s, CLAUDE_CONFIG_DIR=%s, ANTHROPIC_API_KEY=%s)",
            " ".join(cmd),
            working_directory,
            self._session_id,
            env.get("CLAUDE_CONFIG_DIR", "(not set)"),
            "SET" if env.get("ANTHROPIC_API_KEY") else "(not set)",
        )

        self._stderr_output = ""
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=slave_fd,   # child's stdin  → PTY slave (isatty → True)
            stdout=slave_fd,  # child's stdout → PTY slave (isatty → True)
            stderr=asyncio.subprocess.PIPE,  # kept separate for diagnostics
            cwd=working_directory,
            env=env,
            **new_session_kwargs(),
            # NOTE: no `limit=` here — stdout is not an asyncio StreamReader
        )
        # Parent no longer needs the slave end; the child has its own copy.
        os.close(slave_fd)

        self._master_fd = master_fd
        self._pty_reader = PtyLineReader(master_fd)  # type: ignore[name-defined]
        self._process = process
        self._done = False
        self._got_result = False

        # Start draining stderr in background to prevent pipe deadlock
        self._stderr_task = asyncio.create_task(self._drain_stderr())

        return process

    # ------------------------------------------------------------------
    # Stderr drain (shared between pipe and PTY modes)

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

    # ------------------------------------------------------------------
    # stdin write — dispatches to PTY master or asyncio pipe

    async def _send_message(self, msg_type: str, content: str) -> None:
        """Write a stream-json message to the process stdin (pipe or PTY master)."""
        message = json.dumps(
            {
                "type": msg_type,
                "message": {"role": msg_type, "content": content},
            }
        )
        encoded = (message + "\n").encode("utf-8")

        if self._use_pty and self._master_fd is not None:
            # Write directly to the PTY master fd.  The slave is in raw mode
            # so there is no echo; the write goes straight to Claude Code's
            # stdin.  Run in executor to avoid blocking the event loop if the
            # PTY write buffer is momentarily full.
            master_fd = self._master_fd
            await asyncio.get_event_loop().run_in_executor(None, os.write, master_fd, encoded)
        else:
            if not self._process or not self._process.stdin:
                raise RuntimeError("Claude Code process not started or stdin not available")
            self._process.stdin.write(encoded)
            await self._process.stdin.drain()

        logger.debug("Sent to Claude Code stdin [session=%s]: %s", self._session_id, message)

    # ------------------------------------------------------------------
    # stdout read — dispatches to PTY line reader or asyncio pipe

    async def _read_events(self) -> AsyncGenerator[ExecutionChunk, None]:
        """Read and yield parsed stream-json events from stdout (pipe or PTY)."""
        if self._use_pty and self._pty_reader is not None:
            async for chunk in self._read_events_pty():
                yield chunk
        else:
            async for chunk in self._read_events_pipe():
                yield chunk

    async def _read_events_pipe(self) -> AsyncGenerator[ExecutionChunk, None]:
        """Read stream-json events from the asyncio pipe (pipe mode).

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

    async def _read_events_pty(self) -> AsyncGenerator[ExecutionChunk, None]:
        """Read stream-json events from the PTY master fd (PTY mode).

        Functionally equivalent to :meth:`_read_events_pipe` but uses
        :class:`~src.utils.pty_utils.PtyLineReader` instead of the asyncio
        ``StreamReader``.  An additional ``strip_ansi`` pass is applied to each
        decoded line as a safety measure in case Claude Code emits cursor-control
        sequences despite ``--output-format stream-json`` (e.g. spinner
        artifacts from a non-``TERM=dumb`` terminal).
        """
        async with self._read_lock:
            if self._pty_reader is None:
                return
            reader = self._pty_reader
            self._got_result = False
            while True:
                try:
                    line_bytes = await asyncio.wait_for(
                        reader.readline(),
                        timeout=self._READLINE_TIMEOUT,
                    )
                except TimeoutError:
                    logger.warning(
                        "Claude Code PTY read timed out after %ds (session=%s)",
                        self._READLINE_TIMEOUT,
                        self._session_id,
                    )
                    self._done = True
                    break
                except (asyncio.CancelledError, ConnectionResetError, OSError):
                    break

                if not line_bytes:
                    # EOF — PTY slave was closed (process exited)
                    self._done = True
                    await self._wait_and_log_exit()
                    break

                decoded = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
                if not decoded:
                    continue

                # Strip any ANSI escape sequences that leaked into the stream.
                decoded = strip_ansi(decoded).strip()  # type: ignore[name-defined]
                if not decoded:
                    continue

                try:
                    event = json.loads(decoded)
                except json.JSONDecodeError:
                    # Non-JSON output (e.g. startup banner on some terminals)
                    yield ExecutionChunk(stream="stdout", content=decoded + "\n")
                    continue

                yield ExecutionChunk(stream="stdout", content=decoded)

                if isinstance(event, dict) and event.get("type") == "result":
                    self._result_text = json.dumps(event)
                    self._done = True
                    self._got_result = True
                    # Process stays alive for follow-up messages.
                    break

    # ------------------------------------------------------------------
    # Public streaming API

    async def execute_streaming(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> AsyncGenerator[ExecutionChunk, None]:
        """Start Claude Code and stream output events.

        Spawns the subprocess (PTY-backed on Unix by default) with the
        initial prompt as a CLI argument (required by ``--print`` mode),
        then yields parsed stream-json events until the ``result`` event
        or process exit.  Follow-up messages use stdin via stream-json.
        """
        prompt = parameters.get("prompt", "")
        if not prompt:
            raise ValueError("'prompt' parameter is required for claude_code")

        await self._start_process(tool, parameters, prompt=prompt)

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

        await self._start_process(self._tool_def, self._last_parameters, resume=True, prompt=prompt)

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
        if not self._process or (not self._use_pty and not self._process.stdin):
            raise RuntimeError("No running Claude Code process or stdin not available")
        if self._process.returncode is not None:
            raise RuntimeError("Claude Code process has already exited")

        await self._send_message("user", data)

    # ------------------------------------------------------------------
    # Cleanup

    async def _cleanup_process(self) -> None:
        """Kill the process tree, cancel stderr drain, and release PTY resources."""
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()
            self._stderr_task = None
        if self._process:
            await kill_process_tree(self._process)
            self._process = None
        # PTY cleanup — order matters: stop the reader before closing the fd
        # so the event-loop callback is removed before the fd is invalidated.
        if self._pty_reader is not None:
            self._pty_reader.close()
            self._pty_reader = None
        if self._master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._master_fd)
            self._master_fd = None

    async def stop_process(self) -> None:
        """Kill the subprocess to free resources while keeping executor state for restart."""
        await self._cleanup_process()

    async def cancel(self) -> None:
        """Kill the Claude Code subprocess."""
        if self._process:
            logger.info("Cancelling Claude Code session %s", self._session_id)
        await self._cleanup_process()
        self._done = True
