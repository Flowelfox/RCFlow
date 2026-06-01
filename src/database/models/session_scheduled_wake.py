"""DB model for ``ScheduleWakeup`` tool calls.

Claude Code's ``ScheduleWakeup`` tool tells RCFlow to fire a prompt
back at the agent after a delay (the engine behind ``/loop`` dynamic
auto-pacing).  Rows survive backend restarts so a wake armed before
a crash still fires at the original ``fire_at``; the scheduler
re-arms each row at startup via :class:`SessionScheduledWakeStore`.

A row reaches its terminal state through either the ``fired_at`` or
``cancelled_at`` column — the scheduler picks whichever transition
happens first and the other stays NULL.  Cascade-deleted with the
parent session row.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base


class SessionScheduledWake(Base):
    """A wakeup the agent scheduled for itself via ``ScheduleWakeup``."""

    __tablename__ = "session_scheduled_wakes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    wake_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
