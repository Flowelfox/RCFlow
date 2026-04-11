import asyncio
import logging
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.buffer import BufferedMessage, MessageType, SessionBuffer
from src.models.db import Artifact as ArtifactModel
from src.models.db import Session as SessionModel
from src.models.db import SessionMessage as SessionMessageModel
from src.models.db import TaskSession as TaskSessionModel
from src.models.db import ToolExecution as ToolExecutionModel

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.core.permissions import PermissionManager
    from src.executors.claude_code import ClaudeCodeExecutor
    from src.executors.codex import CodexExecutor
    from src.executors.opencode import OpenCodeExecutor

logger = logging.getLogger(__name__)

# Sentinel used to push sessions with no sort_order to the end of the list.
_SORT_ORDER_NULLS_LAST = 2**62


def session_sort_key(item: dict[str, Any]) -> tuple[int, float]:
    """Return a sort key for session dicts: sort_order ASC (nulls last), created_at DESC."""
    sort_order = item.get("sort_order")
    created_at = item.get("created_at")
    return (
        sort_order if sort_order is not None else _SORT_ORDER_NULLS_LAST,
        -(created_at.replace(tzinfo=None).timestamp() if created_at else 0),
    )


class SessionStatus(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    EXECUTING = "executing"
    PAUSED = "paused"
    INTERRUPTED = "interrupted"  # killed by a backend restart; can be resumed
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ActivityState(StrEnum):
    IDLE = "idle"
    PROCESSING_LLM = "processing_llm"
    EXECUTING_TOOL = "executing_tool"
    RUNNING_SUBPROCESS = "running_subprocess"
    AWAITING_PERMISSION = "awaiting_permission"


class SessionType(StrEnum):
    ONE_SHOT = "one-shot"
    CONVERSATIONAL = "conversational"
    LONG_RUNNING = "long-running"


class ActiveSession:
    """An in-memory active session with its buffer and conversation context."""

    def __init__(self, session_id: str, session_type: SessionType) -> None:
        self.id = session_id
        self.session_type = session_type
        self.status = SessionStatus.CREATED
        self._activity_state = ActivityState.IDLE
        self.created_at = datetime.now(UTC)
        self.ended_at: datetime | None = None
        self.last_activity_at: datetime = datetime.now(UTC)
        self.buffer = SessionBuffer(session_id)
        self.conversation_history: list[dict[str, Any]] = []
        self._title: str | None = None
        self._on_update: Callable[[], None] | None = None
        self.metadata: dict[str, Any] = {}
        self.paused_at: datetime | None = None
        # Claude Code mode: when set, subsequent messages bypass the outer LLM
        self.claude_code_executor: ClaudeCodeExecutor | None = None
        self._claude_code_stream_task: asyncio.Task[None] | None = None
        # Codex CLI mode: same pattern as Claude Code but with one-shot processes
        self.codex_executor: CodexExecutor | None = None
        self._codex_stream_task: asyncio.Task[None] | None = None
        # OpenCode CLI mode: one-shot processes with session ID continuation
        self.opencode_executor: OpenCodeExecutor | None = None
        self._opencode_stream_task: asyncio.Task[None] | None = None
        self._prompt_lock: asyncio.Lock = asyncio.Lock()
        # Interactive permission approval manager (None = bypass/auto mode)
        self.permission_manager: PermissionManager | None = None
        # Plan mode approval gate — set when EnterPlanMode is intercepted.
        # The relay task awaits this event before continuing the stream.
        # None means no approval is pending.
        self._plan_mode_event: asyncio.Event | None = None
        self._plan_mode_approved: bool = False
        # Plan review gate — set when ExitPlanMode is intercepted.
        # The relay blocks here until the user approves or provides feedback.
        # None means no plan review is pending.
        self._plan_review_event: asyncio.Event | None = None
        self._plan_review_approved: bool = False
        # The text the user sent in response to the plan review (approval text or feedback).
        self._plan_review_feedback: str | None = None
        # Token usage accumulators (running totals across all turns)
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cache_creation_input_tokens: int = 0
        self.cache_read_input_tokens: int = 0
        # Tool agent token usage (Claude Code / Codex)
        self.tool_input_tokens: int = 0
        self.tool_output_tokens: int = 0
        self.tool_cost_usd: float = 0.0
        # In-memory todo state — updated on each TodoWrite tool_use
        self._todos: list[dict[str, str]] = []
        # Reason why the session was paused (e.g. "max_turns"); None for manual pauses
        self.paused_reason: str | None = None
        # The resolved absolute path of the project attached to this session.
        # Set from the project_name field in the WS prompt message.
        # None means the session is "Global" (no project attached yet).
        self.main_project_path: str | None = None
        # Custom ordering position.  Lower values appear first.
        # None means "use default createdAt ordering".
        self.sort_order: int | None = None
        # Transient error set when the client sends an invalid project_name.
        # Cleared on the next successful project resolution. Not persisted to DB.
        self.project_name_error: str | None = None
        # Transient subprocess tracking — updated while a subprocess is running.
        # Not persisted to DB; always None after session restore.
        self.subprocess_started_at: datetime | None = None
        self.subprocess_current_tool: str | None = None
        self.subprocess_type: str | None = None
        self.subprocess_display_name: str | None = None
        self.subprocess_working_directory: str | None = None

    @property
    def agent_type(self) -> str | None:
        """Return the managed agent type driving this session, or None for pure-LLM sessions.

        Returns ``"claude_code"`` when a Claude Code executor is attached,
        ``"codex"`` when a Codex executor is attached, ``"opencode"`` when an
        OpenCode executor is attached, and ``None`` for sessions that are handled
        directly by the built-in LLM without a managed subprocess.
        """
        if self.claude_code_executor is not None:
            return "claude_code"
        if self.codex_executor is not None:
            return "codex"
        if self.opencode_executor is not None:
            return "opencode"
        return None

    @property
    def todos(self) -> list[dict[str, str]]:
        return list(self._todos)

    def update_todos(self, todos: list[dict[str, str]]) -> None:
        self._todos = todos

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity_at = datetime.now(UTC)

    @property
    def activity_state(self) -> ActivityState:
        return self._activity_state

    def set_activity(self, state: ActivityState) -> None:
        """Update the activity state and broadcast if changed."""
        if self._activity_state == state:
            return
        self._activity_state = state
        if self._on_update:
            self._on_update()

    def clear_subprocess_tracking(self) -> None:
        """Clear transient subprocess fields and broadcast a null status.

        Called whenever the managed subprocess is no longer running
        (normal end, unexpected exit, cancel, pause, etc.) so the client
        hides the subprocess indicator bar.
        """
        self.subprocess_started_at = None
        self.subprocess_current_tool = None
        self.subprocess_type = None
        self.subprocess_display_name = None
        self.subprocess_working_directory = None
        self.buffer.push_ephemeral(
            MessageType.SUBPROCESS_STATUS,
            {"session_id": self.id, "subprocess_type": None},
        )

    @property
    def title(self) -> str | None:
        return self._title

    @title.setter
    def title(self, value: str | None) -> None:
        self._title = value
        if self._on_update:
            self._on_update()

    def set_active(self) -> None:
        if self.status in (
            SessionStatus.PAUSED,
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
        ):
            return
        old = self.status
        self.status = SessionStatus.ACTIVE
        if old != self.status and self._on_update:
            self._on_update()

    def set_executing(self) -> None:
        if self.status in (
            SessionStatus.PAUSED,
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
        ):
            return
        old = self.status
        self.status = SessionStatus.EXECUTING
        if old != self.status and self._on_update:
            self._on_update()

    def complete(self) -> None:
        if self.status == SessionStatus.PAUSED:
            self.metadata["completed_while_paused"] = True
            return
        self.status = SessionStatus.COMPLETED
        self._activity_state = ActivityState.IDLE
        self.ended_at = datetime.now(UTC)
        self.buffer.close()
        if self._on_update:
            self._on_update()

    def fail(self, error: str | None = None) -> None:
        self.status = SessionStatus.FAILED
        self._activity_state = ActivityState.IDLE
        self.ended_at = datetime.now(UTC)
        self.paused_at = None
        if error:
            self.metadata["error"] = error
        self.buffer.close()
        if self._on_update:
            self._on_update()

    def cancel(self) -> None:
        self.status = SessionStatus.CANCELLED
        self._activity_state = ActivityState.IDLE
        self.ended_at = datetime.now(UTC)
        self.paused_at = None
        self.buffer.close()
        if self._on_update:
            self._on_update()

    def interrupt(self) -> None:
        """Mark the session as interrupted by a backend restart.

        Unlike complete/cancel, does NOT set ended_at (the session is open for
        resumption) and does NOT close the buffer.  The ``INTERRUPTED`` status
        signals to clients that the session can be restored.
        """
        terminal = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
        if self.status in terminal:
            return  # already terminal — leave it alone
        was_paused = self.status == SessionStatus.PAUSED
        self.status = SessionStatus.INTERRUPTED
        self._activity_state = ActivityState.IDLE
        self.metadata["restart_interrupted"] = True
        if was_paused:
            self.metadata["was_paused_before_restart"] = True
        if self._on_update:
            self._on_update()

    def pause(self, reason: str | None = None) -> None:
        """Pause the session. Any running subprocess is killed; new prompts are rejected.

        Args:
            reason: Optional reason for the pause (e.g. ``"max_turns"`` when Claude Code
                hit its turn limit). ``None`` indicates a manual/user-initiated pause.
        """
        terminal = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
        if self.status in terminal:
            raise RuntimeError(f"Cannot pause session in terminal state: {self.status.value}")
        if self.status == SessionStatus.PAUSED:
            raise RuntimeError("Session is already paused")
        self.status = SessionStatus.PAUSED
        self._activity_state = ActivityState.IDLE
        self.paused_at = datetime.now(UTC)
        self.paused_reason = reason
        if self._on_update:
            self._on_update()

    def resume(self) -> None:
        """Resume a paused session."""
        if self.status != SessionStatus.PAUSED:
            raise RuntimeError(f"Cannot resume session in state: {self.status.value}")
        self.status = SessionStatus.ACTIVE
        self._activity_state = ActivityState.IDLE
        self.paused_at = None
        self.paused_reason = None
        self.last_activity_at = datetime.now(UTC)
        if self._on_update:
            self._on_update()

    def restore(self) -> None:
        """Restore a session from a terminal or interrupted state back to ACTIVE."""
        restorable = (
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
            SessionStatus.INTERRUPTED,
        )
        if self.status not in restorable:
            raise RuntimeError(f"Cannot restore session in state: {self.status.value}")
        self.status = SessionStatus.ACTIVE
        self._activity_state = ActivityState.IDLE
        self.ended_at = None
        self.last_activity_at = datetime.now(UTC)
        if self._on_update:
            self._on_update()


class SessionManager:
    """Manages active sessions in memory and archives completed ones to the database."""

    def __init__(self, backend_id: str) -> None:
        self._backend_id = backend_id
        self._sessions: dict[str, ActiveSession] = {}
        self._update_subscribers: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}

    def create_session(self, session_type: SessionType = SessionType.ONE_SHOT) -> ActiveSession:
        session_id = str(uuid.uuid4())
        session = ActiveSession(session_id, session_type)
        # Assign sort_order so new sessions appear at the top of the list.
        # Use min(existing) - 1000, or 0 if no sessions exist yet.
        existing_orders = [s.sort_order for s in self._sessions.values() if s.sort_order is not None]
        session.sort_order = (min(existing_orders) - 1000) if existing_orders else 0
        session._on_update = lambda: self.broadcast_session_update(session)
        self._sessions[session_id] = session
        logger.info("Created session %s (type=%s)", session_id, session_type)
        self.broadcast_session_update(session)
        return session

    def subscribe_updates(self, subscriber_id: str) -> asyncio.Queue[dict[str, Any] | None]:
        """Subscribe to session metadata updates (title/status changes).

        Returns a queue that yields update dicts. ``None`` signals unsubscription.
        """
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._update_subscribers[subscriber_id] = queue
        return queue

    def unsubscribe_updates(self, subscriber_id: str) -> None:
        """Unsubscribe from session metadata updates."""
        queue = self._update_subscribers.pop(subscriber_id, None)
        if queue:
            queue.put_nowait(None)

    def broadcast_session_update(self, session: ActiveSession) -> None:
        """Broadcast a session metadata update to all connected output clients."""
        update: dict[str, Any] = {
            "type": "session_update",
            "session_id": session.id,
            "status": session.status.value,
            "activity_state": session.activity_state.value,
            "title": session.title,
            "session_type": session.session_type.value,
            "created_at": session.created_at.isoformat(),
            "input_tokens": session.input_tokens,
            "output_tokens": session.output_tokens,
            "cache_creation_input_tokens": session.cache_creation_input_tokens,
            "cache_read_input_tokens": session.cache_read_input_tokens,
            "tool_input_tokens": session.tool_input_tokens,
            "tool_output_tokens": session.tool_output_tokens,
            "tool_cost_usd": session.tool_cost_usd,
            "paused_reason": session.paused_reason,
            "worktree": session.metadata.get("worktree"),
            "selected_worktree_path": session.metadata.get("selected_worktree_path"),
            "main_project_path": session.main_project_path,
            "project_name_error": session.project_name_error,
            "agent_type": session.agent_type,
            "sort_order": session.sort_order,
        }
        for queue in self._update_subscribers.values():
            queue.put_nowait(update)

    def broadcast_session_reorder(self, ordered_ids: list[str]) -> None:
        """Broadcast a lightweight session reorder event to all connected clients."""
        msg: dict[str, Any] = {"type": "session_reorder", "order": ordered_ids}
        for queue in self._update_subscribers.values():
            queue.put_nowait(msg)

    def broadcast_task_update(self, task_data: dict[str, Any]) -> None:
        """Broadcast a task update to all connected output clients."""
        msg = {"type": "task_update", **task_data}
        for queue in self._update_subscribers.values():
            queue.put_nowait(msg)

    def broadcast_task_deleted(self, task_id: str) -> None:
        """Broadcast a task deletion to all connected output clients."""
        msg = {"type": "task_deleted", "task_id": task_id}
        for queue in self._update_subscribers.values():
            queue.put_nowait(msg)

    def broadcast_linear_issue_update(self, issue_data: dict[str, Any]) -> None:
        """Broadcast a Linear issue update to all connected output clients."""
        msg = {"type": "linear_issue_update", **issue_data}
        for queue in self._update_subscribers.values():
            queue.put_nowait(msg)

    def broadcast_linear_issue_deleted(self, issue_id: str) -> None:
        """Broadcast a Linear issue deletion to all connected output clients."""
        msg = {"type": "linear_issue_deleted", "id": issue_id}
        for queue in self._update_subscribers.values():
            queue.put_nowait(msg)

    def broadcast_artifact_update(self, artifact_data: dict[str, Any]) -> None:
        """Broadcast an artifact update to all connected output clients."""
        msg = {"type": "artifact_update", **artifact_data}
        for queue in self._update_subscribers.values():
            queue.put_nowait(msg)

    def broadcast_artifact_deleted(self, artifact_id: str) -> None:
        """Broadcast an artifact deletion to all connected output clients."""
        msg = {"type": "artifact_deleted", "artifact_id": artifact_id}
        for queue in self._update_subscribers.values():
            queue.put_nowait(msg)

    def broadcast_artifact_list(self, artifacts: list[dict[str, Any]]) -> None:
        """Broadcast an artifact list to all connected output clients."""
        msg = {"type": "artifact_list", "artifacts": artifacts}
        for queue in self._update_subscribers.values():
            queue.put_nowait(msg)

    def get_session(self, session_id: str) -> ActiveSession | None:
        return self._sessions.get(session_id)

    def list_active_sessions(self) -> list[ActiveSession]:
        terminal = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
        return [s for s in self._sessions.values() if s.status not in terminal]

    def list_all_sessions(self) -> list[ActiveSession]:
        return list(self._sessions.values())

    async def archive_session(self, session_id: str, db: AsyncSession) -> None:
        """Archive a completed session to PostgreSQL and remove from memory."""
        session = self._sessions.get(session_id)
        if session is None:
            logger.warning("Cannot archive session %s: not found", session_id)
            return

        if session.status not in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED):
            logger.warning("Cannot archive session %s: still %s", session_id, session.status)
            return

        session_uuid = uuid.UUID(session.id)

        # Ensure the session row exists before inserting child message rows.
        # The row may already exist if created early by task-linking
        # (e.g. _create_tasks_from_session), so check first.
        existing = await db.get(SessionModel, session_uuid)
        if existing is None:
            db.add(
                SessionModel(
                    id=session_uuid,
                    backend_id=self._backend_id,
                    created_at=session.created_at,
                    ended_at=session.ended_at,
                    session_type=session.session_type.value,
                    status=session.status.value,
                    title=session.title,
                    main_project_path=session.main_project_path,
                    metadata_=session.metadata,
                    conversation_history=session.conversation_history or None,
                    input_tokens=session.input_tokens,
                    output_tokens=session.output_tokens,
                    cache_creation_input_tokens=session.cache_creation_input_tokens,
                    cache_read_input_tokens=session.cache_read_input_tokens,
                    tool_input_tokens=session.tool_input_tokens,
                    tool_output_tokens=session.tool_output_tokens,
                    tool_cost_usd=session.tool_cost_usd,
                    sort_order=session.sort_order,
                )
            )
        else:
            existing.backend_id = self._backend_id
            existing.created_at = session.created_at
            existing.ended_at = session.ended_at
            existing.session_type = session.session_type.value
            existing.status = session.status.value
            existing.title = session.title
            existing.main_project_path = session.main_project_path
            existing.metadata_ = session.metadata
            existing.conversation_history = session.conversation_history or None
            existing.input_tokens = session.input_tokens
            existing.output_tokens = session.output_tokens
            existing.cache_creation_input_tokens = session.cache_creation_input_tokens
            existing.cache_read_input_tokens = session.cache_read_input_tokens
            existing.tool_input_tokens = session.tool_input_tokens
            existing.tool_output_tokens = session.tool_output_tokens
            existing.tool_cost_usd = session.tool_cost_usd
            existing.sort_order = session.sort_order
        # Flush the parent row so the FK constraint
        # (session_messages.session_id → sessions.id) is satisfied when child
        # rows are flushed in the same transaction.  Using flush() instead of
        # commit() keeps everything in a single transaction, avoiding a window
        # where concurrent StaticPool tasks could interfere with the committed
        # parent before children are written (the previous two-commit approach
        # was vulnerable to this with aiosqlite + StaticPool).
        await db.flush()

        # Clear any existing messages to avoid UniqueConstraint conflicts,
        # then insert current buffer messages.
        await db.execute(delete(SessionMessageModel).where(SessionMessageModel.session_id == session_uuid))

        for msg in session.buffer.text_history:
            db.add(
                SessionMessageModel(
                    session_id=session_uuid,
                    sequence=msg.sequence,
                    message_type=msg.message_type.value,
                    content=msg.data.get("content", ""),
                    metadata_=msg.data,
                )
            )

        try:
            await db.commit()
        except asyncio.CancelledError:
            # With aiosqlite + StaticPool, the COMMIT may complete on the
            # background thread even though the asyncio task was cancelled.
            # Always remove the session from memory to prevent
            # save_all_sessions from encountering duplicate messages.
            removed = self._sessions.pop(session_id, None)
            if removed is not None:
                removed._on_update = None
            raise
        session._on_update = None  # Prevent late broadcasts from background tasks
        del self._sessions[session_id]
        logger.info("Archived session %s to database", session_id)

    async def restore_session(self, session_id: str, db: AsyncSession) -> ActiveSession:
        """Restore an archived session from PostgreSQL back to an in-memory active session.

        Loads session metadata, conversation history, and buffer messages from the DB.
        Sets the session status back to ACTIVE.

        Raises:
            ValueError: If the session is not found in the DB.
            RuntimeError: If the session is already in memory or not in a terminal state.
        """
        if session_id in self._sessions:
            raise RuntimeError(f"Session {session_id} is already active in memory")

        try:
            session_uuid = uuid.UUID(session_id)
        except ValueError:
            raise ValueError(f"Invalid session ID: {session_id}") from None

        row = await db.get(SessionModel, session_uuid)
        if row is None:
            raise ValueError(f"Session not found in database: {session_id}")

        restorable_statuses = ("completed", "failed", "cancelled", "interrupted")
        if row.status not in restorable_statuses:
            raise RuntimeError(f"Cannot restore session in state: {row.status}")

        session_type = SessionType(row.session_type)
        session = ActiveSession(session_id, session_type)
        created_at = row.created_at
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        session.created_at = created_at
        session.status = SessionStatus.ACTIVE
        session._activity_state = ActivityState.IDLE
        session._title = row.title
        session.main_project_path = row.main_project_path
        session.metadata = dict(row.metadata_) if row.metadata_ else {}
        # Reset the task-update-fired flag so that ending the restored session
        # triggers a fresh LLM task evaluation rather than being suppressed.
        session.metadata.pop("_task_update_fired", None)
        session.last_activity_at = datetime.now(UTC)
        # Restore token usage
        session.input_tokens = row.input_tokens or 0
        session.output_tokens = row.output_tokens or 0
        session.cache_creation_input_tokens = row.cache_creation_input_tokens or 0
        session.cache_read_input_tokens = row.cache_read_input_tokens or 0
        session.tool_input_tokens = row.tool_input_tokens or 0
        session.tool_output_tokens = row.tool_output_tokens or 0
        session.tool_cost_usd = row.tool_cost_usd or 0.0
        session.sort_order = row.sort_order

        if row.conversation_history:
            session.conversation_history = list(row.conversation_history)

        # Rebuild buffer from archived messages
        stmt = (
            select(SessionMessageModel)
            .where(SessionMessageModel.session_id == session_uuid)
            .order_by(SessionMessageModel.sequence)
        )
        result = await db.execute(stmt)
        for msg_row in result.scalars():
            msg_type = MessageType(msg_row.message_type)
            data = dict(msg_row.metadata_) if msg_row.metadata_ else {}
            if "content" not in data and msg_row.content:
                data["content"] = msg_row.content
            buffered = BufferedMessage(
                sequence=msg_row.sequence,
                message_type=msg_type,
                data=data,
            )
            session.buffer._text_messages.append(buffered)
            session.buffer._text_sequence = max(session.buffer._text_sequence, msg_row.sequence)

        # Register in memory
        session._on_update = lambda: self.broadcast_session_update(session)
        self._sessions[session_id] = session
        self.broadcast_session_update(session)

        # Remove from DB using bulk deletes to avoid ORM cascade
        # (db.delete(row) would trigger the messages relationship and set session_id=NULL)
        # Delete all child records that reference this session before deleting the session itself
        await db.execute(delete(SessionMessageModel).where(SessionMessageModel.session_id == session_uuid))
        await db.execute(delete(ToolExecutionModel).where(ToolExecutionModel.session_id == session_uuid))
        await db.execute(delete(TaskSessionModel).where(TaskSessionModel.session_id == session_uuid))
        await db.execute(delete(ArtifactModel).where(ArtifactModel.session_id == session_uuid))
        await db.execute(delete(SessionModel).where(SessionModel.id == session_uuid))
        await db.commit()

        logger.info("Restored session %s from database (type=%s)", session_id, session_type)
        return session

    async def list_all_with_archived(self, db: AsyncSession) -> list[dict[str, Any]]:
        """Return in-memory sessions merged with archived sessions from PostgreSQL.

        Excludes duplicates (in-memory takes precedence). Sorted by created_at descending.
        """
        in_memory_ids: set[str] = set()
        result: list[dict[str, Any]] = []

        for s in self._sessions.values():
            in_memory_ids.add(s.id)
            result.append(
                {
                    "session_id": s.id,
                    "status": s.status.value,
                    "activity_state": s.activity_state.value,
                    "session_type": s.session_type.value,
                    "created_at": s.created_at,
                    "title": s.title,
                    "input_tokens": s.input_tokens,
                    "output_tokens": s.output_tokens,
                    "cache_creation_input_tokens": s.cache_creation_input_tokens,
                    "cache_read_input_tokens": s.cache_read_input_tokens,
                    "tool_input_tokens": s.tool_input_tokens,
                    "tool_output_tokens": s.tool_output_tokens,
                    "tool_cost_usd": s.tool_cost_usd,
                    "worktree": s.metadata.get("worktree"),
                    "selected_worktree_path": s.metadata.get("selected_worktree_path"),
                    "main_project_path": s.main_project_path,
                    "agent_type": s.agent_type,
                    "sort_order": s.sort_order,
                }
            )

        # Fetch archived sessions from DB, excluding those still in memory
        stmt = (
            select(SessionModel)
            .where(SessionModel.backend_id == self._backend_id)
            .order_by(SessionModel.created_at.desc())
        )
        rows = await db.execute(stmt)
        for row in rows.scalars():
            sid = str(row.id)
            if sid not in in_memory_ids:
                archived_meta: dict[str, Any] = dict(row.metadata_) if row.metadata_ else {}
                result.append(
                    {
                        "session_id": sid,
                        "status": row.status,
                        "activity_state": ActivityState.IDLE.value,
                        "session_type": row.session_type,
                        "created_at": row.created_at,
                        "title": row.title,
                        "input_tokens": row.input_tokens or 0,
                        "output_tokens": row.output_tokens or 0,
                        "cache_creation_input_tokens": row.cache_creation_input_tokens or 0,
                        "cache_read_input_tokens": row.cache_read_input_tokens or 0,
                        "tool_input_tokens": row.tool_input_tokens or 0,
                        "tool_output_tokens": row.tool_output_tokens or 0,
                        "tool_cost_usd": row.tool_cost_usd or 0.0,
                        "worktree": archived_meta.get("worktree"),
                        "selected_worktree_path": archived_meta.get("selected_worktree_path"),
                        "main_project_path": row.main_project_path,
                        "agent_type": None,
                        "sort_order": row.sort_order,
                    }
                )

        result.sort(key=session_sort_key)
        return result

    async def archive_all_completed(self, db: AsyncSession) -> None:
        """Archive all completed/failed/cancelled sessions."""
        to_archive = [
            s.id
            for s in self._sessions.values()
            if s.status in (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
        ]
        for session_id in to_archive:
            await self.archive_session(session_id, db)

    async def persist_session_metadata(self, session: "ActiveSession", db: AsyncSession) -> None:
        """Write the session's current metadata and main_project_path to the DB.

        Called after mid-session mutations (e.g. worktree selection, project change)
        to make the change durable before archival so it survives backend restarts.
        Only updates the existing stub row — the full archive write at session end
        supersedes this with the complete record.
        """
        try:
            session_uuid = uuid.UUID(session.id)
            row = await db.get(SessionModel, session_uuid)
            if row is not None:
                row.metadata_ = dict(session.metadata)
                row.main_project_path = session.main_project_path
                await db.commit()
        except Exception:
            logger.warning("Failed to persist metadata for session %s", session.id, exc_info=True)

    def complete_all_active(self) -> int:
        """Mark all non-terminal sessions as completed for graceful shutdown.

        Returns the number of sessions that were moved to COMPLETED status.
        """
        terminal = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
        count = 0
        for session in self._sessions.values():
            if session.status not in terminal:
                # For paused sessions, clear paused state first so complete() takes effect
                if session.status == SessionStatus.PAUSED:
                    session.status = SessionStatus.ACTIVE
                session.complete()
                count += 1
        return count

    def interrupt_all_active(self) -> int:
        """Mark all non-terminal sessions as INTERRUPTED for graceful shutdown.

        Preferred over complete_all_active during shutdown because INTERRUPTED
        sessions can be restored by the client after the backend restarts.
        Unlike complete(), does not set ended_at so clients know resumption is
        possible.  Returns the number of sessions affected.
        """
        terminal = (
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
            SessionStatus.INTERRUPTED,
        )
        count = 0
        for session in self._sessions.values():
            if session.status not in terminal:
                session.interrupt()
                count += 1
        return count

    async def reload_stale_sessions(self, db: AsyncSession, backend_id: str) -> int:
        """Reload stale sessions from the database back into memory on startup.

        Finds all non-terminal sessions for *backend_id* and restores them to the
        in-memory store so clients can continue using them immediately — without any
        explicit restore step.  Paused sessions are restored as PAUSED; every other
        non-terminal status (active, executing, created, interrupted) is restored as
        ACTIVE because the subprocess is gone after a restart.

        Each session is committed independently so that one corrupt row cannot prevent
        the others from loading.  Returns the number of sessions reloaded.
        """
        stale_statuses = (
            SessionStatus.CREATED,
            SessionStatus.ACTIVE,
            SessionStatus.EXECUTING,
            SessionStatus.PAUSED,
            SessionStatus.INTERRUPTED,
        )
        stale_rows = (
            (
                await db.execute(
                    select(SessionModel).where(
                        SessionModel.status.in_([s.value for s in stale_statuses]),
                        SessionModel.backend_id == backend_id,
                    )
                )
            )
            .scalars()
            .all()
        )

        if not stale_rows:
            return 0

        reloaded = 0
        for row in stale_rows:
            session_id = str(row.id)
            try:
                if session_id in self._sessions:
                    logger.warning("Skipping stale session %s — already in memory", session_id)
                    continue

                session_type = SessionType(row.session_type)
                session = ActiveSession(session_id, session_type)

                created_at = row.created_at
                if created_at and created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=UTC)
                session.created_at = created_at

                # Paused sessions are restored as PAUSED; everything else as ACTIVE
                # (executing sessions lose their subprocess on restart).
                was_paused = row.status == SessionStatus.PAUSED.value
                session.status = SessionStatus.PAUSED if was_paused else SessionStatus.ACTIVE
                session._activity_state = ActivityState.IDLE

                session._title = row.title
                session.main_project_path = row.main_project_path

                meta = dict(row.metadata_) if row.metadata_ else {}
                meta["restart_interrupted"] = True
                if was_paused:
                    meta["was_paused_before_restart"] = True
                meta.pop("_task_update_fired", None)
                session.metadata = meta

                session.last_activity_at = datetime.now(UTC)
                session.input_tokens = row.input_tokens or 0
                session.output_tokens = row.output_tokens or 0
                session.cache_creation_input_tokens = row.cache_creation_input_tokens or 0
                session.cache_read_input_tokens = row.cache_read_input_tokens or 0
                session.tool_input_tokens = row.tool_input_tokens or 0
                session.tool_output_tokens = row.tool_output_tokens or 0
                session.tool_cost_usd = row.tool_cost_usd or 0.0

                if row.conversation_history:
                    session.conversation_history = list(row.conversation_history)

                # Rebuild buffer from saved messages
                session_uuid = row.id
                msg_rows = (
                    (
                        await db.execute(
                            select(SessionMessageModel)
                            .where(SessionMessageModel.session_id == session_uuid)
                            .order_by(SessionMessageModel.sequence)
                        )
                    )
                    .scalars()
                    .all()
                )
                for msg_row in msg_rows:
                    try:
                        msg_type = MessageType(msg_row.message_type)
                    except ValueError:
                        logger.warning(
                            "Skipping unknown message type %r in session %s",
                            msg_row.message_type,
                            session_id,
                        )
                        continue
                    data = dict(msg_row.metadata_) if msg_row.metadata_ else {}
                    if "content" not in data and msg_row.content:
                        data["content"] = msg_row.content
                    buffered = BufferedMessage(
                        sequence=msg_row.sequence,
                        message_type=msg_type,
                        data=data,
                    )
                    session.buffer._text_messages.append(buffered)
                    session.buffer._text_sequence = max(session.buffer._text_sequence, msg_row.sequence)

                # Register in memory and remove from DB so the session is fully live
                session._on_update = lambda s=session: self.broadcast_session_update(s)
                self._sessions[session_id] = session

                await db.execute(delete(SessionMessageModel).where(SessionMessageModel.session_id == session_uuid))
                await db.execute(delete(ToolExecutionModel).where(ToolExecutionModel.session_id == session_uuid))
                await db.execute(delete(TaskSessionModel).where(TaskSessionModel.session_id == session_uuid))
                await db.execute(delete(ArtifactModel).where(ArtifactModel.session_id == session_uuid))
                await db.execute(delete(SessionModel).where(SessionModel.id == session_uuid))
                await db.commit()

                reloaded += 1
            except Exception:
                logger.exception("Failed to reload stale session %s on startup", session_id)
                try:
                    await db.rollback()
                except Exception:
                    logger.debug("Rollback after failed session reload also failed", exc_info=True)

        logger.info("Reloaded %d/%d stale session(s) from database on startup", reloaded, len(stale_rows))
        return reloaded

    async def save_all_sessions(self, db: AsyncSession) -> None:
        """Save ALL in-memory sessions to the database for graceful shutdown.

        Unlike archive_session, this saves sessions regardless of their status,
        preserving the current status so they can appear in the archived sessions list.
        Does not remove sessions from memory since the server is shutting down.

        Each session is committed independently so that a failure in one
        (e.g. duplicate messages from an interrupted archive) does not cause
        all other sessions to lose their data.
        """
        sessions = list(self._sessions.values())
        if not sessions:
            logger.info("No in-memory sessions to save on shutdown")
            return

        saved = 0
        for session in sessions:
            try:
                session_uuid = uuid.UUID(session.id)

                # Delete any existing messages for this session to avoid
                # UniqueConstraint violations if the session was partially
                # archived (commit completed but session not removed from memory).
                existing_msg_count = await db.scalar(
                    select(func.count())
                    .select_from(SessionMessageModel)
                    .where(SessionMessageModel.session_id == session_uuid)
                )
                if existing_msg_count and existing_msg_count > 0:
                    await db.execute(delete(SessionMessageModel).where(SessionMessageModel.session_id == session_uuid))

                existing = await db.get(SessionModel, session_uuid)
                # Only force ended_at for definitively-ended sessions.  Non-terminal
                # sessions (active, paused, executing, created, interrupted) keep
                # ended_at=None so reload_stale_sessions can restore them on restart.
                terminal_with_end = (
                    SessionStatus.COMPLETED,
                    SessionStatus.FAILED,
                    SessionStatus.CANCELLED,
                )
                effective_ended_at = (
                    session.ended_at or datetime.now(UTC) if session.status in terminal_with_end else session.ended_at
                )

                if existing is None:
                    db.add(
                        SessionModel(
                            id=session_uuid,
                            backend_id=self._backend_id,
                            created_at=session.created_at,
                            ended_at=effective_ended_at,
                            session_type=session.session_type.value,
                            status=session.status.value,
                            title=session.title,
                            main_project_path=session.main_project_path,
                            metadata_=session.metadata,
                            input_tokens=session.input_tokens,
                            output_tokens=session.output_tokens,
                            cache_creation_input_tokens=session.cache_creation_input_tokens,
                            cache_read_input_tokens=session.cache_read_input_tokens,
                            tool_input_tokens=session.tool_input_tokens,
                            tool_output_tokens=session.tool_output_tokens,
                            tool_cost_usd=session.tool_cost_usd,
                            conversation_history=session.conversation_history or None,
                        )
                    )
                else:
                    existing.backend_id = self._backend_id
                    existing.created_at = session.created_at
                    existing.ended_at = effective_ended_at
                    existing.session_type = session.session_type.value
                    existing.status = session.status.value
                    existing.title = session.title
                    existing.main_project_path = session.main_project_path
                    existing.metadata_ = session.metadata
                    existing.input_tokens = session.input_tokens
                    existing.output_tokens = session.output_tokens
                    existing.cache_creation_input_tokens = session.cache_creation_input_tokens
                    existing.cache_read_input_tokens = session.cache_read_input_tokens
                    existing.tool_input_tokens = session.tool_input_tokens
                    existing.tool_output_tokens = session.tool_output_tokens
                    existing.tool_cost_usd = session.tool_cost_usd
                    existing.conversation_history = session.conversation_history or None

                # Flush the session row before inserting child session_messages so
                # the FK constraint (session_messages.session_id → sessions.id) is
                # satisfied even when the parent row is being created in this same
                # transaction.  Mirrors the explicit flush in archive_session.
                await db.flush()

                for msg in session.buffer.text_history:
                    db_msg = SessionMessageModel(
                        session_id=session_uuid,
                        sequence=msg.sequence,
                        message_type=msg.message_type.value,
                        content=msg.data.get("content", ""),
                        metadata_=msg.data,
                    )
                    db.add(db_msg)

                await db.commit()
                saved += 1
            except Exception:
                logger.exception("Failed to save session %s on shutdown", session.id)
                try:
                    await db.rollback()
                except Exception:
                    logger.debug("Rollback after failed session save also failed", exc_info=True)

        logger.info("Saved %d/%d in-memory sessions to database on shutdown", saved, len(sessions))
