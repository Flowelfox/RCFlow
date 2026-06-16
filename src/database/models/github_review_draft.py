"""SQLAlchemy model for a local pending GitHub review (draft)."""

from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base


class GitHubReviewDraft(Base):
    """A human's in-progress review of a PR, before it is submitted to GitHub.

    Holds the overall verdict (``event``), the summary ``body``, and the queue of
    inline ``comments`` (JSON array of ``{path, line, side, body}``).  Exactly one
    draft per ``(backend_id, pr_id)``; cleared once the review is submitted.
    """

    __tablename__ = "github_review_drafts"
    __table_args__ = (UniqueConstraint("backend_id", "pr_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    backend_id: Mapped[str] = mapped_column(String(36), nullable=False)
    pr_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("github_prs.id", ondelete="CASCADE"), nullable=False)
    # Pending verdict: APPROVE | REQUEST_CHANGES | COMMENT.
    event: Mapped[str] = mapped_column(String(20), nullable=False, default="COMMENT")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    # JSON array of queued inline comments: [{path, line, side, body}, ...]
    comments: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
