"""Claude Code executor backed by the Python Agent SDK (`claude-agent-sdk`).

This is the SDK-based replacement for the raw-CLI ``ClaudeCodeExecutor``.  It
drives Claude Code through :class:`claude_agent_sdk.ClaudeSDKClient` (which spawns
the *same* ``claude`` binary RCFlow already manages, via ``options.cli_path``)
and adapts the SDK's typed message objects back into the **legacy stream-json
line shape** that ``_relay_claude_code_stream`` already parses.  Keeping that
intermediate shape lets the existing relay, diff/monitor/cwd/artifact logic, and
its tests stay unchanged; only permissions + AskUserQuestion move out of the
relay into the SDK ``can_use_tool`` callback (handled by the agent layer).

The converter (:func:`sdk_message_to_events`) is a pure function and is unit
tested without any subprocess/network.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from src.executors.base import BaseExecutor, ExecutionChunk, ExecutionResult

if TYPE_CHECKING:
    from claude_agent_sdk import Message, PermissionResult, ToolPermissionContext

    from src.tools.loader import ToolDefinition

logger = logging.getLogger(__name__)

# Callback type the agent layer supplies to handle AskUserQuestion + permissions.
CanUseTool = Callable[[str, dict[str, Any], "ToolPermissionContext"], Awaitable["PermissionResult"]]


# ---------------------------------------------------------------------------
# Message → legacy stream-json line adapter (pure, testable)
# ---------------------------------------------------------------------------

# The relay checks ``subtype == "max_turns"``; the SDK reports ``error_max_turns``.
_RESULT_SUBTYPE_MAP = {"error_max_turns": "max_turns"}

# Map a TaskNotification status to a Monitor-terminal verb that
# ``_is_monitor_terminal`` recognises (prefix match), so the synthesised
# tool_result is classified as the watch ending.
_TASK_STATUS_VERB = {"completed": "exited", "stopped": "stopped", "failed": "failed"}

# Sentinel pushed to the message queue when the persistent reader ends.
_STREAM_END = object()

# Idle wait before ``read_more_events`` returns control to the drain loop so it
# can re-check whether any monitors are still tracked.
_DRAIN_IDLE_TIMEOUT = 5.0


def _content_block_to_dict(block: Any) -> dict[str, Any] | None:
    """Convert one SDK content block into the legacy JSON block shape."""
    if isinstance(block, TextBlock):
        return {"type": "text", "text": block.text}
    if isinstance(block, ThinkingBlock):
        return {"type": "thinking", "thinking": block.thinking}
    if isinstance(block, ToolUseBlock):
        return {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
    if isinstance(block, ToolResultBlock):
        return {
            "type": "tool_result",
            "tool_use_id": block.tool_use_id,
            "content": block.content,
            "is_error": bool(block.is_error),
        }
    # Unknown / server-tool blocks (ServerToolUseBlock etc.) — skip; the relay
    # only acts on text/thinking/tool_use/tool_result.
    return None


def sdk_message_to_events(message: Message) -> list[dict[str, Any]]:
    """Map one SDK ``Message`` to zero or more legacy stream-json event dicts.

    The returned dicts match exactly what ``_relay_claude_code_stream`` parses
    from raw CLI output (``assistant`` / ``user`` / ``result`` / ``system``),
    so the relay needs no changes for these.
    """
    if isinstance(message, AssistantMessage):
        content = [b for b in (_content_block_to_dict(x) for x in message.content) if b]
        return [{"type": "assistant", "message": {"content": content}}]

    if isinstance(message, UserMessage):
        raw = message.content
        if isinstance(raw, str):
            # A plain user text echo — nothing the relay consumes.
            return []
        content = [b for b in (_content_block_to_dict(x) for x in raw) if b]
        if not content:
            return []
        return [{"type": "user", "message": {"content": content}}]

    if isinstance(message, ResultMessage):
        subtype = _RESULT_SUBTYPE_MAP.get(message.subtype, message.subtype)
        event: dict[str, Any] = {
            "type": "result",
            "subtype": subtype,
            "result": message.result or "",
        }
        if message.total_cost_usd is not None:
            event["cost_usd"] = message.total_cost_usd
        if message.usage:
            event["usage"] = message.usage
        return [event]

    # A Monitor watch's terminal arrives (between turns) as a TaskNotificationMessage
    # keyed by the Monitor tool's ``tool_use_id``.  Synthesise the legacy
    # monitor-terminal ``tool_result`` so the relay's ``_process_monitor_event`` ends
    # the watch (MONITOR_END) with no relay changes.  (Checked before SystemMessage —
    # it is a subclass.)
    if isinstance(message, TaskNotificationMessage):
        if not message.tool_use_id:
            return []
        verb = _TASK_STATUS_VERB.get(message.status, "stopped")
        return [
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message.tool_use_id,
                            "content": f"Monitor {verb}: {message.summary}",
                            "is_error": message.status != "completed",
                        }
                    ]
                },
            }
        ]

    if isinstance(message, SystemMessage):
        # The relay only acts on ``subtype == "usage"``; pass others through
        # harmlessly (init/hook_*/task_* are ignored downstream).
        event = {"type": "system", "subtype": message.subtype}
        data = getattr(message, "data", None)
        if isinstance(data, dict) and "usage" in data:
            event["usage"] = data["usage"]
        return [event]

    # StreamEvent (partial messages) and anything else: not consumed.
    return []


def _event_chunk(event: dict[str, Any]) -> ExecutionChunk:
    return ExecutionChunk(stream="stdout", content=json.dumps(event))


# ---------------------------------------------------------------------------
# SDK-backed executor
# ---------------------------------------------------------------------------


class ClaudeCodeSdkExecutor(BaseExecutor):
    """Persistent Claude Code session driven by the Agent SDK.

    Mirrors the public surface the relay/lifecycle use from the legacy executor
    (``execute_streaming``, ``restart_with_prompt``, ``send_input``,
    ``stop_process``/``cancel``, ``is_running``, ``got_result``, ``exit_code``,
    ``session_id``, ``_on_stderr_line``) but holds a :class:`ClaudeSDKClient`
    instead of a raw subprocess.  Permissions + AskUserQuestion are resolved by
    the injected :attr:`can_use_tool` callback rather than via the relay stream.
    """

    def __init__(
        self,
        binary_path: str = "claude",
        session_id: str | None = None,
        extra_env: dict[str, str] | None = None,
        config_overrides: dict[str, Any] | None = None,
        can_use_tool: CanUseTool | None = None,
    ) -> None:
        self._binary_path = binary_path
        self._session_id: str = session_id or str(uuid.uuid4())
        self._extra_env: dict[str, str] = extra_env or {}
        self._config_overrides: dict[str, Any] = config_overrides or {}
        self._can_use_tool: CanUseTool | None = can_use_tool

        self._client: ClaudeSDKClient | None = None
        self._connected: bool = False
        self._got_result: bool = False
        self._result_text: str = ""
        self._exit_code: int | None = None
        self._cwd: str | None = None
        # Stored so a crashed client can be reopened with resume=.
        self._tool_def: ToolDefinition | None = None
        self._last_parameters: dict[str, Any] = {}
        # Parity with the CLI executor; the SDK surfaces stderr via exceptions/
        # logging rather than a line stream, so this is currently informational.
        self._on_stderr_line: Callable[[str], None] | None = None
        # Single persistent reader of ``client.receive_messages()`` feeding a
        # queue.  One never-closed consumer of the shared stream means turn
        # streaming AND the between-turn Monitor drain both read from the queue
        # (no cross-turn generator conflict) and deferred messages — e.g. a
        # Monitor watch's ``TaskNotificationMessage`` terminal — are never dropped.
        self._queue: asyncio.Queue[Any] | None = None
        self._reader_task: asyncio.Task[None] | None = None

    # -- properties -----------------------------------------------------

    def set_can_use_tool(self, callback: CanUseTool | None) -> None:
        """Install the permission/AskUserQuestion callback (bound to the session)."""
        self._can_use_tool = callback

    @property
    def is_running(self) -> bool:
        """Whether the SDK client is connected (a session is live)."""
        return self._connected and self._client is not None

    @property
    def session_id(self) -> str:
        """Return the persistent session id (shared with RCFlow's session)."""
        return self._session_id

    @property
    def got_result(self) -> bool:
        """Whether the last streamed turn produced a ``ResultMessage``."""
        return self._got_result

    @property
    def exit_code(self) -> int | None:
        """Best-effort exit code (0 after a clean ``ResultMessage``; else None)."""
        return self._exit_code

    # -- options mapping ------------------------------------------------

    def _resolve_cwd(self, parameters: dict[str, Any]) -> str | None:
        wd = parameters.get("working_directory")
        return str(wd) if wd else self._cwd

    def _build_options(self, parameters: dict[str, Any], *, resume: str | None) -> ClaudeAgentOptions:
        config = self._config_overrides
        # Map RCFlow's permission mode to an SDK mode that still consults
        # ``can_use_tool``.  ``bypassPermissions`` would SKIP the callback (and
        # auto-deny AskUserQuestion — the original bug), so we always use
        # "default" and let the callback enforce policy (auto-allow for
        # autonomy, intercept AskUserQuestion / interactive permissions).
        env = self._sdk_env()
        model = parameters.get("model") or config.get("model")
        max_turns = config.get("max_turns")
        cwd = self._resolve_cwd(parameters)

        allowed = parameters.get("allowed_tools")
        allowed_list = (
            [t.strip() for t in allowed.split(",") if t.strip()] if isinstance(allowed, str) else (allowed or [])
        )

        return ClaudeAgentOptions(
            cli_path=self._binary_path,
            env=env,
            permission_mode="default",
            can_use_tool=self._can_use_tool,
            model=model,
            max_turns=max_turns,
            cwd=cwd,
            allowed_tools=allowed_list,
            resume=resume,
            # Persist the same session id the rest of RCFlow tracks so resume
            # across worker restarts lines up.
            extra_args={"session-id": self._session_id} if resume is None else {},
        )

    def _sdk_env(self) -> dict[str, str]:
        """Env overrides passed to the SDK (merged over the worker's env).

        The SDK merges these as ``{**os.environ, **options.env}`` and already
        strips ``CLAUDECODE``.  ``options.env`` can only *override* keys, not
        unset them, so the model-pinning vars the legacy executor used to
        ``pop`` are blanked to ``""`` here.  This matches legacy intent: it stops
        a foreign/stale ``ANTHROPIC_MODEL`` (e.g. a Bedrock ARN meant for
        RCFlow's own LLM) from leaking into the nested claude_code, and the model
        is selected via the ``--model`` flag instead.  Verified: with a bogus
        ``ANTHROPIC_MODEL`` in the parent env and no configured model, Claude Code
        ignores the blank and runs on its default.  ``extra_env`` is passed
        through (e.g. ``CLAUDE_CONFIG_DIR``; an empty ``ANTHROPIC_API_KEY``
        selects OAuth).
        """
        env: dict[str, str] = {"CLAUDE_AVAILABLE_MODELS": "", "ANTHROPIC_MODEL": ""}
        env.update(self._extra_env)
        return env

    # -- streaming ------------------------------------------------------

    async def _ensure_client(self, options: ClaudeAgentOptions) -> ClaudeSDKClient:
        if self._client is None:
            self._client = ClaudeSDKClient(options=options)
            await self._client.connect()
            self._connected = True
            self._queue = asyncio.Queue()
            self._reader_task = asyncio.create_task(self._read_loop(self._client, self._queue))
        return self._client

    async def _read_loop(self, client: ClaudeSDKClient, queue: asyncio.Queue[Any]) -> None:
        """Single persistent consumer of the SDK message stream → queue."""
        try:
            async for message in client.receive_messages():
                await queue.put(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("SDK reader loop ended (session=%s)", self._session_id, exc_info=True)
        finally:
            await queue.put(_STREAM_END)

    async def _stream_turn(self, prompt: str, options: ClaudeAgentOptions) -> AsyncGenerator[ExecutionChunk, None]:
        client = await self._ensure_client(options)
        assert self._queue is not None  # noqa: S101 — set by _ensure_client
        await client.query(prompt)
        self._got_result = False
        self._result_text = ""
        while True:
            message = await self._queue.get()
            if message is _STREAM_END:
                self._connected = False
                return
            for event in sdk_message_to_events(message):
                if event.get("type") == "result":
                    self._got_result = True
                    self._result_text = json.dumps(event)
                    self._exit_code = 0
                    yield _event_chunk(event)
                    return  # turn boundary — the reader keeps filling the queue
                yield _event_chunk(event)

    async def execute_streaming(
        self,
        tool: ToolDefinition,
        parameters: dict[str, Any],
    ) -> AsyncGenerator[ExecutionChunk, None]:
        """Run the initial turn and stream converted events."""
        self._tool_def = tool
        self._last_parameters = parameters
        self._cwd = self._resolve_cwd(parameters)
        prompt = parameters.get("prompt", "")
        options = self._build_options(parameters, resume=None)
        async for chunk in self._stream_turn(prompt, options):
            yield chunk

    async def restart_with_prompt(self, prompt: str) -> AsyncGenerator[ExecutionChunk, None]:
        """Deliver a follow-up turn.

        A live client continues the same session; a dead one is reopened with
        ``resume=`` so Claude Code reconnects to the prior conversation.
        ``_ensure_client`` reuses the existing client when connected (the
        ``resume`` option only applies when a fresh client is created).
        """
        resume = None if (self._client is not None and self._connected) else self._session_id
        options = self._build_options(self._last_parameters, resume=resume)
        async for chunk in self._stream_turn(prompt, options):
            yield chunk

    async def read_more_events(self) -> AsyncGenerator[ExecutionChunk, None]:
        """Drain between-turn messages from the persistent reader's queue.

        Called repeatedly by ``_drain_monitor_events`` while a Monitor watch is
        live.  Yields only ``user`` (tool_result) events — i.e. Monitor events
        and the synthesised ``TaskNotificationMessage`` terminal that ends the
        watch (``MONITOR_END``).  The model's between-turn "Monitor event" wake
        narration and per-notification ``result`` events are dropped so the chat
        is not spammed.  Returns on an idle gap so the drain loop can re-check
        ``_active_monitors`` (it exits once the terminal empties them).
        """
        if self._queue is None:
            return
        while True:
            try:
                message = await asyncio.wait_for(self._queue.get(), timeout=_DRAIN_IDLE_TIMEOUT)
            except TimeoutError:
                return
            if message is _STREAM_END:
                self._connected = False
                return
            for event in sdk_message_to_events(message):
                if event.get("type") == "user":
                    yield _event_chunk(event)

    async def send_input(self, data: str) -> None:
        """Send a follow-up message to the live session.

        NOTE(sdk-migration): the legacy mid-turn ``send_input`` (plan review /
        interactive responses) is superseded by ``can_use_tool``.  This remains
        for plain follow-up text on a live client; callers that need the
        streamed response use ``restart_with_prompt`` instead.
        """
        if self._client is None or not self._connected:
            raise RuntimeError("No live Claude Code SDK session")
        await self._client.query(data)

    # -- lifecycle ------------------------------------------------------

    async def execute(self, tool: ToolDefinition, parameters: dict[str, Any]) -> ExecutionResult:
        """Run one turn to completion and return the concatenated event stream."""
        collected: list[str] = []
        async for chunk in self.execute_streaming(tool, parameters):
            collected.append(chunk.content)
        return ExecutionResult(output="\n".join(collected), exit_code=self._exit_code)

    async def stop_process(self) -> None:
        """Interrupt the current turn and disconnect the client (keep state)."""
        await self._disconnect()

    async def cancel(self) -> None:
        """Full shutdown."""
        await self._disconnect()

    async def _disconnect(self) -> None:
        reader = self._reader_task
        self._reader_task = None
        self._queue = None
        if reader is not None and not reader.done():
            reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader
        client = self._client
        self._client = None
        self._connected = False
        if client is None:
            return
        try:
            await client.interrupt()
        except Exception:
            logger.debug("SDK interrupt failed (session=%s)", self._session_id, exc_info=True)
        try:
            await client.disconnect()
        except Exception:
            logger.debug("SDK disconnect failed (session=%s)", self._session_id, exc_info=True)
