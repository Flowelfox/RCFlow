"""Shared base class, constants, and helpers for the managed-agent paths."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.buffer import MessageType
from src.core.session import SessionStatus

if TYPE_CHECKING:
    from src.core.prompt_router import PromptRouter
    from src.core.session import ActiveSession

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
