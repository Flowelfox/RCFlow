"""SQLAlchemy model for cached GitHub pull requests."""

from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.models.base import Base

if TYPE_CHECKING:
    from src.database.models.task import Task


class GitHubPR(Base):
    """Cached mirror of a GitHub pull request for a specific backend.

    Metadata only — diffs and file patches are fetched live from GitHub when a
    PR is opened.  ``github_id`` is the PR's GraphQL node id (stable across
    renames); ``repo_owner``/``repo_name``/``number`` address it on the REST API.
    """

    __tablename__ = "github_prs"
    __table_args__ = (
        UniqueConstraint("backend_id", "github_id"),
        Index("ix_github_prs_backend_id", "backend_id"),
        Index("ix_github_prs_role", "role"),
        Index("ix_github_prs_state", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    backend_id: Mapped[str] = mapped_column(String(36), nullable=False)
    github_id: Mapped[str] = mapped_column(String(255), nullable=False)  # PR node_id
    repo_owner: Mapped[str] = mapped_column(String(255), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(255), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), nullable=False)  # open|closed|merged
    draft: Mapped[bool] = mapped_column(default=False, nullable=False)
    # GraphQL reviewDecision: APPROVED | CHANGES_REQUESTED | REVIEW_REQUIRED | null.
    review_decision: Mapped[str | None] = mapped_column(String(20))
    # GraphQL mergeable: MERGEABLE | CONFLICTING | UNKNOWN | null.
    merge_status: Mapped[str | None] = mapped_column(String(20))
    # The local checkout this worker maps the PR's repo to (by git remote), or
    # null when this worker has no clone. Drives the "Worker/Project" badge and
    # clone-gating for writable actions.
    project_name: Mapped[str | None] = mapped_column(String(255))
    project_path: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    author_avatar_url: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False)  # html_url
    base_ref: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    head_ref: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    additions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    deletions: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    changed_files: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Which listing bucket last synced this PR: "for_me" (review-requested) or
    # "created" (authored).  A PR can belong to both; this records the most
    # recent sync's bucket (MVP — refined to multi-membership in a later phase).
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("tasks.id", ondelete="SET NULL"))

    task: Mapped[Task | None] = relationship("Task")
