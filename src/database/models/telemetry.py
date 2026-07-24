"""SQLAlchemy telemetry models (turns and tool calls)."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base


class TelemetryMinutely(Base):
    """Pre-aggregated 1-minute buckets for fast time-series queries."""

    __tablename__ = "telemetry_minutely"
    __table_args__ = (
        UniqueConstraint("backend_id", "bucket", "session_id", name="uq_telemetry_minutely"),
        Index("idx_telemetry_minutely_lookup", "backend_id", "bucket", "session_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    backend_id: Mapped[str] = mapped_column(String(36), nullable=False)
    bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # Token usage
    tokens_sent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tokens_received: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cache_creation: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    cache_read: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)

    # Latency stored as microseconds for integer precision
    llm_duration_sum_us: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    llm_duration_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_duration_sum_us: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    tool_duration_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inter_tool_gap_sum_us: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    inter_tool_gap_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    inter_turn_gap_sum_us: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    inter_turn_gap_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Counts
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parallel_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TelemetryState(Base):
    """Durable aggregation cursor so restarts don't re-count history.

    Holds the high-water mark (max ``ts_end`` already folded into
    ``telemetry_minutely``) per backend. Without it the in-memory watermark
    resets on restart and the additive minutely upserts re-add every completed
    turn/tool row inside the retention window — inflating all history.
    """

    __tablename__ = "telemetry_state"

    backend_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    aggregation_watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
