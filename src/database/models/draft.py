import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base


class Draft(Base):
    """One unsent message draft per session.

    Created/updated when the client saves a draft via PUT /sessions/{id}/draft.
    Automatically deleted when the parent session is deleted (CASCADE).
    """

    __tablename__ = "drafts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("sessions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # Set explicitly in every write — never relies on onupdate, which does not
    # fire for raw SQL upserts.
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
