"""ACP agent methods for PromptRouter.

Session-side lifecycle for agents driven over the Agent Client Protocol
(OpenCode natively, Codex via the ``codex-acp`` adapter). One collaborator
serves every ACP agent — per-agent differences live in the tool definition
(``executor_config.acp``) and the per-agent env builders, never in code
branches here.

Composition collaborator — ``PromptRouter`` owns an :class:`AcpAgent`
instance (``self._acp``) and delegates its entry points to it. Shared router
state / sibling behaviour is reached through ``self._r``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.agent_auth import agent_configuration_issue
from src.core.agents import truncate_tool_output
from src.core.buffer import MessageType
from src.core.cwd_tracking import apply_agent_cwd, infer_cwd_from_tool_paths
from src.core.permissions import PermissionDecision, PermissionManager
from src.core.session import ActivityState, SessionStatus, SessionType
from src.executors.acp import AcpExecutor
from src.services.mcp_bridge import RCFLOW_MCP_SERVER_NAME

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from src.core.llm import ToolCallRequest
    from src.core.prompt_router import PromptRouter
    from src.core.session import ActiveSession
    from src.executors.base import ExecutionChunk
    from src.tools.loader import ToolDefinition

logger = logging.getLogger(__name__)

_truncate_tool_output = truncate_tool_output


def worker_loopback_url(settings: Any) -> str:
    """Loopback base URL of this worker for agent subprocesses (rcflow-mcp).

    Scheme must match how uvicorn was launched: TLS is on when WSS is enabled
    (auto self-signed certs) or explicit cert paths are configured.
    """
    port = getattr(settings, "RCFLOW_PORT", None) or 53890
    tls = bool(
        settings is not None
        and (
            getattr(settings, "WSS_ENABLED", False)
            or (getattr(settings, "SSL_CERTFILE", "") and getattr(settings, "SSL_KEYFILE", ""))
        )
    )
    return f"{'https' if tls else 'http'}://127.0.0.1:{port}"


def resolve_mcp_proxy_command() -> tuple[str, list[str]] | None:
    """Command + args that launch the ``rcflow-mcp`` stdio proxy, or None.

    Frozen (PyInstaller) installs ship a single ``rcflow`` executable, so the
    proxy runs as its ``mcp-proxy`` subcommand; dev/venv installs use the
    ``rcflow-mcp`` console script next to the interpreter.
    """
    from src.paths import is_frozen  # noqa: PLC0415

    if is_frozen():
        return sys.executable, ["mcp-proxy"]
    proxy = Path(sys.executable).parent / ("rcflow-mcp.exe" if sys.platform == "win32" else "rcflow-mcp")
    if proxy.is_file():
        return str(proxy), []
    return None


# Maps the ACP tool name (the tool definition name, e.g. "opencode") to the
# per-tool settings key used for env building, auth preflight, and the
# expose_rcflow_tools toggle. Identity today; kept as a hook for adapter
# names diverging from settings keys (e.g. a future "codex" via codex-acp).
def _settings_key(tool_def: ToolDefinition) -> str:
    return tool_def.name


class AcpAgent:
    """ACP agent subprocess lifecycle collaborator for PromptRouter."""

    def __init__(self, router: PromptRouter) -> None:
        self._r = router

    # -- spawn-time wiring ----------------------------------------------

    def _build_acp_extra_env(self, settings_key: str) -> dict[str, str]:
        """Per-agent env for the ACP subprocess (reuses the legacy builders)."""
        if settings_key == "opencode":
            return self._r._opencode._build_opencode_extra_env()
        if settings_key == "codex":
            return self._r._codex._build_codex_extra_env()
        return {}

    def _build_mcp_servers_param(self, session: ActiveSession, settings_key: str) -> list[dict[str, Any]]:
        """Client-provided MCP servers for ``session/new`` — the RCFlow bridge.

        On the ACP path the per-session token travels as explicit protocol
        data (the ``env`` entries below), not via process-env inheritance,
        and no agent config file needs a managed block.
        """
        bridge = self._r._mcp_bridge
        if bridge is None:
            return []
        enabled = bool(self._r._get_managed_config_overrides(settings_key).get("expose_rcflow_tools"))
        if not enabled:
            return []
        proxy = resolve_mcp_proxy_command()
        if proxy is None:
            logger.warning("rcflow-mcp proxy not available; MCP bridge disabled for ACP agent")
            return []
        command, args = proxy
        token = bridge.tokens.issue(session.id)
        return [
            {
                "name": RCFLOW_MCP_SERVER_NAME,
                "command": command,
                "args": args,
                "env": [
                    {"name": "RCFLOW_MCP_TOKEN", "value": token},
                    {"name": "RCFLOW_MCP_URL", "value": worker_loopback_url(self._r._settings)},
                ],
            }
        ]

    def _make_permission_callback(self, session: ActiveSession):
        """Build the executor's permission relay bound to *session*.

        Maps an ACP ``session/request_permission`` onto RCFlow's interactive
        permission flow: ALLOW selects the agent's allow-once option (rule
        caching stays on RCFlow's side), DENY selects reject-once (or cancels
        when the agent offered no reject option).
        """

        async def on_permission(tool_call: dict[str, Any], options: list[dict[str, Any]]) -> str | None:
            if session.permission_manager is None:
                session.permission_manager = PermissionManager()
            tool_name = str(tool_call.get("title") or tool_call.get("kind") or "acp_tool")
            tool_input = tool_call.get("raw_input") or {}
            decision = await self._r._handle_permission_check(session, tool_name, tool_input)
            # Map a one-time RCFlow decision onto the ACP option of matching
            # scope. Prefer the *_once kind so a single Allow/Deny is not
            # silently escalated to a permanent allow_always/reject_always
            # (rule persistence stays on RCFlow's PermissionManager, not the
            # agent's). Fall back to any option of the wanted polarity, then —
            # only on allow — to the first offered option.
            wanted = "allow" if decision == PermissionDecision.ALLOW else "reject"
            preferred = f"{wanted}_once"
            for opt in options:
                if str(opt.get("kind", "")) == preferred:
                    return opt["option_id"]
            for opt in options:
                if str(opt.get("kind", "")).startswith(wanted):
                    return opt["option_id"]
            # No option of the wanted polarity — cancel rather than pick an
            # opposite-polarity option (never turn an Allow into a reject).
            return None

        return on_permission

    # -- session start ---------------------------------------------------

    async def _start_acp(
        self,
        session: ActiveSession,
        tool_def: ToolDefinition,
        tool_call: ToolCallRequest,
    ) -> str:
        """Start an ACP agent session: spawn subprocess, begin background streaming."""
        settings_key = _settings_key(tool_def)
        auth_issue = agent_configuration_issue(
            settings_key,
            self._r._settings,
            self._r._tool_settings,
            self._r._tool_manager,
        )
        if auth_issue is not None:
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": auth_issue,
                    "code": "AGENT_CONFIG_ERROR",
                    "agent_type": settings_key,
                },
            )
            session.set_activity(ActivityState.IDLE)
            return auth_issue

        working_dir = tool_call.tool_input.get("working_directory", ".")
        selected_wt = session.metadata.get("selected_worktree_path")
        if selected_wt:
            working_dir = selected_wt
        working_path = self._r._resolve_working_directory(working_dir)
        try:
            is_dir = working_path.is_dir()
        except OSError as e:
            error_msg = f"Cannot access directory {working_dir}: {e}"
            session.buffer.push_text(
                MessageType.ERROR,
                {"session_id": session.id, "content": error_msg, "code": "INVALID_WORKING_DIRECTORY"},
            )
            return error_msg
        if not is_dir:
            error_msg = f"Directory does not exist: {working_dir}"
            session.buffer.push_text(
                MessageType.ERROR,
                {"session_id": session.id, "content": error_msg, "code": "INVALID_WORKING_DIRECTORY"},
            )
            return error_msg

        tool_call.tool_input["working_directory"] = str(working_path)

        # Explicitly request the ACP executor — tool_def.executor may still say
        # "opencode"/"codex" when the env flag routed the agent onto ACP.
        executor = self._r._get_executor("acp", tool_def)
        assert isinstance(executor, AcpExecutor)  # noqa: S101
        executor.set_permission_callback(self._make_permission_callback(session))
        executor._mcp_servers = self._build_mcp_servers_param(session, settings_key)

        session.acp_executor = executor
        session.session_type = SessionType.LONG_RUNNING
        session.set_activity(ActivityState.RUNNING_SUBPROCESS)

        session.metadata["acp_agent_name"] = settings_key
        session.metadata["acp_working_directory"] = str(working_path)
        session.metadata["acp_tool_name"] = tool_def.name
        session.metadata["acp_parameters"] = tool_call.tool_input
        # Seed agent-cwd mirror for the live worktree badge.
        session.metadata["agent_cwd"] = str(working_path)
        session.agent_cwd = str(working_path)

        task = asyncio.create_task(self._stream_acp_events(session, executor, tool_def, tool_call))
        session._acp_stream_task = task

        display_name = tool_def.display_name or tool_def.name
        session.subprocess_started_at = datetime.now(UTC)
        session.subprocess_current_tool = None
        session.subprocess_type = settings_key
        session.subprocess_display_name = display_name
        session.subprocess_working_directory = str(working_path)
        session.buffer.push_ephemeral(
            MessageType.SUBPROCESS_STATUS,
            {
                "session_id": session.id,
                "subprocess_type": settings_key,
                "display_name": display_name,
                "working_directory": str(working_path),
                "current_tool": None,
                "started_at": session.subprocess_started_at_iso,
            },
        )

        return f"{display_name} session started in {working_path}"

    # -- relay -----------------------------------------------------------

    def _push_subprocess_status(self, session: ActiveSession, current_tool: str | None) -> None:
        session.subprocess_current_tool = current_tool
        if session.subprocess_started_at is None:
            return
        session.buffer.push_ephemeral(
            MessageType.SUBPROCESS_STATUS,
            {
                "session_id": session.id,
                "subprocess_type": session.subprocess_type,
                "display_name": session.subprocess_display_name,
                "working_directory": session.subprocess_working_directory,
                "current_tool": current_tool,
                "started_at": session.subprocess_started_at_iso,
            },
        )

    def _track_cwd_from_locations(self, session: ActiveSession, locations: list[str]) -> None:
        if not locations:
            return
        spawn_cwd = session.metadata.get("acp_working_directory")
        repo_path = session.main_project_path or spawn_cwd
        new_cwd = infer_cwd_from_tool_paths(locations, spawn_cwd, repo_path)
        if new_cwd and apply_agent_cwd(session, new_cwd) and self._r._session_manager is not None:
            self._r._session_manager.broadcast_session_update(session)
            self._r._fire_pr_detect(session)

    async def _relay_acp_stream(
        self,
        session: ActiveSession,
        executor: AcpExecutor,
        stream: AsyncGenerator[ExecutionChunk, None],
    ) -> bool:
        """Translate normalised ACP events into RCFlow buffer messages.

        Returns True when the turn completed with a stop reason (including a
        user cancel), False when the stream ended in an error or unexpectedly.
        """
        text_chunks: list[str] = []
        completed = False

        async for chunk in stream:
            # Persist the agent-issued session id the moment it exists (set
            # synchronously inside the executor's first ``session/new`` before
            # any event streams). Writing it here — not only at stream end —
            # means a pause or worker restart mid-first-turn still leaves the
            # session resumable instead of falling back to the outer LLM.
            if executor.acp_session_id and session.metadata.get("acp_session_id") != executor.acp_session_id:
                session.metadata["acp_session_id"] = executor.acp_session_id
            line = chunk.content.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            etype = event.get("type")

            if etype == "text":
                text = event.get("text", "")
                if text:
                    text_chunks.append(text)
                    session.buffer.push_text(
                        MessageType.TEXT_CHUNK,
                        {"session_id": session.id, "content": text, "finished": False},
                    )
                    self._r._fire_text_artifact_scan(session, [text])

            elif etype == "thought":
                text = event.get("text", "")
                if text:
                    session.buffer.push_text(
                        MessageType.THINKING,
                        {"session_id": session.id, "content": text},
                    )

            elif etype == "tool_call":
                tool_name = str(event.get("title") or event.get("kind") or "tool")
                text_chunks.clear()
                session.buffer.push_text(
                    MessageType.TOOL_START,
                    {
                        "session_id": session.id,
                        "tool_name": tool_name,
                        "tool_input": event.get("raw_input") or {},
                    },
                )
                self._push_subprocess_status(session, tool_name)
                self._track_cwd_from_locations(session, event.get("locations") or [])

            elif etype == "tool_call_update":
                self._track_cwd_from_locations(session, event.get("locations") or [])
                status = event.get("status")
                if status in ("completed", "cancelled", "failed"):
                    output = "\n".join(event.get("content_texts") or []) or (event.get("raw_output") or "")
                    output = _truncate_tool_output(str(output)) if output else ""
                    if output:
                        session.buffer.push_text(
                            MessageType.TOOL_OUTPUT,
                            {
                                "session_id": session.id,
                                "tool_name": str(event.get("title") or "tool"),
                                "content": output,
                                "stream": "stdout",
                                "is_error": status == "failed",
                            },
                        )
                        self._r._fire_text_artifact_scan(session, [output])
                    self._push_subprocess_status(session, None)

            elif etype == "plan":
                todos = [
                    {"content": e.get("content", ""), "status": e.get("status", "pending")}
                    for e in event.get("entries") or []
                ]
                session.update_todos(todos)
                session.buffer.push_text(
                    MessageType.TODO_UPDATE,
                    {"session_id": session.id, "todos": todos},
                )

            elif etype == "usage":
                usage = {
                    "used": event.get("used"),
                    "size": event.get("size"),
                    "cost_amount": event.get("cost_amount"),
                    "cost_currency": event.get("cost_currency"),
                }
                session.metadata["acp_usage"] = usage
                if session._on_update:
                    session._on_update()

            elif etype == "available_commands":
                session.metadata["acp_available_commands"] = event.get("commands") or []

            elif etype == "turn_end":
                completed = True
                stop_reason = event.get("stop_reason", "")
                session.set_activity(ActivityState.IDLE)
                if stop_reason == "end_turn":
                    summary_text = "".join(text_chunks).strip() or "Agent task completed"
                    self._r._fire_summary_task(session, summary_text)
                    self._r._fire_task_update_task(session, summary_text)
                elif stop_reason in ("max_tokens", "max_turn_requests", "refusal"):
                    session.buffer.push_text(
                        MessageType.ERROR,
                        {
                            "session_id": session.id,
                            "content": f"Agent stopped: {stop_reason}",
                            "code": "ACP_STOP",
                        },
                    )

            elif etype == "error":
                message = event.get("message", "ACP agent error")
                logger.warning("ACP error event (session=%s): %s", session.id, message)
                session.buffer.push_text(
                    MessageType.ERROR,
                    {"session_id": session.id, "content": message, "code": "ACP_ERROR"},
                )

        return completed

    async def _stream_acp_events(
        self,
        session: ActiveSession,
        executor: AcpExecutor,
        tool_def: ToolDefinition,
        tool_call: ToolCallRequest,
    ) -> None:
        """Background task: read ACP events and push to the session buffer."""
        try:
            completed = await self._relay_acp_stream(
                session, executor, executor.execute_streaming(tool_def, tool_call.tool_input)
            )
        except Exception as e:
            logger.exception("ACP streaming error in session %s", session.id)
            session.buffer.push_text(MessageType.AGENT_GROUP_END, {"session_id": session.id})
            session.buffer.push_text(
                MessageType.ERROR,
                {"session_id": session.id, "content": f"ACP agent error: {e}", "code": "ACP_ERROR"},
            )
            await self._end_acp_session(session)
            return

        # acp_session_id is persisted incrementally inside _relay_acp_stream.
        session.buffer.push_text(MessageType.AGENT_GROUP_END, {"session_id": session.id})

        if not completed:
            logger.info("ACP stream ended without completion (session=%s), ending session", session.id)
            await self._end_acp_session(session)
            return

        self._r.schedule_pending_drain(session)

    # -- follow-ups / teardown -------------------------------------------

    async def _forward_to_acp(self, session: ActiveSession, text: str) -> None:
        """Forward a follow-up message to the live ACP session (same process)."""
        executor = session.acp_executor
        if executor is None:
            return
        if session.status == SessionStatus.PAUSED:
            return

        session.set_activity(ActivityState.RUNNING_SUBPROCESS)

        if session.subprocess_started_at is None:
            session.subprocess_started_at = datetime.now(UTC)
            session.subprocess_type = session.metadata.get("acp_agent_name", "acp")
            tool_name = session.metadata.get("acp_tool_name", "")
            tool_def = self._r._tool_registry.get(tool_name) if tool_name else None
            session.subprocess_display_name = (
                tool_def.display_name if tool_def and tool_def.display_name else tool_name or "Agent"
            )
            session.subprocess_working_directory = session.metadata.get("acp_working_directory", "")
        self._push_subprocess_status(session, None)

        session.buffer.push_text(
            MessageType.AGENT_GROUP_START,
            {
                "session_id": session.id,
                "tool_name": session.metadata.get("acp_tool_name", "acp"),
                "display_name": session.subprocess_display_name,
            },
        )

        session._acp_stream_task = asyncio.create_task(self._continue_acp_with_prompt(session, executor, text))

    async def _continue_acp_with_prompt(
        self,
        session: ActiveSession,
        executor: AcpExecutor,
        prompt: str,
    ) -> None:
        """Run a follow-up prompt turn and stream its events."""
        try:
            completed = await self._relay_acp_stream(session, executor, executor.restart_with_prompt(prompt))
        except Exception as e:
            logger.exception("ACP follow-up error in session %s", session.id)
            session.buffer.push_text(MessageType.AGENT_GROUP_END, {"session_id": session.id})
            session.buffer.push_text(
                MessageType.ERROR,
                {"session_id": session.id, "content": f"ACP agent error: {e}", "code": "ACP_ERROR"},
            )
            await self._end_acp_session(session)
            return

        session.buffer.push_text(MessageType.AGENT_GROUP_END, {"session_id": session.id})

        if not completed:
            logger.info("ACP follow-up ended without completion (session=%s), ending session", session.id)
            await self._end_acp_session(session)
            return

        self._r.schedule_pending_drain(session)

    async def _end_acp_session(self, session: ActiveSession) -> None:
        """Clean up ACP state when the session ends."""
        if session.acp_executor is not None:
            await session.acp_executor.stop_process()
        session.acp_executor = None
        session._acp_stream_task = None
        if self._r._mcp_bridge is not None:
            self._r._mcp_bridge.tokens.revoke_session(session.id)

        session.clear_subprocess_tracking()

        if session.status == SessionStatus.PAUSED:
            session.complete()
            return

        session.buffer.push_text(
            MessageType.SESSION_END,
            {"session_id": session.id, "reason": "acp_finished"},
        )
        session.complete()
        self._r._fire_archive_task(session.id)
