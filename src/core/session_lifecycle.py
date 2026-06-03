"""Session lifecycle management methods for PromptRouter.

Extracted from prompt_router.py to reduce file size. These methods handle
creating, cancelling, ending, pausing, resuming, and restoring sessions,
as well as permission resolution, interactive responses, and the
inactivity reaper.

Composition collaborator — ``PromptRouter`` owns a :class:`SessionLifecycle`
instance (``self._lifecycle``) and delegates its public entry points to it.
Shared router state / sibling behaviour is reached through ``self._r``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from src.core.buffer import MessageType
from src.core.permissions import (
    PermissionDecision,
    PermissionScope,
)
from src.core.session import ActiveSession, ActivityState, SessionStatus, SessionType
from src.database.models import TaskSession as TaskSessionModel
from src.executors.codex import CodexExecutor
from src.executors.opencode import OpenCodeExecutor

if TYPE_CHECKING:
    from src.core.prompt_router import PromptRouter

logger = logging.getLogger(__name__)


def _parse_answer_text(text: str) -> dict[str, str]:
    """Parse a newline-joined ``"question: answer"`` string into a dict.

    Fallback for AskUserQuestion answers that arrive only as flattened text
    (the structured ``answers`` map is preferred when available).
    """
    answers: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        key, sep, value = line.partition(": ")
        if sep:
            answers[key.strip()] = value.strip()
        else:
            answers[line] = line
    return answers


class SessionLifecycle:
    """Session lifecycle collaborator for PromptRouter."""

    def __init__(self, router: PromptRouter) -> None:
        self._r = router

    async def _drop_pending_on_session_end(self, session: ActiveSession, *, reason: str) -> None:
        """Drop any queued user messages when the session reaches a terminal state."""
        store = getattr(self, "_pending_store", None)
        if store is None or not session.pending_user_messages:
            return
        try:
            await store.clear_session(session, reason=reason)
        except Exception:
            logger.exception("Failed to clear pending messages for session %s", session.id)

    async def _drop_wakes_on_session_end(self, session: ActiveSession, *, reason: str) -> None:
        """Cancel any pending ``ScheduleWakeup`` callbacks on session end."""
        store = getattr(self, "_wakeup_store", None)
        scheduler = getattr(self, "_wakeup_scheduler", None)
        if store is None or not session.scheduled_wakes:
            return
        wake_ids = [w.wake_id for w in session.scheduled_wakes]
        try:
            await store.cancel_all_for_session(session, reason=reason)
        except Exception:
            logger.exception("Failed to clear scheduled wakes for session %s", session.id)
        if scheduler is not None:
            scheduler.cancel_all_for_session(session.id, wake_ids)

    @property
    def is_direct_tool_mode(self) -> bool:
        """Whether the router is in direct tool mode (no LLM)."""
        return self._r._llm is None

    async def cancel_pending_tasks(self) -> None:
        """Cancel and await all pending background tasks.

        Should be called during shutdown before the DB engine is disposed.

        Title and metadata-persist tasks are given a grace period to finish
        naturally so that session titles reach the DB before
        ``save_all_sessions`` runs.  Persist tasks are spawned by completing
        title tasks, so they are awaited *after* the title queue drains.
        All other background tasks are cancelled immediately.
        """
        # --- 1. Let title tasks finish (short LLM calls; they spawn persist tasks) ---
        title_tasks = set(self._r._pending_title_tasks)
        if title_tasks:
            logger.info("Awaiting %d pending title task(s) before shutdown", len(title_tasks))
            _done, pending = await asyncio.wait(title_tasks, timeout=10)
            if pending:
                logger.warning("Cancelling %d title task(s) that did not finish in time", len(pending))
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        # --- 2. Let metadata-persist tasks finish (short DB writes) ---
        persist_tasks = set(self._r._pending_persist_tasks)
        if persist_tasks:
            logger.info("Awaiting %d pending persist task(s) before shutdown", len(persist_tasks))
            _done, pending = await asyncio.wait(persist_tasks, timeout=10)
            if pending:
                logger.warning("Cancelling %d persist task(s) that did not finish in time", len(pending))
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

        # --- 3. Cancel everything else ---
        all_pending: set[asyncio.Task[None]] = set()
        for task_set in (
            self._r._pending_log_tasks,
            self._r._pending_archive_tasks,
            self._r._pending_summary_tasks,
            self._r._pending_task_creation_tasks,
            self._r._pending_task_update_tasks,
            self._r._pending_plan_finalization_tasks,
        ):
            all_pending.update(task_set)

        if not all_pending:
            return

        logger.info("Cancelling %d pending background tasks", len(all_pending))
        for task in all_pending:
            task.cancel()
        await asyncio.gather(*all_pending, return_exceptions=True)

    def ensure_session(self, session_id: str | None = None) -> str:
        """Get an existing session or create a new one. Returns the session ID."""
        session: ActiveSession | None = None
        if session_id:
            session = self._r._session_manager.get_session(session_id)
            if session is None:
                logger.warning("Session %s not found, will create new session", session_id)
            elif session.status in (
                SessionStatus.COMPLETED,
                SessionStatus.FAILED,
                SessionStatus.CANCELLED,
            ):
                logger.warning(
                    "Session %s is in terminal state %s, will create new session",
                    session_id,
                    session.status.value,
                )
                session = None
        if session is None:
            session = self._r._session_manager.create_session(SessionType.CONVERSATIONAL)
        return session.id

    async def cancel_session(self, session_id: str) -> ActiveSession:
        """Cancel a running session, killing any active subprocess.

        Returns the cancelled session.

        Raises:
            ValueError: If the session does not exist.
            RuntimeError: If the session is already in a terminal state.
        """
        session = self._r._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        terminal_states = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
        if session.status in terminal_states:
            raise RuntimeError(f"Session already in terminal state: {session.status.value}")

        # Kill Claude Code subprocess if running
        had_claude_code = session.claude_code_executor is not None
        if session.claude_code_executor is not None:
            await session.claude_code_executor.cancel()
            session.claude_code_executor = None

        # Cancel the background stream task if running
        if session._claude_code_stream_task is not None and not session._claude_code_stream_task.done():
            session._claude_code_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._claude_code_stream_task
        session._claude_code_stream_task = None

        # Kill Codex subprocess if running
        had_codex = session.codex_executor is not None
        if session.codex_executor is not None:
            await session.codex_executor.cancel()
            session.codex_executor = None

        if session._codex_stream_task is not None and not session._codex_stream_task.done():
            session._codex_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._codex_stream_task
        session._codex_stream_task = None

        # Kill OpenCode subprocess if running
        had_opencode = session.opencode_executor is not None
        if session.opencode_executor is not None:
            await session.opencode_executor.cancel()
            session.opencode_executor = None

        if session._opencode_stream_task is not None and not session._opencode_stream_task.done():
            session._opencode_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._opencode_stream_task
        session._opencode_stream_task = None

        # Auto-deny any pending permission requests
        if session.permission_manager is not None:
            session.permission_manager.cancel_all_pending()

        # Auto-deny any pending plan mode approval gate
        if session._plan_mode_event is not None and not session._plan_mode_event.is_set():
            session._plan_mode_approved = False
            session._plan_mode_event.set()

        # Auto-deny any pending plan review gate
        if session._plan_review_event is not None and not session._plan_review_event.is_set():
            session._plan_review_approved = False
            session._plan_review_feedback = None
            session._plan_review_event.set()

        # Release any pending AskUserQuestion gate so the callback unblocks
        # (with no answer) instead of timing out an hour later.
        if session._question_event is not None and not session._question_event.is_set():
            session._question_answers = None
            session._question_tool_use_id = None
            session._question_event.set()

        # Clear subprocess tracking fields and broadcast null status
        session.clear_subprocess_tracking()

        # Close any open agent group before ending the session
        if had_claude_code or had_codex or had_opencode:
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )

        # Push SESSION_END before cancel() closes the buffer
        session.buffer.push_text(
            MessageType.SESSION_END,
            {
                "session_id": session.id,
                "reason": "cancelled",
            },
        )

        # Update attached task statuses if not already triggered by a tool result
        self._r._fire_task_update_on_session_end(session)
        self._r._fire_plan_finalization_task(session)

        session.cancel()
        await self._drop_pending_on_session_end(session, reason="session_ended")
        await self._drop_wakes_on_session_end(session, reason="session_ended")
        self._r._fire_archive_task(session_id)
        logger.info("Cancelled session %s", session_id)
        return session

    async def end_session(self, session_id: str) -> ActiveSession:
        """Gracefully end a session (user-confirmed completion).

        Returns the ended session.  If the session was already completed
        (e.g. by a background agent task finishing), returns it as-is so
        the caller can still treat the operation as successful.

        Raises:
            ValueError: If the session does not exist.
            RuntimeError: If the session is in a non-completable terminal state
                          (failed or cancelled).
        """
        session = self._r._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        # If the session was already completed (e.g. by _end_claude_code_session
        # racing with a user-initiated end), treat it as a successful no-op so
        # the client can update its state consistently.
        if session.status == SessionStatus.COMPLETED:
            logger.info("Session %s already completed, returning as-is", session_id)
            return session

        terminal_states = (SessionStatus.FAILED, SessionStatus.CANCELLED)
        if session.status in terminal_states:
            raise RuntimeError(f"Session already in terminal state: {session.status.value}")

        # Kill Claude Code subprocess if running
        had_claude_code = session.claude_code_executor is not None
        if session.claude_code_executor is not None:
            await session.claude_code_executor.cancel()
            session.claude_code_executor = None

        # Cancel the background stream task if running
        if session._claude_code_stream_task is not None and not session._claude_code_stream_task.done():
            session._claude_code_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._claude_code_stream_task
        session._claude_code_stream_task = None

        # Kill Codex subprocess if running
        had_codex = session.codex_executor is not None
        if session.codex_executor is not None:
            await session.codex_executor.cancel()
            session.codex_executor = None

        if session._codex_stream_task is not None and not session._codex_stream_task.done():
            session._codex_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._codex_stream_task
        session._codex_stream_task = None

        # Kill OpenCode subprocess if running
        had_opencode = session.opencode_executor is not None
        if session.opencode_executor is not None:
            await session.opencode_executor.cancel()
            session.opencode_executor = None

        if session._opencode_stream_task is not None and not session._opencode_stream_task.done():
            session._opencode_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._opencode_stream_task
        session._opencode_stream_task = None

        # Clear subprocess tracking fields and broadcast null status
        session.clear_subprocess_tracking()

        # Close any open agent group before ending the session
        if had_claude_code or had_codex or had_opencode:
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )

        session.buffer.push_text(
            MessageType.SESSION_END,
            {
                "session_id": session.id,
                "reason": "user_ended",
            },
        )

        # Clear paused state so complete() proceeds (explicit user action)
        if session.status == SessionStatus.PAUSED:
            session.status = SessionStatus.ACTIVE
            session.paused_at = None

        # Update attached task statuses if not already triggered by a tool result
        self._r._fire_task_update_on_session_end(session)
        self._r._fire_plan_finalization_task(session)

        session.complete()
        await self._drop_pending_on_session_end(session, reason="session_ended")
        await self._drop_wakes_on_session_end(session, reason="session_ended")
        self._r._fire_archive_task(session_id)
        logger.info("Ended session %s (user confirmed)", session_id)
        return session

    def resolve_permission(
        self,
        session_id: str,
        request_id: str,
        decision: str,
        scope: str,
        path_prefix: str | None = None,
    ) -> None:
        """Resolve a pending permission request from the client.

        Raises:
            ValueError: If the session does not exist.
            RuntimeError: If the session does not have interactive permissions
                enabled, or the request_id is unknown.
        """
        session = self._r._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        if session.permission_manager is None:
            raise RuntimeError("Session does not have interactive permissions enabled")

        resolved = session.permission_manager.resolve_request(
            request_id=request_id,
            decision=PermissionDecision(decision),
            scope=PermissionScope(scope),
            path_prefix=path_prefix,
        )
        if not resolved:
            raise RuntimeError(f"Unknown or already-resolved permission request: {request_id}")

        # Persist the decision in the buffer so that replaying the history
        # (e.g. when re-opening the session in a new pane) shows the resolved
        # state instead of re-presenting the pending widget.
        accepted = PermissionDecision(decision) == PermissionDecision.ALLOW
        for msg in session.buffer.text_history:
            if msg.message_type == MessageType.PERMISSION_REQUEST and msg.data.get("request_id") == request_id:
                msg.data["accepted"] = accepted
                break

    async def send_interactive_response(
        self,
        session_id: str,
        text: str,
        *,
        accepted: bool = True,
        answers: dict[str, str] | None = None,
    ) -> None:
        """Send an interactive response directly to Claude Code's stdin.

        Used for answering AskUserQuestion prompts, plan mode approval, plan
        review approval/feedback, and other mid-turn interactions.  Unlike
        :meth:`handle_prompt`, this does **not** open a new agent group or
        create a new reading task — the original streaming task picks up the
        follow-on events.

        Falls back to :meth:`handle_prompt` when there is no active Claude
        Code process (e.g. the process exited while the user was answering).

        Args:
            session_id: The session to respond to.
            text: The response text to deliver.
            accepted: For plan review responses — ``True`` means the user
                approved the plan; ``False`` means the user provided feedback
                for the plan to be revised.  Ignored for non-plan-review
                interactions.

        Raises:
            ValueError: If the session does not exist.
        """
        session = self._r._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        # If the session is blocked awaiting plan mode approval, resolve that first
        # without touching Claude Code's stdin (EnterPlanMode is an internal tool
        # that Claude Code auto-handles; the gate is on the RCFlow relay side).
        if session._plan_mode_event is not None and not session._plan_mode_event.is_set():
            approved = text.strip().lower() not in ("no", "n", "deny")
            session._plan_mode_approved = approved
            # Persist the decision in the buffer so history replay shows resolved state.
            for msg in reversed(session.buffer.text_history):
                if msg.message_type == MessageType.PLAN_MODE_ASK and "accepted" not in msg.data:
                    msg.data["accepted"] = approved
                    break
            session._plan_mode_event.set()
            return

        # If the session is blocked awaiting plan review approval, resolve that gate.
        # The relay will forward the response text to Claude Code's stdin.
        if session._plan_review_event is not None and not session._plan_review_event.is_set():
            session._plan_review_approved = accepted
            session._plan_review_feedback = text
            # Persist the decision in the buffer so history replay shows resolved state.
            for msg in reversed(session.buffer.text_history):
                if msg.message_type == MessageType.PLAN_REVIEW_ASK and "accepted" not in msg.data:
                    msg.data["accepted"] = accepted
                    break
            session._plan_review_event.set()
            return

        # If the session is blocked awaiting an AskUserQuestion answer, resolve
        # that gate.  The ``can_use_tool`` callback reads ``_question_answers`` and
        # returns them to Claude Code as the tool's answer (so the model continues
        # in the same turn) and annotates the buffered TOOL_START for replay.
        if session._question_event is not None and not session._question_event.is_set():
            # Prefer the structured answers map; fall back to parsing the flat
            # "question: answer" text the WS handler builds.
            session._question_answers = answers or _parse_answer_text(text)
            session._question_event.set()
            return

        executor = session.claude_code_executor
        if executor is not None and executor.is_running and session.claude_code_relay_active:
            # Mid-turn: a relay task is actively draining the stream, so the
            # injected text reaches the model and its response streams back
            # through that relay.
            await executor.send_input(text)
            return

        # No active relay (process gone, or connected-but-idle between turns):
        # deliver as a fresh turn so a relay is spawned to stream the
        # continuation.  A bare send_input here would land in the SDK message
        # queue and be mis-consumed by the next turn.
        await self._r.handle_prompt(text, session_id)

    async def pause_session(self, session_id: str) -> ActiveSession:
        """Pause an active session.

        If a Claude Code or Codex subprocess is running, it is killed and the
        background stream task is cancelled.  New prompts are rejected
        until the session is resumed.

        Returns the paused session.

        Raises:
            ValueError: If the session does not exist.
            RuntimeError: If the session cannot be paused (terminal or already paused).
        """
        session = self._r._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        # Mark the session as PAUSED *before* the first await so that any
        # racing stream task that observes the SIGKILL-induced EOF (during
        # ``await executor.cancel()`` → ``await proc.wait()``) sees
        # status=PAUSED and takes the clean PAUSED branch.  Without this,
        # the task would fall through to the "unexpected exit" path, push a
        # spurious "exit code -9" ERROR, and emit an extra AGENT_GROUP_END,
        # leaving the client in an unrecoverable loading state after resume.
        session.pause()  # raises RuntimeError if already paused / terminal

        # Kill Claude Code subprocess if running
        had_claude_code = session.claude_code_executor is not None
        if session.claude_code_executor is not None:
            await session.claude_code_executor.cancel()
            session.claude_code_executor = None

        # Cancel the background stream task if running
        if session._claude_code_stream_task is not None and not session._claude_code_stream_task.done():
            session._claude_code_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._claude_code_stream_task
        session._claude_code_stream_task = None

        # Kill Codex subprocess if running
        had_codex = session.codex_executor is not None
        if session.codex_executor is not None:
            await session.codex_executor.cancel()
            session.codex_executor = None

        if session._codex_stream_task is not None and not session._codex_stream_task.done():
            session._codex_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._codex_stream_task
        session._codex_stream_task = None

        # Kill OpenCode subprocess if running
        had_opencode = session.opencode_executor is not None
        if session.opencode_executor is not None:
            await session.opencode_executor.cancel()
            session.opencode_executor = None

        if session._opencode_stream_task is not None and not session._opencode_stream_task.done():
            session._opencode_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._opencode_stream_task
        session._opencode_stream_task = None

        had_agent = had_claude_code or had_codex or had_opencode

        # Close out any live Monitor watches so they do not appear as still ticking
        # in the UI while the session sits paused.
        await self._r._terminate_active_monitors(session, reason="session_end")

        # Auto-deny any pending permission requests
        if session.permission_manager is not None:
            session.permission_manager.cancel_all_pending()

        # Auto-deny any pending plan mode approval gate
        if session._plan_mode_event is not None and not session._plan_mode_event.is_set():
            session._plan_mode_approved = False
            session._plan_mode_event.set()

        # Auto-deny any pending plan review gate
        if session._plan_review_event is not None and not session._plan_review_event.is_set():
            session._plan_review_approved = False
            session._plan_review_feedback = None
            session._plan_review_event.set()

        # Release any pending AskUserQuestion gate so the callback unblocks
        # (with no answer) instead of timing out an hour later.
        if session._question_event is not None and not session._question_event.is_set():
            session._question_answers = None
            session._question_tool_use_id = None
            session._question_event.set()

        # Clear subprocess tracking fields and broadcast null status
        session.clear_subprocess_tracking()

        # Close any open agent group
        if had_agent:
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )

        session.buffer.push_text(
            MessageType.SESSION_PAUSED,
            {
                "session_id": session.id,
                "paused_at": session.paused_at.isoformat() if session.paused_at else None,
                "claude_code_interrupted": had_agent,
            },
        )

        logger.info("Paused session %s (agent_interrupted=%s)", session_id, had_agent)
        return session

    async def interrupt_subprocess(self, session_id: str) -> ActiveSession:
        """Kill any running subprocess without pausing the session.

        Unlike :meth:`pause_session`, the session remains ``ACTIVE`` after
        this call and is immediately ready to accept new prompts.  The
        subprocess is killed and its background stream task is cancelled,
        but no ``SESSION_PAUSED`` message is emitted.

        A null ``subprocess_status`` message is broadcast so the client can
        clear its subprocess indicator.

        Returns the session after interruption.

        Raises:
            ValueError: If the session does not exist.
            RuntimeError: If the session is in a terminal or paused state.
        """
        session = self._r._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        terminal_states = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
        if session.status in terminal_states:
            raise RuntimeError(f"Session is in terminal state: {session.status.value}")
        if session.status == SessionStatus.PAUSED:
            raise RuntimeError("Cannot interrupt subprocess of a paused session")

        had_claude_code = session.claude_code_executor is not None
        if session.claude_code_executor is not None:
            await session.claude_code_executor.cancel()
            session.claude_code_executor = None

        if session._claude_code_stream_task is not None and not session._claude_code_stream_task.done():
            session._claude_code_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._claude_code_stream_task
        session._claude_code_stream_task = None

        had_codex = session.codex_executor is not None
        if session.codex_executor is not None:
            await session.codex_executor.cancel()
            session.codex_executor = None

        if session._codex_stream_task is not None and not session._codex_stream_task.done():
            session._codex_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._codex_stream_task
        session._codex_stream_task = None

        had_opencode = session.opencode_executor is not None
        if session.opencode_executor is not None:
            await session.opencode_executor.cancel()
            session.opencode_executor = None

        if session._opencode_stream_task is not None and not session._opencode_stream_task.done():
            session._opencode_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._opencode_stream_task
        session._opencode_stream_task = None

        # Close out any live Monitor watches before broadcasting interrupt.
        await self._r._terminate_active_monitors(session, reason="cancelled")

        # Close any open agent group
        if had_claude_code or had_codex or had_opencode:
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            session.buffer.push_text(
                MessageType.TEXT_CHUNK,
                {"session_id": session.id, "content": "[Subprocess interrupted by user]\n"},
            )

        # Clear subprocess tracking fields and broadcast null status
        session.clear_subprocess_tracking()

        session.set_activity(ActivityState.IDLE)

        logger.info(
            "Interrupted subprocess for session %s (claude_code=%s, codex=%s, opencode=%s)",
            session_id,
            had_claude_code,
            had_codex,
            had_opencode,
        )
        return session

    async def resume_session(self, session_id: str) -> ActiveSession:
        """Resume a paused session.

        The client can subscribe to the session's output channel to receive
        all buffered messages produced while paused, then send new prompts.

        If the session had a Claude Code or Codex executor before it was
        paused (executor was torn down by :meth:`pause_session` but the
        session metadata still holds the session/thread IDs), this method
        reconstructs a ready-to-restart executor so that the next call to
        :meth:`~src.core.prompt_router.PromptRouter.handle_prompt` correctly
        routes to the agent rather than the outer LLM.

        Returns the resumed session.

        Raises:
            ValueError: If the session does not exist.
            RuntimeError: If the session is not paused.
        """
        session = self._r._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        # Idempotent: multiple concurrent sends to a paused session each
        # fire a resume task — the second arrives after the first already
        # flipped status back to ACTIVE.  Skip cleanly without re-firing
        # the drain (the first call is already draining, and a second
        # ``schedule_pending_drain`` would race ``_drain_one`` against
        # itself and risk double-delivering the head message).
        if session.status != SessionStatus.PAUSED:
            return session

        session.resume()

        session.buffer.push_text(
            MessageType.SESSION_RESUMED,
            {"session_id": session.id},
        )

        # If the underlying work completed while paused, finalize now.
        if session.metadata.pop("completed_while_paused", False):
            if session.codex_executor is not None:
                await self._r._end_codex_session(session)
            elif session.opencode_executor is not None:
                await self._r._end_opencode_session(session)
            else:
                await self._r._end_claude_code_session(session)
            logger.info("Resumed session %s", session_id)
            return session

        # Reconstruct the Claude Code executor if this session had one before
        # it was paused.  pause_session() kills the subprocess and sets
        # claude_code_executor = None, but preserves the metadata keys that
        # identify the prior Claude Code session.  Without reconstruction the
        # next handle_prompt() call would see executor=None and route the
        # message to the outer LLM instead of Claude Code.
        if session.claude_code_executor is None:
            cc_session_id = session.metadata.get("claude_code_session_id")
            cc_tool_name = session.metadata.get("claude_code_tool_name")
            if cc_session_id and cc_tool_name:
                tool_def = self._r._tool_registry.get(cc_tool_name)
                if tool_def is not None and tool_def.executor == "claude_code":
                    session.claude_code_executor = self._r._claude.build_session_executor(
                        tool_def, session, cc_session_id
                    )

        # Reconstruct the Codex executor if this session had one before pause.
        if session.codex_executor is None:
            codex_thread_id = session.metadata.get("codex_thread_id")
            codex_tool_name = session.metadata.get("codex_tool_name")
            if codex_thread_id and codex_tool_name:
                tool_def = self._r._tool_registry.get(codex_tool_name)
                if tool_def is not None and tool_def.executor == "codex":
                    binary_path = "codex"
                    config = tool_def.get_codex_config()
                    binary_path = config.binary_path
                    if self._r._tool_manager:
                        resolved = self._r._tool_manager.get_binary_path("codex")
                        if resolved:
                            binary_path = resolved
                    codex_executor = CodexExecutor(
                        binary_path=binary_path,
                        thread_id=codex_thread_id,
                        extra_env=self._r._build_codex_extra_env(),
                        config_overrides=self._r._get_managed_config_overrides("codex"),
                    )
                    codex_executor._tool_def = tool_def
                    codex_params = session.metadata.get("codex_parameters", {})
                    codex_executor._last_parameters = codex_params
                    session.codex_executor = codex_executor

        # Reconstruct the OpenCode executor if this session had one before pause.
        if session.opencode_executor is None:
            oc_session_id = session.metadata.get("opencode_session_id")
            oc_tool_name = session.metadata.get("opencode_tool_name")
            if oc_session_id and oc_tool_name:
                tool_def = self._r._tool_registry.get(oc_tool_name)
                if tool_def is not None and tool_def.executor == "opencode":
                    binary_path = "opencode"
                    config = tool_def.get_opencode_config()
                    binary_path = config.binary_path
                    if self._r._tool_manager:
                        resolved = self._r._tool_manager.get_binary_path("opencode")
                        if resolved:
                            binary_path = resolved
                    oc_executor = OpenCodeExecutor(
                        binary_path=binary_path,
                        session_id=oc_session_id,
                        extra_env=self._r._build_opencode_extra_env(),
                        config_overrides=self._r._get_managed_config_overrides("opencode"),
                    )
                    oc_executor._tool_def = tool_def
                    oc_params = session.metadata.get("opencode_parameters", {})
                    oc_executor._last_parameters = oc_params
                    session.opencode_executor = oc_executor

        logger.info("Resumed session %s", session_id)
        # Drain any messages that piled up while the session was paused.
        # Without this, messages enqueued during pause would sit
        # untouched until the user sent a *new* message after resume.
        if session.pending_user_messages:
            self._r.schedule_pending_drain(session)
        return session

    async def restore_session(self, session_id: str) -> ActiveSession:
        """Restore an archived session from the database back to active state.

        Loads session metadata, conversation history, and buffer messages.
        For Claude Code sessions, prepares the executor state for lazy restart
        on the next user message.

        Returns the restored session.

        Raises:
            ValueError: If the session is not found in the DB.
            RuntimeError: If the session is already active or DB is unavailable.
        """
        if self._r._db_session_factory is None:
            raise RuntimeError("Database is not configured; cannot restore sessions")

        async with self._r._db_session_factory() as db:
            session = await self._r._session_manager.restore_session(session_id, db)

        # If this was a Claude Code session, reconstruct the executor (and any
        # saved permission rules) for lazy restart on the next user message.
        self._r._claude.reattach_executor(session)

        # If this was a Codex session, set up executor for lazy restart
        codex_thread_id = session.metadata.get("codex_thread_id")
        codex_tool_name = session.metadata.get("codex_tool_name")
        if codex_thread_id and codex_tool_name:
            tool_def = self._r._tool_registry.get(codex_tool_name)
            if tool_def is not None and tool_def.executor == "codex":
                binary_path = "codex"
                config = tool_def.get_codex_config()
                binary_path = config.binary_path
                if self._r._tool_manager:
                    resolved = self._r._tool_manager.get_binary_path("codex")
                    if resolved:
                        binary_path = resolved

                codex_executor = CodexExecutor(
                    binary_path=binary_path,
                    thread_id=codex_thread_id,
                    extra_env=self._r._build_codex_extra_env(),
                    config_overrides=self._r._get_managed_config_overrides("codex"),
                )
                codex_executor._tool_def = tool_def
                codex_params = session.metadata.get("codex_parameters", {})
                codex_executor._last_parameters = codex_params

                session.codex_executor = codex_executor
                session.session_type = SessionType.LONG_RUNNING

        # If this was an OpenCode session, set up executor for lazy restart
        oc_session_id = session.metadata.get("opencode_session_id")
        oc_tool_name = session.metadata.get("opencode_tool_name")
        if oc_session_id and oc_tool_name:
            tool_def = self._r._tool_registry.get(oc_tool_name)
            if tool_def is not None and tool_def.executor == "opencode":
                binary_path = "opencode"
                config = tool_def.get_opencode_config()
                binary_path = config.binary_path
                if self._r._tool_manager:
                    resolved = self._r._tool_manager.get_binary_path("opencode")
                    if resolved:
                        binary_path = resolved

                oc_executor = OpenCodeExecutor(
                    binary_path=binary_path,
                    session_id=oc_session_id,
                    extra_env=self._r._build_opencode_extra_env(),
                    config_overrides=self._r._get_managed_config_overrides("opencode"),
                )
                oc_executor._tool_def = tool_def
                oc_params = session.metadata.get("opencode_parameters", {})
                oc_executor._last_parameters = oc_params

                session.opencode_executor = oc_executor
                session.session_type = SessionType.LONG_RUNNING

        # Repopulate attached task IDs from task_sessions table
        if self._r._db_session_factory is not None:
            try:
                async with self._r._db_session_factory() as db:
                    stmt = select(TaskSessionModel.task_id).where(TaskSessionModel.session_id == uuid.UUID(session_id))
                    result = await db.execute(stmt)
                    task_ids = [str(row[0]) for row in result.all()]
                    if task_ids:
                        session.metadata["attached_task_ids"] = task_ids
                        logger.info(
                            "Restored %d attached task IDs for session %s",
                            len(task_ids),
                            session_id,
                        )
            except Exception:
                logger.exception("Failed to restore task IDs for session %s", session_id)

        session.buffer.push_text(
            MessageType.SESSION_RESTORED,
            {"session_id": session.id},
        )

        logger.info("Restored session %s via prompt router", session_id)
        return session

    def _check_token_limit_exceeded(self, session: ActiveSession) -> bool:
        """Check if the session has exceeded its token limits.

        If exceeded, pushes an error message to the buffer and returns True.
        """
        if self._r._settings is None:
            return False

        total_in = session.input_tokens + session.tool_input_tokens
        total_out = session.output_tokens + session.tool_output_tokens
        input_limit = self._r._settings.SESSION_INPUT_TOKEN_LIMIT
        output_limit = self._r._settings.SESSION_OUTPUT_TOKEN_LIMIT

        if input_limit > 0 and total_in >= input_limit:
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": f"Session input token limit reached ({total_in:,}/{input_limit:,}). "
                    "End this session and start a new one, or ask your admin to increase the limit.",
                    "code": "TOKEN_LIMIT_REACHED",
                },
            )
            return True

        if output_limit > 0 and total_out >= output_limit:
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": f"Session output token limit reached ({total_out:,}/{output_limit:,}). "
                    "End this session and start a new one, or ask your admin to increase the limit.",
                    "code": "TOKEN_LIMIT_REACHED",
                },
            )
            return True

        return False

    # ------------------------------------------------------------------
    # Inactivity reaper
    # ------------------------------------------------------------------

    _REAPER_CHECK_INTERVAL = 600  # seconds (10 minutes)

    async def run_inactivity_reaper(self) -> None:
        """Periodically end sessions that have been inactive for too long.

        The timeout is read from ``Settings.SESSION_INACTIVITY_TIMEOUT_MINUTES``
        on every tick so the reaper picks up live config changes
        (``PATCH /api/config``) without a worker restart.  When the setting is
        ``0`` (the default) reaping is disabled entirely.
        """
        try:
            while True:
                await asyncio.sleep(self._REAPER_CHECK_INTERVAL)
                await self._reap_inactive_sessions()
        except asyncio.CancelledError:
            pass

    async def _reap_inactive_sessions(self) -> None:
        """End all active sessions that exceed the inactivity timeout.

        No-op when ``Settings.SESSION_INACTIVITY_TIMEOUT_MINUTES`` is ``0``
        or unset.
        """
        minutes = 0
        if self._r._settings is not None:
            minutes = self._r._settings.SESSION_INACTIVITY_TIMEOUT_MINUTES
        if minutes <= 0:
            return  # Reaper disabled.
        timeout = timedelta(minutes=minutes)
        now = datetime.now(UTC)
        terminal = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
        for session in self._r._session_manager.list_all_sessions():
            if session.status in terminal:
                continue
            if session.status == SessionStatus.PAUSED:
                continue  # Never reap paused sessions
            if now - session.last_activity_at > timeout:
                idle_minutes = int((now - session.last_activity_at).total_seconds() / 60)
                logger.info(
                    "Auto-ending inactive session %s (idle %d min, limit %d min)",
                    session.id,
                    idle_minutes,
                    minutes,
                )
                with contextlib.suppress(ValueError, RuntimeError):
                    await self.end_session(session.id)
