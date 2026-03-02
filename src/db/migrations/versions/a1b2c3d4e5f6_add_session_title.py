"""add_session_title

Revision ID: a1b2c3d4e5f6
Revises: f548e93d3e9e
Create Date: 2026-02-27 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f548e93d3e9e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add title column to sessions table."""
    op.add_column("sessions", sa.Column("title", sa.String(length=200), nullable=True))


def downgrade() -> None:
    """Remove title column from sessions table."""
    op.drop_column("sessions", "title")
