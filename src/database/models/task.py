from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base

if TYPE_CHECKING:
    from src.database.models.artifact import Artifact
    from src.database.models.session import Session


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

    # FK to the most recently generated plan Artifact for this task. Null means
    # no plan has been created yet. ON DELETE SET NULL so deleting the artifact
    # record clears the reference without deleting the task.
    plan_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid,
        ForeignKey("artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    plan_artifact: Mapped[Artifact | None] = relationship(
        "Artifact",
        foreign_keys="[Task.plan_artifact_id]",
        lazy="select",
    )

    # Relationship to sessions via association table
    sessions: Mapped[list[Session]] = relationship(secondary="task_sessions", back_populates="tasks")


class TaskSession(Base):
    __tablename__ = "task_sessions"
    __table_args__ = (UniqueConstraint("task_id", "session_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False)
    attached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
