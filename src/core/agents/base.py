"""Shared base class, constants, and helpers for the managed-agent paths."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.core.buffer import MessageType
from src.core.session import ActivityState, SessionStatus

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Coroutine

    from src.core.prompt_router import PromptRouter
    from src.core.session import ActiveSession
    from src.executors.base import ExecutionChunk

logger = logging.getLogger(__name__)

MAX_TOOL_OUTPUT_CHARS = 100_000


def truncate_tool_output(content: str) -> str:
    """Truncate tool output that exceeds the size limit for client delivery.

    Used by all three managed agents (Claude Code, Codex, OpenCode) so a
    single agent turn cannot flood the WebSocket buffer with megabytes of
    grep / find / build output.
    """
    if len(content) > MAX_TOOL_OUTPUT_CHARS:
        return content[:MAX_TOOL_OUTPUT_CHARS] + f"\n\n... (truncated, {len(content):,} total chars)"
    return content


class ManagedAgentBase:
    """Shared behaviour for the per-agent PromptRouter collaborators.

    Each concrete agent (Claude Code, Codex, OpenCode, ACP) is a collaborator
    that holds a back-reference to the ``PromptRouter`` (``self._r``) and drives
    one executor type. This base owns behaviour that was hand-copied across all
    four modules, so a fix lands once instead of drifting per-agent.
    """

    def __init__(self, router: PromptRouter) -> None:
        self._r = router

    def _push_subprocess_status(self, session: ActiveSession, current_tool: str | None) -> None:
        """Update the session's current-tool field and broadcast subprocess status.

        A no-op broadcast when the subprocess hasn't started yet (nothing to
        show). Emitted as an ephemeral message so it never lands in history.
        """
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

    async def _end_agent_session(
        self,
        session: ActiveSession,
        *,
        executor_attr: str,
        task_attr: str,
        reason: str,
    ) -> None:
        """Run the standard end-of-session teardown for a one-executor subprocess agent.

        Stop + drop the executor and its stream task, revoke MCP tokens, clear
        subprocess tracking, then either complete a paused session silently or
        push SESSION_END + complete + fire the archive. Shared by Codex /
        OpenCode / ACP (Claude Code differs — it also terminates live monitors).
        """
        executor = getattr(session, executor_attr)
        if executor is not None:
            await executor.stop_process()
        setattr(session, executor_attr, None)
        setattr(session, task_attr, None)
        if self._r._mcp_bridge is not None:
            self._r._mcp_bridge.tokens.revoke_session(session.id)
        session.clear_subprocess_tracking()
        if session.status == SessionStatus.PAUSED:
            session.complete()
            return
        session.buffer.push_text(MessageType.SESSION_END, {"session_id": session.id, "reason": reason})
        session.complete()
        self._r._fire_archive_task(session.id)

    async def _forward_to_oneshot_agent(
        self,
        session: ActiveSession,
        text: str,
        *,
        tool_key: str,
        executor_attr: str,
        task_attr: str,
        subprocess_type: str,
        default_display_name: str,
        working_dir_meta_key: str,
        restart: Callable[[ActiveSession, Any, str], Coroutine[Any, Any, None]],
    ) -> None:
        """Forward a follow-up message to a one-shot CLI agent (Codex / OpenCode).

        These CLIs use one process per turn, so a follow-up re-broadcasts the
        subprocess status, opens a fresh agent group, and spawns a new resume
        process via *restart*. Shared by Codex and OpenCode.
        """
        executor = getattr(session, executor_attr)
        if executor is None or session.status == SessionStatus.PAUSED:
            return

        session.set_activity(ActivityState.RUNNING_SUBPROCESS)

        tool_def = self._r._tool_registry.get(tool_key)
        display_name = tool_def.display_name if tool_def and tool_def.display_name else default_display_name

        # Re-broadcast subprocess status so the client shows the indicator again.
        if session.subprocess_started_at is None:
            session.subprocess_started_at = datetime.now(UTC)
            session.subprocess_type = subprocess_type
            session.subprocess_display_name = display_name
            session.subprocess_working_directory = session.metadata.get(working_dir_meta_key, "")
        self._push_subprocess_status(session, None)

        session.buffer.push_text(
            MessageType.AGENT_GROUP_START,
            {"session_id": session.id, "tool_name": tool_key, "display_name": display_name},
        )

        setattr(session, task_attr, asyncio.create_task(restart(session, executor, text)))

    async def _restart_oneshot_agent(
        self,
        session: ActiveSession,
        executor: Any,
        prompt: str,
        *,
        display_name: str,
        error_code: str,
        relay: Callable[[ActiveSession, AsyncGenerator[ExecutionChunk, None]], Coroutine[Any, Any, bool]],
        end: Callable[[ActiveSession], Coroutine[Any, Any, None]],
    ) -> None:
        """Spawn a resume process, stream a follow-up turn, and settle the session.

        Shared by Codex and OpenCode. *relay* returns whether the turn completed
        successfully; on failure or an incomplete stream the session is ended via
        *end* so the user is never left with a stuck subprocess.
        """
        try:
            completed = await relay(session, executor.restart_with_prompt(prompt))
        except Exception as e:
            logger.exception("%s restart error in session %s", display_name, session.id)
            session.buffer.push_text(MessageType.AGENT_GROUP_END, {"session_id": session.id})
            session.buffer.push_text(
                MessageType.ERROR,
                {"session_id": session.id, "content": f"{display_name} error: {e}", "code": error_code},
            )
            await end(session)
            return

        session.buffer.push_text(MessageType.AGENT_GROUP_END, {"session_id": session.id})

        if not completed:
            logger.info(
                "%s follow-up stream ended without completion (session=%s), ending session", display_name, session.id
            )
            await end(session)
            return

        await executor.stop_process()
        self._r.schedule_pending_drain(session)
