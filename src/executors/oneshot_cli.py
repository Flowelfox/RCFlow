"""Shared base for one-shot CLI executors (Codex, OpenCode).

Both wrap a CLI that runs one subprocess per turn: spawn, stream JSONL from
stdout until the turn completes, then let the process exit; follow-ups re-spawn
with a resume flag. The subprocess plumbing (stderr drain, exit logging, tree
kill, the unsupported stdin/read-more paths) is identical between them modulo
the agent name and the session-reference field, so it lives here. Subclasses
implement the parts that genuinely differ: command building, env, process
start, prompt write, event parsing, and the streaming/restart entry points.
"""

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from typing import TYPE_CHECKING, Any

from src.executors.base import BaseExecutor, ExecutionChunk, ExecutionResult
from src.utils.process import kill_process_tree

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from src.tools.loader import ToolDefinition

logger = logging.getLogger(__name__)


class OneShotCliExecutor(BaseExecutor):
    """Base for CLI agents that run one subprocess per turn (Codex / OpenCode)."""

    #: Human-readable agent name used in logs and error messages.
    _AGENT_NAME: str = "agent"
    #: Label for the session-reference field in logs ("thread" / "session").
    _SESSION_REF_LABEL: str = "session"

    _STDERR_MAX_BYTES = 64 * 1024

    # Attributes set by subclass __init__ (declared here for the type checker).
    _process: asyncio.subprocess.Process | None
    _done: bool
    _stderr_output: str
    _stderr_task: asyncio.Task[None] | None

    @property
    def _session_ref(self) -> str | None:
        """The agent's resume identifier (thread id / session id), for logs."""
        raise NotImplementedError

    @property
    def is_running(self) -> bool:
        """Whether the process is currently running."""
        return self._process is not None and self._process.returncode is None

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
                logger.debug(
                    "%s stderr [%s=%s]: %s", self._AGENT_NAME, self._SESSION_REF_LABEL, self._session_ref, decoded
                )
        except ConnectionResetError:
            pass
        # CancelledError deliberately propagates: swallowing it defeats the
        # task.cancel() the session lifecycle uses to tear this reader down.

    async def _wait_and_log_exit(self) -> None:
        """Wait for the process to exit and log diagnostics."""
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
                "%s exited with code %d (%s=%s). stderr: %s",
                self._AGENT_NAME,
                returncode,
                self._SESSION_REF_LABEL,
                self._session_ref,
                self._stderr_output.strip() or "(empty)",
            )
        else:
            logger.info(
                "%s exited normally (%s=%s, code=%d)",
                self._AGENT_NAME,
                self._SESSION_REF_LABEL,
                self._session_ref,
                returncode,
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
        """Kill the subprocess and mark the turn done."""
        if self._process:
            logger.info("Cancelling %s session (%s=%s)", self._AGENT_NAME, self._SESSION_REF_LABEL, self._session_ref)
        await self._cleanup_process()
        self._done = True

    async def send_input(self, data: str) -> None:
        """Unsupported — these CLIs use one-shot processes (stdin closed after prompt)."""
        raise RuntimeError(
            f"{self._AGENT_NAME} CLI does not support interactive stdin input; "
            "use restart_with_prompt() for follow-up messages"
        )

    async def read_more_events(self) -> AsyncGenerator[ExecutionChunk, None]:
        """Unsupported — these CLIs use one-shot processes (no reading past turn end)."""
        raise RuntimeError(
            f"{self._AGENT_NAME} CLI does not support reading more events from a completed turn; "
            "use restart_with_prompt() instead"
        )
        # Make this a generator so the type signature is correct.
        yield  # pragma: no cover

    async def execute(self, tool: ToolDefinition, parameters: dict[str, Any]) -> ExecutionResult:
        """Non-streaming execution: collect all chunks and return the final result."""
        collected: list[str] = []
        async for chunk in self.execute_streaming(tool, parameters):
            collected.append(chunk.content)
        output = "\n".join(collected)
        exit_code = self._process.returncode if self._process else None
        return ExecutionResult(output=output, exit_code=exit_code)

    @abstractmethod
    def execute_streaming(
        self, tool: ToolDefinition, parameters: dict[str, Any]
    ) -> AsyncGenerator[ExecutionChunk, None]:
        """Spawn the subprocess for one turn and stream its parsed events."""
        raise NotImplementedError
