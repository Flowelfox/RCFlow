import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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

    messages: Mapped[list["SessionMessage"]] = relationship(
        back_populates="session", order_by="SessionMessage.sequence"
    )
    tool_executions: Mapped[list["ToolExecution"]] = relationship(back_populates="session")
    tasks: Mapped[list["Task"]] = relationship(
        secondary="task_sessions", back_populates="sessions"
    )
    turns: Mapped[list["SessionTurn"]] = relationship(back_populates="session")
    tool_calls_telemetry: Mapped[list["ToolCall"]] = relationship(back_populates="session")


class SessionMessage(Base):
    __tablename__ = "session_messages"
    __table_args__ = (UniqueConstraint("session_id", "sequence"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sessions.id"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    message_type: Mapped[str] = mapped_column(String(30), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session: Mapped[Session] = relationship(back_populates="messages")


class ToolExecution(Base):
    __tablename__ = "tool_executions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sessions.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tool_input: Mapped[dict] = mapped_column(JSON, nullable=False)
    tool_output: Mapped[str | None] = mapped_column(Text)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    session: Mapped[Session] = relationship(back_populates="tool_executions")


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


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_backend_id", "backend_id"),
        Index("ix_tasks_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    backend_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="todo")
    source: Mapped[str] = mapped_column(String(10), nullable=False)  # "ai" or "user"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Relationship to sessions via association table
    sessions: Mapped[list["Session"]] = relationship(
        secondary="task_sessions", back_populates="tasks"
    )


class TaskSession(Base):
    __tablename__ = "task_sessions"
    __table_args__ = (
        UniqueConstraint("task_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    attached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class LinearIssue(Base):
    """Cached mirror of a Linear issue for a specific backend."""

    __tablename__ = "linear_issues"
    __table_args__ = (
        UniqueConstraint("backend_id", "linear_id"),
        Index("ix_linear_issues_backend_id", "backend_id"),
        Index("ix_linear_issues_state_type", "state_type"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    backend_id: Mapped[str] = mapped_column(String(36), nullable=False)
    linear_id: Mapped[str] = mapped_column(String(255), nullable=False)
    identifier: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g. "ENG-123"
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0=none 1=urgent 2=high 3=medium 4=low
    state_name: Mapped[str] = mapped_column(String(100), nullable=False)
    state_type: Mapped[str] = mapped_column(String(30), nullable=False)  # triage|backlog|unstarted|started|completed|cancelled
    assignee_id: Mapped[str | None] = mapped_column(String(255))
    assignee_name: Mapped[str | None] = mapped_column(String(255))
    team_id: Mapped[str] = mapped_column(String(255), nullable=False)
    team_name: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text, nullable=False)
    labels: Mapped[str] = mapped_column(Text, nullable=False, default="[]")  # JSON array
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("tasks.id", ondelete="SET NULL"))

    task: Mapped["Task | None"] = relationship("Task")


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("backend_id", "file_path"),
        Index("ix_artifacts_backend_id", "backend_id"),
        Index("ix_artifacts_session_id", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    backend_id: Mapped[str] = mapped_column(String(36), nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    file_extension: Mapped[str] = mapped_column(String(50), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(100))
    discovered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    modified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("sessions.id"))

    # Relationship to session
    session: Mapped[Session | None] = relationship("Session")


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
    turn_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("session_turns.id", ondelete="SET NULL"), nullable=True)
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
