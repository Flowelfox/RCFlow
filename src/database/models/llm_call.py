import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base


class LLMCall(Base):
    """Log of a single LLM API call (one turn)."""

    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_session_id", "session_id"),
        Index("ix_llm_calls_started_at", "started_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    message_id: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(255))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_creation_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cache_read_input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    stop_reason: Mapped[str] = mapped_column(String(50), nullable=False)
    has_tool_calls: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    request_messages: Mapped[dict | list] = mapped_column(JSON, nullable=False)
    response_text: Mapped[str | None] = mapped_column(Text)
    service_tier: Mapped[str | None] = mapped_column(String(50))
    inference_geo: Mapped[str | None] = mapped_column(String(100))
