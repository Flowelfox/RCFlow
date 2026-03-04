import asyncio
import logging
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.buffer import BufferedMessage, MessageType, SessionBuffer
from src.models.db import Session as SessionModel
from src.models.db import SessionMessage as SessionMessageModel
from src.models.db import ToolExecution as ToolExecutionModel

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.core.permissions import PermissionManager
    from src.executors.claude_code import ClaudeCodeExecutor
    from src.executors.codex import CodexExecutor

logger = logging.getLogger(__name__)


class SessionStatus(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    EXECUTING = "executing"
    PAUSED = "paused"
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
        self._prompt_lock: asyncio.Lock = asyncio.Lock()
        # Interactive permission approval manager (None = bypass/auto mode)
        self.permission_manager: PermissionManager | None = None

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

    @property
    def title(self) -> str | None:
        return self._title

    @title.setter
    def title(self, value: str | None) -> None:
        self._title = value
        if self._on_update:
            self._on_update()

    def set_active(self) -> None:
        if self.status == SessionStatus.PAUSED:
            return
        old = self.status
        self.status = SessionStatus.ACTIVE
        if old != self.status and self._on_update:
            self._on_update()

    def set_executing(self) -> None:
        if self.status == SessionStatus.PAUSED:
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

    def pause(self) -> None:
        """Pause the session. Any running subprocess is killed; new prompts are rejected."""
        terminal = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
        if self.status in terminal:
            raise RuntimeError(f"Cannot pause session in terminal state: {self.status.value}")
        if self.status == SessionStatus.PAUSED:
            raise RuntimeError("Session is already paused")
        self.status = SessionStatus.PAUSED
        self._activity_state = ActivityState.IDLE
        self.paused_at = datetime.now(UTC)
        if self._on_update:
            self._on_update()

    def resume(self) -> None:
        """Resume a paused session."""
        if self.status != SessionStatus.PAUSED:
            raise RuntimeError(f"Cannot resume session in state: {self.status.value}")
        self.status = SessionStatus.ACTIVE
        self._activity_state = ActivityState.IDLE
        self.paused_at = None
        self.last_activity_at = datetime.now(UTC)
        if self._on_update:
            self._on_update()

    def restore(self) -> None:
        """Restore a session from a terminal state back to ACTIVE."""
        terminal = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
        if self.status not in terminal:
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
        }
        for queue in self._update_subscribers.values():
            queue.put_nowait(update)

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

        db_session = SessionModel(
            id=uuid.UUID(session.id),
            backend_id=self._backend_id,
            created_at=session.created_at,
            ended_at=session.ended_at,
            session_type=session.session_type.value,
            status=session.status.value,
            title=session.title,
            metadata_=session.metadata,
            conversation_history=session.conversation_history or None,
        )
        db.add(db_session)
        await db.flush()

        for msg in session.buffer.text_history:
            db_msg = SessionMessageModel(
                session_id=uuid.UUID(session.id),
                sequence=msg.sequence,
                message_type=msg.message_type.value,
                content=msg.data.get("content", ""),
                metadata_=msg.data,
            )
            db.add(db_msg)

        await db.commit()
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

        terminal_statuses = ("completed", "failed", "cancelled")
        if row.status not in terminal_statuses:
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
        session.metadata = dict(row.metadata_) if row.metadata_ else {}
        session.last_activity_at = datetime.now(UTC)

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
        await db.execute(delete(SessionMessageModel).where(SessionMessageModel.session_id == session_uuid))
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
                result.append(
                    {
                        "session_id": sid,
                        "status": row.status,
                        "activity_state": ActivityState.IDLE.value,
                        "session_type": row.session_type,
                        "created_at": row.created_at,
                        "title": row.title,
                    }
                )

        result.sort(
            key=lambda x: x["created_at"].replace(tzinfo=None) if x["created_at"] else datetime.min,
            reverse=True,
        )
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

    async def save_all_sessions(self, db: AsyncSession) -> None:
        """Save ALL in-memory sessions to the database for graceful shutdown.

        Unlike archive_session, this saves sessions regardless of their status,
        preserving the current status so they can appear in the archived sessions list.
        Does not remove sessions from memory since the server is shutting down.
        """
        sessions = list(self._sessions.values())
        if not sessions:
            logger.info("No in-memory sessions to save on shutdown")
            return

        saved = 0
        for session in sessions:
            try:
                db_session = SessionModel(
                    id=uuid.UUID(session.id),
                    backend_id=self._backend_id,
                    created_at=session.created_at,
                    ended_at=session.ended_at or datetime.now(UTC),
                    session_type=session.session_type.value,
                    status=session.status.value,
                    title=session.title,
                    metadata_=session.metadata,
                    conversation_history=session.conversation_history or None,
                )
                db.add(db_session)

                for msg in session.buffer.text_history:
                    db_msg = SessionMessageModel(
                        session_id=uuid.UUID(session.id),
                        sequence=msg.sequence,
                        message_type=msg.message_type.value,
                        content=msg.data.get("content", ""),
                        metadata_=msg.data,
                    )
                    db.add(db_msg)

                saved += 1
            except Exception:
                logger.exception("Failed to save session %s on shutdown", session.id)

        try:
            await db.commit()
            logger.info("Saved %d/%d in-memory sessions to database on shutdown", saved, len(sessions))
        except Exception:
            logger.exception("Failed to commit sessions to database on shutdown")
            await db.rollback()
