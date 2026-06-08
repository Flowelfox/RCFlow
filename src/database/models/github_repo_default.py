"""SQLAlchemy model for this worker's default-repo routing flags.

Each row marks that *this* worker (backend) is the preferred target for actions
on a given GitHub repository — used by the client to route writable PR actions
(resolve-conflicts / fix / assist) to one worker when several back the same PR.
The flag is intentionally per-worker: the client tallies votes across workers and
heals conflicts, so there is no central map.
"""

from __future__ import annotations

import uuid
from datetime import datetime  # noqa: TC003

from sqlalchemy import DateTime, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from src.database.models.base import Base


class GitHubRepoDefault(Base):
    """Presence of a row = this worker is the default for ``owner/repo``."""

    __tablename__ = "github_repo_defaults"
    __table_args__ = (UniqueConstraint("backend_id", "repo_owner", "repo_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    backend_id: Mapped[str] = mapped_column(String(36), nullable=False)
    repo_owner: Mapped[str] = mapped_column(String(255), nullable=False)
    repo_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
