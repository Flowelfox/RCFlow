from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base

if TYPE_CHECKING:
    from src.database.models.session import Session


class SessionTurn(Base):
    """One complete prompt→response LLM turn (one user message → full streaming response)."""

    __tablename__ = "session_turns"
    __table_args__ = (
        Index("idx_session_turns_session_id", "session_id"),
        Index("idx_session_turns_backend_id_ts", "backend_id", "ts_start"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    backend_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False)
    ts_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ts_first_token: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ts_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    llm_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    interrupted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    session: Mapped[Session] = relationship("Session", back_populates="turns")
