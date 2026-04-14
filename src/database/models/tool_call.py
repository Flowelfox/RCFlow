from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base

if TYPE_CHECKING:
    from src.database.models.session import Session


class ToolCall(Base):
    """One tool invocation inside a session (shell, http, worktree, or agent tool)."""

    __tablename__ = "tool_calls"
    __table_args__ = (
        Index("idx_tool_calls_session_id", "session_id"),
        Index("idx_tool_calls_backend_id_ts", "backend_id", "ts_start"),
        Index("idx_tool_calls_tool_name", "backend_id", "tool_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    turn_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("session_turns.id", ondelete="SET NULL"), nullable=True
    )
    backend_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    turn_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_call_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    ts_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ts_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")
    executor_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    session: Mapped[Session] = relationship("Session", back_populates="tool_calls_telemetry")
