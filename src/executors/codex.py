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


class CodexExecutor(BaseExecutor):
    """Executor that manages OpenAI Codex CLI as a one-shot subprocess per turn.

    Unlike ``ClaudeCodeExecutor`` which keeps a persistent bidirectional
    process, Codex CLI uses a one-shot model: each turn spawns
    ``codex exec --json --full-auto PROMPT``, reads JSONL from stdout
    until ``turn.completed`` or process exit, and then the process
    naturally terminates.

    Follow-up messages are handled by spawning a new process with
    ``codex exec --json --full-auto resume THREAD_ID PROMPT``.  The
    thread ID is extracted from the first ``thread.started`` event.
    """

    def __init__(
        self,
        binary_path: str = "codex",
        thread_id: str | None = None,
        extra_env: dict[str, str] | None = None,
        config_overrides: dict[str, Any] | None = None,
    ) -> None:
        self._binary_path = binary_path
        self._process: asyncio.subprocess.Process | None = None
        self._thread_id: str | None = thread_id
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
    def thread_id(self) -> str | None:
        return self._thread_id

    def _build_command(self, parameters: dict[str, Any], config: dict[str, Any], *, resume: bool = False) -> list[str]:
        """Build the command-line arguments for the Codex CLI subprocess."""
        cmd = [
            self._binary_path,
            "exec",
            "--json",
            "--skip-git-repo-check",
        ]

        # Approval / sandbox mode
        approval_mode = config.get("approval_mode", "full-auto")
        if approval_mode == "yolo":
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            cmd.append("--full-auto")

        # Model override
        model = parameters.get("model") or config.get("model")
        if model:
            cmd.extend(["--model", model])

        # Working directory
        working_dir = parameters.get("working_directory")
        if working_dir:
            cmd.extend(["--cd", working_dir])

        # Resume existing thread
        if resume and self._thread_id:
            cmd.extend(["resume", self._thread_id])

        return cmd

    def _build_env(self) -> dict[str, str]:
        """Build environment for the subprocess."""
        env = dict(os.environ)
        # Inject extra env vars (e.g. CODEX_API_KEY from Settings)
        env.update(self._extra_env)
        return env

    async def _start_process(
        self, tool: ToolDefinition, parameters: dict[str, Any], *, resume: bool = False
    ) -> asyncio.subprocess.Process:
        """Spawn the Codex CLI subprocess.

        Kills any existing process first.  The prompt is written to stdin
        after the process starts (matching the Codex SDK pattern).
        """
        await self._cleanup_process()

        config = {**tool.executor_config.get("codex", {})}
        # Apply managed-tool settings overrides (non-empty values only)
        for k, v in self._config_overrides.items():
            if v not in (None, ""):
                config[k] = v
        cmd = self._build_command(parameters, config, resume=resume)
        env = self._build_env()

        # Store for potential restart on follow-up
        self._tool_def = tool
        self._last_parameters = parameters

        logger.info(
            "Starting Codex CLI: %s (thread=%s, resume=%s)",
            " ".join(cmd),
            self._thread_id,
            resume,
        )

        self._stderr_output = ""
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            limit=10 * 1024 * 1024,  # 10 MB buffer
            **new_session_kwargs(),
        )
        self._process = process
        self._done = False

        # Start draining stderr in background to prevent pipe deadlock
        self._stderr_task = asyncio.create_task(self._drain_stderr())

        # Yield to the event loop so we can detect immediate startup failures
        # (e.g. binary not found, permission denied, crash on init).
        await asyncio.sleep(0)
        if process.returncode is not None:
            # Let stderr drain briefly so we get a useful error message
            if self._stderr_task and not self._stderr_task.done():
                try:
                    await asyncio.wait_for(self._stderr_task, timeout=1.0)
                except TimeoutError:
                    self._stderr_task.cancel()
            stderr_hint = self._stderr_output.strip()
            raise RuntimeError(
                f"Codex process exited immediately with code {process.returncode}"
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
                logger.debug("Codex stderr [thread=%s]: %s", self._thread_id, decoded)
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

        # Ensure stderr task finishes
        if self._stderr_task and not self._stderr_task.done():
            try:
                await asyncio.wait_for(self._stderr_task, timeout=2.0)
            except TimeoutError:
                self._stderr_task.cancel()

        if returncode != 0:
            logger.warning(
                "Codex exited with code %d (thread=%s). stderr: %s",
                returncode,
                self._thread_id,
                self._stderr_output.strip() or "(empty)",
            )
        else:
            logger.info(
                "Codex exited normally (thread=%s, code=%d)",
                self._thread_id,
                returncode,
            )

    async def _write_prompt_and_close(self, prompt: str) -> None:
        """Write the prompt to stdin and close it (Codex one-shot model)."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Codex process not started or stdin not available")

        if self._process.returncode is not None:
            # Process already exited before we could write — collect stderr
            # and raise a descriptive error.
            if self._stderr_task and not self._stderr_task.done():
                try:
                    await asyncio.wait_for(self._stderr_task, timeout=1.0)
                except TimeoutError:
                    self._stderr_task.cancel()
            stderr_hint = self._stderr_output.strip()
            raise RuntimeError(
                f"Codex process exited (code {self._process.returncode}) before prompt could be sent"
                + (f": {stderr_hint}" if stderr_hint else "")
            )

        try:
            self._process.stdin.write(prompt.encode("utf-8"))
            await self._process.stdin.drain()
            self._process.stdin.close()
            await self._process.stdin.wait_closed()
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            # The process died between our check and the write/drain.
            if self._stderr_task and not self._stderr_task.done():
                try:
                    await asyncio.wait_for(self._stderr_task, timeout=1.0)
                except TimeoutError:
                    self._stderr_task.cancel()
            stderr_hint = self._stderr_output.strip()
            raise RuntimeError(
                "Failed to write prompt to Codex stdin (transport closed)"
                + (f": {stderr_hint}" if stderr_hint else "")
            ) from exc

        logger.debug("Sent prompt to Codex stdin and closed [thread=%s]", self._thread_id)

    async def _read_events(self) -> AsyncGenerator[ExecutionChunk, None]:
        """Read and yield parsed JSONL events from stdout.

        Each line from stdout is expected to be a JSON object.  Events are
        yielded as ``ExecutionChunk`` instances with ``stream="stdout"`` and the
        raw JSON line as content.  When a ``turn.completed`` or ``turn.failed``
        event is received, ``_done`` is set to True and we stop reading.
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

                if not isinstance(event, dict):
                    continue

                # Capture thread ID from first event
                if event.get("type") == "thread.started":
                    thread_id = event.get("thread_id")
                    if thread_id:
                        self._thread_id = thread_id

                # Turn completion/failure marks end of this invocation
                if event.get("type") in ("turn.completed", "turn.failed"):
                    self._result_text = json.dumps(event)
                    self._done = True
                    # Process will exit naturally after turn completes
                    await self._wait_and_log_exit()
                    break

    async def execute_streaming(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> AsyncGenerator[ExecutionChunk, None]:
        """Start Codex CLI and stream output events.

        Spawns the subprocess, writes the prompt to stdin, closes stdin,
        then yields parsed JSONL events until turn completion or process exit.
        """
        prompt = parameters.get("prompt", "")
        if not prompt:
            raise ValueError("'prompt' parameter is required for codex")

        await self._start_process(tool, parameters)
        await self._write_prompt_and_close(prompt)

        async for chunk in self._read_events():
            yield chunk

    async def restart_with_prompt(self, prompt: str) -> AsyncGenerator[ExecutionChunk, None]:
        """Spawn a new Codex process with ``resume THREAD_ID`` and a follow-up prompt.

        This is the standard multi-turn pattern for Codex CLI — each turn
        is a separate process invocation that resumes the thread.
        """
        if self._tool_def is None:
            raise RuntimeError("Cannot restart: no previous tool definition stored")

        if self._thread_id is None:
            raise RuntimeError("Cannot restart: no thread ID from previous turn")

        logger.info(
            "Restarting Codex for follow-up (thread=%s)",
            self._thread_id,
        )

        await self._cleanup_process()

        await self._start_process(self._tool_def, self._last_parameters, resume=True)
        await self._write_prompt_and_close(prompt)

        async for chunk in self._read_events():
            yield chunk

    async def read_more_events(self) -> AsyncGenerator[ExecutionChunk, None]:
        """Not supported — Codex CLI uses one-shot processes.

        Raises RuntimeError because Codex does not support reading more
        events from a completed turn. Use ``restart_with_prompt()`` instead.
        """
        raise RuntimeError(
            "Codex CLI does not support reading more events from a completed turn; use restart_with_prompt() instead"
        )
        # Make this a generator so the type signature is correct
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
        """Not supported — Codex CLI uses one-shot processes.

        Raises RuntimeError because stdin is closed after the initial prompt.
        Use ``restart_with_prompt()`` for follow-up messages.
        """
        raise RuntimeError(
            "Codex CLI does not support interactive stdin input; use restart_with_prompt() for follow-up messages"
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
        """Kill the Codex subprocess."""
        if self._process:
            logger.info("Cancelling Codex session (thread=%s)", self._thread_id)
        await self._cleanup_process()
        self._done = True
