import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base


class SessionPendingMessage(Base):
    """A user message queued while the session's agent was busy.

    Rows survive backend restarts; on resubscribe the client repopulates the
    pinned queue from ``session_update.queued_messages``.  Attachments are
    spilled to disk under ``data/pending_attachments/<session_id>/<queued_id>/``
    (see :class:`SessionPendingMessageStore`).  Cascade-deleted with the parent
    session; the store layer is responsible for removing the attachment
    directory alongside the row.
    """

    __tablename__ = "session_pending_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    queued_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    display_content: Mapped[str] = mapped_column(Text, nullable=False)
    attachments_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    project_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_worktree_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
