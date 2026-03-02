"""add_backend_id_to_sessions

Revision ID: c9d5e1f3a7b2
Revises: b7a4f2e8c1d3
Create Date: 2026-03-02 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9d5e1f3a7b2"
down_revision: str | Sequence[str] | None = "b7a4f2e8c1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add backend_id column to sessions table with index."""
    op.add_column("sessions", sa.Column("backend_id", sa.String(36), nullable=False, server_default=""))
    op.create_index("ix_sessions_backend_id", "sessions", ["backend_id"])


def downgrade() -> None:
    """Remove backend_id column and index from sessions table."""
    op.drop_index("ix_sessions_backend_id", table_name="sessions")
    op.drop_column("sessions", "backend_id")
