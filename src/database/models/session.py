from __future__ import annotations

import uuid  # noqa: TC003
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base

if TYPE_CHECKING:
    from src.database.models.session_message import SessionMessage
    from src.database.models.session_turn import SessionTurn
    from src.database.models.task import Task
    from src.database.models.tool_call import ToolCall
    from src.database.models.tool_execution import ToolExecution


class Session(Base):
    __tablename__ = "sessions"
    __table_args__ = (Index("ix_sessions_backend_id", "backend_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    backend_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    session_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    main_project_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    conversation_history: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Token usage totals
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_creation_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_read_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool_cost_usd: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    sort_order: Mapped[int | None] = mapped_column(Integer, nullable=True)

    messages: Mapped[list[SessionMessage]] = relationship(back_populates="session", order_by="SessionMessage.sequence")
    tool_executions: Mapped[list[ToolExecution]] = relationship(back_populates="session")
    tasks: Mapped[list[Task]] = relationship(secondary="task_sessions", back_populates="sessions")
    turns: Mapped[list[SessionTurn]] = relationship(back_populates="session")
    tool_calls_telemetry: Mapped[list[ToolCall]] = relationship(back_populates="session")
