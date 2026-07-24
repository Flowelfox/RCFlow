"""ACP executor — drives any Agent Client Protocol agent as a stdio subprocess.

One executor covers every ACP-speaking coding agent (OpenCode natively,
Codex via the ``codex-acp`` adapter); per-agent differences live in the tool
definition's ``executor_config.acp`` (binary + args) and the agent layer's
env builder — never in code branches here.

Boundary rule: this module knows the *protocol*, never the session. It
exposes a stream of normalised event dicts (JSON in ``ExecutionChunk``s, the
same transport every other agent executor uses) plus an injected permission
callback — all RCFlow side effects (buffer pushes, PermissionManager,
metadata) live in :mod:`src.core.agent_acp`.

The RCFlow client side declines the optional ACP capabilities in Phase 1:
no ``fs/*`` (agents use their own filesystem access, as today) and no
``terminal/*``. ``session/request_permission`` is the one client method that
matters — it is relayed through :attr:`AcpExecutor.set_permission_callback`.

Translation from typed SDK update objects into plain dicts is a pure
function (:func:`acp_update_to_event`) so the vocabulary mapping is testable
without any subprocess.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from acp import Client, spawn_agent_process, text_block
from acp.schema import AllowedOutcome, DeniedOutcome, EnvVariable, McpServerStdio, RequestPermissionResponse

from src.executors.base import BaseExecutor, ExecutionChunk, ExecutionResult
from src.utils.process import kill_process_tree

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from acp.schema import AcpMcpServer, HttpMcpServer, SseMcpServer

    from src.tools.loader import ToolDefinition

logger = logging.getLogger(__name__)

# Injected by the agent layer: receives the tool-call dict and the permission
# options (dicts with option_id / name / kind), returns the chosen option_id
# or None to cancel.
PermissionCallback = "Callable[[dict[str, Any], list[dict[str, Any]]], Awaitable[str | None]]"

# Sentinel closing the update queue when the agent process/stream ends.
_STREAM_END = object()

_DEFAULT_TURN_TIMEOUT = 1800.0


# ---------------------------------------------------------------------------
# Typed SDK update → normalised event dict (pure, testable)
# ---------------------------------------------------------------------------


def _content_text(content: Any) -> str:
    """Best-effort text out of an ACP content block."""
    return str(getattr(content, "text", "") or "")


def _tool_call_event(update: Any, kind: str) -> dict[str, Any]:
    locations = [p for loc in (getattr(update, "locations", None) or []) if (p := str(getattr(loc, "path", "") or ""))]
    content_texts: list[str] = []
    diff: dict[str, Any] | None = None
    for item in getattr(update, "content", None) or []:
        item_type = str(getattr(item, "type", "") or "")
        if item_type == "diff":
            diff = {
                "path": str(getattr(item, "path", "") or ""),
                "old_text": getattr(item, "old_text", None),
                "new_text": str(getattr(item, "new_text", "") or ""),
            }
            if diff["path"]:
                locations.append(diff["path"])
        else:
            inner = getattr(item, "content", None)
            text = _content_text(inner) if inner is not None else _content_text(item)
            if text:
                content_texts.append(text)
    raw_output = getattr(update, "raw_output", None)
    if raw_output is not None and not isinstance(raw_output, str):
        raw_output = json.dumps(raw_output, default=str)
    return {
        "type": kind,
        "id": str(getattr(update, "tool_call_id", "") or ""),
        "title": getattr(update, "title", None),
        "kind": getattr(update, "kind", None),
        "status": getattr(update, "status", None),
        "raw_input": getattr(update, "raw_input", None) or {},
        "raw_output": raw_output,
        "locations": locations,
        "content_texts": content_texts,
        "diff": diff,
    }


def acp_update_to_event(update: Any) -> dict[str, Any] | None:
    """Map one ACP ``session/update`` object to a normalised event dict.

    Returns None for update kinds RCFlow does not consume. Works on attribute
    access only (no SDK class checks) so tests can drive it with lightweight
    stand-ins.
    """
    kind = str(getattr(update, "session_update", "") or "")

    if kind in ("agent_message_chunk", "agent_thought_chunk"):
        text = _content_text(getattr(update, "content", None))
        if not text:
            return None
        return {
            "type": "text" if kind == "agent_message_chunk" else "thought",
            "text": text,
            "message_id": getattr(update, "message_id", None),
        }

    if kind in ("tool_call", "tool_call_update"):
        return _tool_call_event(update, kind)

    if kind == "plan":
        return {
            "type": "plan",
            "entries": [
                {
                    "content": str(getattr(e, "content", "") or ""),
                    "priority": str(getattr(e, "priority", "") or ""),
                    "status": str(getattr(e, "status", "") or ""),
                }
                for e in (getattr(update, "entries", None) or [])
            ],
        }

    if kind == "usage_update":
        cost = getattr(update, "cost", None)
        return {
            "type": "usage",
            "used": getattr(update, "used", None),
            "size": getattr(update, "size", None),
            "cost_amount": getattr(cost, "amount", None) if cost is not None else None,
            "cost_currency": getattr(cost, "currency", None) if cost is not None else None,
        }

    if kind == "available_commands_update":
        return {
            "type": "available_commands",
            "commands": [
                {
                    "name": str(getattr(c, "name", "") or ""),
                    "description": str(getattr(c, "description", "") or ""),
                }
                for c in (getattr(update, "available_commands", None) or [])
            ],
        }

    # current_mode_update / config options / anything else: not consumed (yet).
    return None


def _permission_option_dicts(options: Any) -> list[dict[str, Any]]:
    return [
        {
            "option_id": str(getattr(o, "option_id", "") or ""),
            "name": str(getattr(o, "name", "") or ""),
            "kind": str(getattr(o, "kind", "") or ""),
        }
        for o in (options or [])
    ]


class _ExecutorAcpClient(Client):
    """RCFlow's ACP client half — feeds the executor's update queue.

    Deliberately session-agnostic: updates become normalised dicts on the
    queue; permission requests go through the injected callback. fs and
    terminal methods are never advertised, so the inherited defaults are
    unreachable.
    """

    def __init__(self, executor: AcpExecutor) -> None:
        self._executor = executor

    async def session_update(self, session_id: str | None = None, update: Any = None, **kwargs: Any) -> None:
        event = acp_update_to_event(update)
        if event is not None:
            await self._executor._push_event(event)

    async def request_permission(
        self,
        session_id: Any = None,
        tool_call: Any = None,
        options: Any = None,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        callback = self._executor._permission_callback
        option_dicts = _permission_option_dicts(options)
        tool_call_event = _tool_call_event(tool_call, "tool_call") if tool_call is not None else {}
        if callback is None:
            # No handler installed — fail closed.
            logger.warning("ACP permission request with no callback installed; cancelling")
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        chosen = await callback(tool_call_event, option_dicts)
        if chosen is None:
            return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))
        return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id=chosen))


class AcpExecutor(BaseExecutor):
    """Persistent ACP agent session over one stdio subprocess.

    The process and ACP session live across turns; follow-ups are further
    ``session/prompt`` calls on the same connection. If the process dies and
    the agent advertised ``load_session``, the next turn respawns and resumes.
    """

    def __init__(
        self,
        binary_path: str,
        args: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
        config_overrides: dict[str, Any] | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
    ) -> None:
        self._binary_path = binary_path
        self._args: list[str] = list(args or [])
        self._extra_env: dict[str, str] = extra_env or {}
        self._config_overrides: dict[str, Any] = config_overrides or {}
        self._mcp_servers: list[dict[str, Any]] = mcp_servers or []
        self._permission_callback: Any = None

        self._stack: AsyncExitStack | None = None
        self._conn: Any = None
        self._proc: Any = None
        self._acp_session_id: str | None = None
        self._cwd: str | None = None
        self._load_session_supported: bool = False
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._got_result: bool = False
        self._last_stop_reason: str | None = None
        self._tool_def: ToolDefinition | None = None

    # -- properties -----------------------------------------------------

    @property
    def is_running(self) -> bool:
        """Whether the agent subprocess is alive."""
        return self._proc is not None and self._proc.returncode is None

    @property
    def acp_session_id(self) -> str | None:
        """The agent-issued ACP session id (persisted for resume)."""
        return self._acp_session_id

    @property
    def got_result(self) -> bool:
        """Whether the last turn completed with a stop reason."""
        return self._got_result

    @property
    def last_stop_reason(self) -> str | None:
        """Stop reason of the most recent completed turn."""
        return self._last_stop_reason

    @property
    def exit_code(self) -> int | None:
        """Subprocess exit code, or None while running / never started."""
        return self._proc.returncode if self._proc is not None else None

    def set_permission_callback(self, callback: Any) -> None:
        """Install the permission relay (bound to the session by the agent layer)."""
        self._permission_callback = callback

    def set_resume_target(self, acp_session_id: str) -> None:
        """Arm a resume: the next connection issues ``session/load`` for this id."""
        self._acp_session_id = acp_session_id

    async def _push_event(self, event: dict[str, Any]) -> None:
        await self._queue.put(event)

    # -- connection lifecycle -------------------------------------------

    def _turn_timeout(self) -> float:
        timeout = self._config_overrides.get("timeout")
        try:
            return float(timeout) if timeout else _DEFAULT_TURN_TIMEOUT
        except (TypeError, ValueError):
            return _DEFAULT_TURN_TIMEOUT

    async def _ensure_session(self, cwd: str) -> None:
        """Spawn + initialize + create (or load) the ACP session if needed."""
        if self._conn is not None and self.is_running:
            return

        # Respawn-after-death: the previous connection is dead but its
        # AsyncExitStack + subprocess resources are still open. Tear them down
        # (and drop any stale queued events) before spawning a replacement,
        # otherwise the old transport/pipes leak on every reconnect.
        if self._conn is not None or self._stack is not None:
            await self.stop_process()
        self._drain_queue()

        import acp as acp_sdk  # noqa: PLC0415 — module-level name kept light for tests

        env = {**os.environ, **self._extra_env}
        self._stack = AsyncExitStack()
        self._conn, self._proc = await self._stack.enter_async_context(
            spawn_agent_process(_ExecutorAcpClient(self), self._binary_path, *self._args, env=env)
        )
        init = await self._conn.initialize(protocol_version=acp_sdk.PROTOCOL_VERSION)
        caps = getattr(init, "agent_capabilities", None)
        self._load_session_supported = bool(getattr(caps, "load_session", False))
        logger.info(
            "ACP agent started: %s (load_session=%s)",
            self._binary_path,
            self._load_session_supported,
        )

        mcp_servers = self._typed_mcp_servers()
        resume_id = self._acp_session_id
        if resume_id and self._load_session_supported:
            await self._conn.load_session(cwd=cwd, session_id=resume_id, mcp_servers=mcp_servers)
            self._cwd = cwd
            logger.info("ACP session resumed: %s", resume_id)
            return
        session = await self._conn.new_session(cwd=cwd, mcp_servers=mcp_servers)
        self._acp_session_id = session.session_id
        self._cwd = cwd
        logger.info("ACP session created: %s", self._acp_session_id)

    def _typed_mcp_servers(self) -> list[HttpMcpServer | SseMcpServer | AcpMcpServer | McpServerStdio]:
        """Convert the agent layer's plain dicts into typed stdio MCP entries."""
        return [
            McpServerStdio(
                name=str(entry.get("name", "")),
                command=str(entry.get("command", "")),
                args=[str(a) for a in entry.get("args", [])],
                env=[
                    EnvVariable(name=str(e.get("name", "")), value=str(e.get("value", "")))
                    for e in entry.get("env", [])
                ],
            )
            for entry in self._mcp_servers
        ]

    async def _run_turn(self, prompt: str) -> AsyncGenerator[ExecutionChunk, None]:
        """One ``session/prompt`` turn: drain updates, finish with turn_end/error."""
        assert self._conn is not None and self._acp_session_id is not None  # noqa: S101
        self._got_result = False
        self._last_stop_reason = None

        prompt_task = asyncio.create_task(
            self._conn.prompt(session_id=self._acp_session_id, prompt=[text_block(prompt)])
        )
        deadline = asyncio.get_running_loop().time() + self._turn_timeout()

        try:
            while True:
                if prompt_task.done():
                    # The SDK dispatches update notifications as separate tasks,
                    # so ones that arrived on the wire *before* the prompt
                    # response may not have reached the queue yet. Grace-drain
                    # until the queue stays quiet.
                    try:
                        event = await asyncio.wait_for(self._queue.get(), timeout=0.15)
                    except TimeoutError:
                        break
                    if event is _STREAM_END:
                        break
                    yield _event_chunk(event)
                    continue
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    await self.cancel()
                    yield _event_chunk({"type": "error", "message": "ACP turn timed out"})
                    break
                try:
                    event = await asyncio.wait_for(self._queue.get(), timeout=min(remaining, 0.5))
                except TimeoutError:
                    continue
                if event is _STREAM_END:
                    break
                yield _event_chunk(event)

            if prompt_task.done() and not prompt_task.cancelled():
                exc = prompt_task.exception()
                if exc is not None:
                    yield _event_chunk({"type": "error", "message": f"ACP prompt failed: {exc}"})
                else:
                    response = prompt_task.result()
                    stop_reason = str(getattr(response, "stop_reason", "") or "")
                    self._got_result = True
                    self._last_stop_reason = stop_reason
                    yield _event_chunk({"type": "turn_end", "stop_reason": stop_reason})
        finally:
            if not prompt_task.done():
                prompt_task.cancel()

    # -- BaseExecutor surface -------------------------------------------

    async def _drain_stderr_tail(self) -> str:
        """Best-effort read of the agent's piped stderr after a failure.

        The SDK pipes the child's stderr; when the process dies before the
        handshake the interesting diagnostics are there, so surface them in
        the error message instead of losing them with the pipe.
        """
        proc = self._proc
        stderr = getattr(proc, "stderr", None)
        if proc is None or stderr is None:
            return ""
        try:
            data = await asyncio.wait_for(stderr.read(8192), timeout=1.0)
        except Exception:
            return ""
        tail = data.decode("utf-8", errors="replace").strip()
        return f" — agent stderr: {tail[-2000:]}" if tail else ""

    async def execute_streaming(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> AsyncGenerator[ExecutionChunk, None]:
        """Start (or reuse) the agent session and run the initial prompt turn."""
        self._tool_def = tool
        cwd = str(parameters.get("working_directory") or ".")
        try:
            await self._ensure_session(cwd)
        except Exception as e:
            logger.exception("ACP agent failed to start: %s", self._binary_path)
            stderr_tail = await self._drain_stderr_tail()
            yield _event_chunk({"type": "error", "message": f"ACP agent failed to start: {e}{stderr_tail}"})
            return
        async for chunk in self._run_turn(str(parameters.get("prompt") or "")):
            yield chunk

    async def restart_with_prompt(self, prompt: str) -> AsyncGenerator[ExecutionChunk, None]:
        """Run a follow-up turn (reconnecting + resuming if the process died)."""
        cwd = self._cwd or "."
        try:
            await self._ensure_session(cwd)
        except Exception as e:
            logger.exception("ACP agent failed to restart: %s", self._binary_path)
            stderr_tail = await self._drain_stderr_tail()
            yield _event_chunk({"type": "error", "message": f"ACP agent failed to restart: {e}{stderr_tail}"})
            return
        async for chunk in self._run_turn(prompt):
            yield chunk

    async def execute(self, tool: ToolDefinition, parameters: dict[str, Any]) -> ExecutionResult:
        """Aggregate a full streaming run (test/one-shot convenience)."""
        parts: list[str] = []
        async for chunk in self.execute_streaming(tool, parameters):
            parts.append(chunk.content)
        return ExecutionResult(output="\n".join(parts), exit_code=0 if self._got_result else 1)

    async def send_input(self, data: str) -> None:
        """Unused — ACP turns are prompt-driven, not stdin-driven."""

    async def cancel(self) -> None:
        """Cancel the in-flight turn (``session/cancel``)."""
        if self._conn is not None and self._acp_session_id is not None:
            try:
                await self._conn.cancel(session_id=self._acp_session_id)
            except Exception:
                logger.debug("ACP cancel failed", exc_info=True)

    def _drain_queue(self) -> None:
        """Discard any events left over from a previous (dead) connection."""
        while not self._queue.empty():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()

    async def stop_process(self) -> None:
        """Tear down the connection and subprocess."""
        stack = self._stack
        self._stack = None
        self._conn = None
        if stack is not None:
            try:
                await stack.aclose()
            except Exception:
                logger.debug("ACP teardown error", exc_info=True)
        proc = self._proc
        self._proc = None
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            # Escalate to a tree kill if the agent ignores SIGTERM, so no
            # orphaned agent/MCP child keeps running.
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except TimeoutError:
                await kill_process_tree(proc)


def _event_chunk(event: dict[str, Any]) -> ExecutionChunk:
    return ExecutionChunk(stream="stdout", content=json.dumps(event, default=str))
