"""Session lifecycle management methods for PromptRouter.

Extracted from prompt_router.py to reduce file size. These methods handle
creating, cancelling, ending, pausing, resuming, and restoring sessions,
as well as permission resolution, interactive responses, and the
inactivity reaper.

Used as a mixin class — ``PromptRouter`` inherits from
``SessionLifecycleMixin`` to gain these methods.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from src.core.buffer import MessageType
from src.core.permissions import (
    PermissionDecision,
    PermissionManager,
    PermissionScope,
)
from src.core.session import ActivityState, ActiveSession, SessionStatus, SessionType
from src.executors.claude_code import ClaudeCodeExecutor
from src.executors.codex import CodexExecutor
from src.models.db import TaskSession as TaskSessionModel

logger = logging.getLogger(__name__)


class SessionLifecycleMixin:
    """Mixin providing session lifecycle methods for PromptRouter."""

    @property
    def is_direct_tool_mode(self) -> bool:
        """Whether the router is in direct tool mode (no LLM)."""
        return self._llm is None

    async def cancel_pending_tasks(self) -> None:
        """Cancel and await all pending background tasks.

        Should be called during shutdown before the DB engine is disposed.
        """
        all_pending: set[asyncio.Task[None]] = set()
        for task_set in (
            self._pending_log_tasks,
            self._pending_summary_tasks,
            self._pending_title_tasks,
            self._pending_archive_tasks,
            self._pending_task_creation_tasks,
            self._pending_task_update_tasks,
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
            session = self._session_manager.get_session(session_id)
            if session is None:
                logger.warning("Session %s not found, will create new session", session_id)
            elif session.status in (
                SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED,
            ):
                logger.warning(
                    "Session %s is in terminal state %s, will create new session",
                    session_id, session.status.value,
                )
                session = None
        if session is None:
            session = self._session_manager.create_session(SessionType.CONVERSATIONAL)
        return session.id

    async def cancel_session(self, session_id: str) -> ActiveSession:
        """Cancel a running session, killing any active subprocess.

        Returns the cancelled session.

        Raises:
            ValueError: If the session does not exist.
            RuntimeError: If the session is already in a terminal state.
        """
        session = self._session_manager.get_session(session_id)
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

        # Clear subprocess tracking fields
        session.subprocess_started_at = None
        session.subprocess_current_tool = None

        # Close any open agent group before ending the session
        if had_claude_code or had_codex:
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
        self._fire_task_update_on_session_end(session)

        session.cancel()
        self._fire_archive_task(session_id)
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
        session = self._session_manager.get_session(session_id)
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

        self._resolve_session_end_ask(session, accepted=True)

        # Close any open agent group before ending the session
        if had_claude_code or had_codex:
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
        self._fire_task_update_on_session_end(session)

        session.complete()
        self._fire_archive_task(session_id)
        logger.info("Ended session %s (user confirmed)", session_id)
        return session

    def dismiss_session_end_ask(self, session_id: str) -> None:
        """Mark the last unresolved SESSION_END_ASK as declined (user clicked Continue).

        This persists the dismissal in the buffer so that replaying the history
        does not show the widget as pending again.

        Raises:
            ValueError: If the session does not exist.
        """
        session = self._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        self._resolve_session_end_ask(session, accepted=False)

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
        session = self._session_manager.get_session(session_id)
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
            if (
                msg.message_type == MessageType.PERMISSION_REQUEST
                and msg.data.get("request_id") == request_id
            ):
                msg.data["accepted"] = accepted
                break

    async def send_interactive_response(
        self, session_id: str, text: str, *, accepted: bool = True
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
        session = self._session_manager.get_session(session_id)
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

        executor = session.claude_code_executor
        if executor is not None and executor.is_running:
            # Mid-turn: just send to stdin; the original relay task handles the rest
            await executor.send_input(text)
            return

        # Fallback: process is gone — treat as a regular follow-up prompt
        await self.handle_prompt(text, session_id)

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
        session = self._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

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

        had_agent = had_claude_code or had_codex

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

        # Clear subprocess tracking fields
        session.subprocess_started_at = None
        session.subprocess_current_tool = None

        session.pause()

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
        session = self._session_manager.get_session(session_id)
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

        # Close any open agent group
        if had_claude_code or had_codex:
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            session.buffer.push_text(
                MessageType.TEXT_CHUNK,
                {"session_id": session.id, "content": "[Subprocess interrupted by user]\n"},
            )

        # Clear subprocess tracking fields
        session.subprocess_started_at = None
        session.subprocess_current_tool = None

        # Broadcast null subprocess_status so the client clears its indicator
        session.buffer.push_ephemeral(
            MessageType.SUBPROCESS_STATUS,
            {"session_id": session.id, "subprocess_type": None},
        )

        session.set_activity(ActivityState.IDLE)

        logger.info(
            "Interrupted subprocess for session %s (claude_code=%s, codex=%s)",
            session_id, had_claude_code, had_codex,
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
        session = self._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        session.resume()

        session.buffer.push_text(
            MessageType.SESSION_RESUMED,
            {"session_id": session.id},
        )

        # If the underlying work completed while paused, finalize now.
        if session.metadata.pop("completed_while_paused", False):
            if session.codex_executor is not None:
                await self._end_codex_session(session)
            else:
                await self._end_claude_code_session(session)
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
                tool_def = self._tool_registry.get(cc_tool_name)
                if tool_def is not None and tool_def.executor == "claude_code":
                    binary_path = "claude"
                    config = tool_def.get_claude_code_config()
                    binary_path = config.binary_path
                    if self._tool_manager:
                        resolved = self._tool_manager.get_binary_path("claude_code")
                        if resolved:
                            binary_path = resolved
                    executor = ClaudeCodeExecutor(
                        binary_path=binary_path,
                        session_id=cc_session_id,
                        extra_env=self._build_claude_code_extra_env(),
                        config_overrides=self._get_managed_config_overrides("claude_code"),
                    )
                    executor._tool_def = tool_def
                    cc_params = session.metadata.get("claude_code_parameters", {})
                    executor._last_parameters = cc_params
                    session.claude_code_executor = executor

        # Reconstruct the Codex executor if this session had one before pause.
        if session.codex_executor is None:
            codex_thread_id = session.metadata.get("codex_thread_id")
            codex_tool_name = session.metadata.get("codex_tool_name")
            if codex_thread_id and codex_tool_name:
                tool_def = self._tool_registry.get(codex_tool_name)
                if tool_def is not None and tool_def.executor == "codex":
                    binary_path = "codex"
                    config = tool_def.get_codex_config()
                    binary_path = config.binary_path
                    if self._tool_manager:
                        resolved = self._tool_manager.get_binary_path("codex")
                        if resolved:
                            binary_path = resolved
                    codex_executor = CodexExecutor(
                        binary_path=binary_path,
                        thread_id=codex_thread_id,
                        extra_env=self._build_codex_extra_env(),
                        config_overrides=self._get_managed_config_overrides("codex"),
                    )
                    codex_executor._tool_def = tool_def
                    codex_params = session.metadata.get("codex_parameters", {})
                    codex_executor._last_parameters = codex_params
                    session.codex_executor = codex_executor

        logger.info("Resumed session %s", session_id)
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
        if self._db_session_factory is None:
            raise RuntimeError("Database is not configured; cannot restore sessions")

        async with self._db_session_factory() as db:
            session = await self._session_manager.restore_session(session_id, db)

        # If this was a Claude Code session, set up executor for lazy restart
        cc_session_id = session.metadata.get("claude_code_session_id")
        cc_tool_name = session.metadata.get("claude_code_tool_name")
        if cc_session_id and cc_tool_name:
            tool_def = self._tool_registry.get(cc_tool_name)
            if tool_def is not None and tool_def.executor == "claude_code":
                binary_path = "claude"
                config = tool_def.get_claude_code_config()
                binary_path = config.binary_path
                if self._tool_manager:
                    resolved = self._tool_manager.get_binary_path("claude_code")
                    if resolved:
                        binary_path = resolved

                executor = ClaudeCodeExecutor(
                    binary_path=binary_path,
                    session_id=cc_session_id,
                    extra_env=self._build_claude_code_extra_env(),
                    config_overrides=self._get_managed_config_overrides("claude_code"),
                )
                # Pre-set the tool_def and last_parameters so restart_with_prompt works
                executor._tool_def = tool_def
                cc_params = session.metadata.get("claude_code_parameters", {})
                executor._last_parameters = cc_params

                session.claude_code_executor = executor
                session.session_type = SessionType.LONG_RUNNING

        # Restore permission rules if they were saved
        saved_rules = session.metadata.get("permission_rules")
        if saved_rules:
            pm = PermissionManager()
            pm.restore_rules(saved_rules)
            session.permission_manager = pm

        # If this was a Codex session, set up executor for lazy restart
        codex_thread_id = session.metadata.get("codex_thread_id")
        codex_tool_name = session.metadata.get("codex_tool_name")
        if codex_thread_id and codex_tool_name:
            tool_def = self._tool_registry.get(codex_tool_name)
            if tool_def is not None and tool_def.executor == "codex":
                binary_path = "codex"
                config = tool_def.get_codex_config()
                binary_path = config.binary_path
                if self._tool_manager:
                    resolved = self._tool_manager.get_binary_path("codex")
                    if resolved:
                        binary_path = resolved

                codex_executor = CodexExecutor(
                    binary_path=binary_path,
                    thread_id=codex_thread_id,
                    extra_env=self._build_codex_extra_env(),
                    config_overrides=self._get_managed_config_overrides("codex"),
                )
                codex_executor._tool_def = tool_def
                codex_params = session.metadata.get("codex_parameters", {})
                codex_executor._last_parameters = codex_params

                session.codex_executor = codex_executor
                session.session_type = SessionType.LONG_RUNNING

        # Repopulate attached task IDs from task_sessions table
        if self._db_session_factory is not None:
            try:
                async with self._db_session_factory() as db:
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

    # ------------------------------------------------------------------
    # Session end ask helpers
    # ------------------------------------------------------------------

    _SESSION_END_ASK_TAG = "[SessionEndAsk]"

    @staticmethod
    def _resolve_session_end_ask(session: ActiveSession, *, accepted: bool) -> None:
        """Mark the last unresolved SESSION_END_ASK in the buffer as accepted/declined."""
        for msg in reversed(session.buffer.text_history):
            if msg.message_type == MessageType.SESSION_END_ASK and "accepted" not in msg.data:
                msg.data["accepted"] = accepted
                return

    def _check_token_limit_exceeded(self, session: ActiveSession) -> bool:
        """Check if the session has exceeded its token limits.

        If exceeded, pushes an error message to the buffer and returns True.
        """
        if self._settings is None:
            return False

        total_in = session.input_tokens + session.tool_input_tokens
        total_out = session.output_tokens + session.tool_output_tokens
        input_limit = self._settings.SESSION_INPUT_TOKEN_LIMIT
        output_limit = self._settings.SESSION_OUTPUT_TOKEN_LIMIT

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

    @staticmethod
    def _contains_session_end_ask(assistant_message: dict[str, Any]) -> bool:
        """Check if an assistant message contains the [SessionEndAsk] tag."""
        tag = SessionLifecycleMixin._SESSION_END_ASK_TAG
        content = assistant_message.get("content")
        if isinstance(content, str):
            return tag in content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and tag in block.get("text", ""):
                    return True
        return False

    # ------------------------------------------------------------------
    # Inactivity reaper
    # ------------------------------------------------------------------

    _INACTIVITY_TIMEOUT = timedelta(hours=6)
    _REAPER_CHECK_INTERVAL = 600  # seconds (10 minutes)

    async def run_inactivity_reaper(self) -> None:
        """Periodically end sessions that have been inactive for too long."""
        try:
            while True:
                await asyncio.sleep(self._REAPER_CHECK_INTERVAL)
                await self._reap_inactive_sessions()
        except asyncio.CancelledError:
            pass

    async def _reap_inactive_sessions(self) -> None:
        """End all active sessions that exceed the inactivity timeout."""
        now = datetime.now(UTC)
        terminal = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
        for session in self._session_manager.list_all_sessions():
            if session.status in terminal:
                continue
            if session.status == SessionStatus.PAUSED:
                continue  # Never reap paused sessions
            if now - session.last_activity_at > self._INACTIVITY_TIMEOUT:
                logger.info(
                    "Auto-ending inactive session %s (last activity: %s)",
                    session.id,
                    session.last_activity_at.isoformat(),
                )
                with contextlib.suppress(ValueError, RuntimeError):
                    await self.end_session(session.id)
