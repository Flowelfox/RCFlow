"""Add sort_order to sessions

Revision ID: e4f5a6b7c8d9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-09 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e4f5a6b7c8d9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add sort_order column to sessions table."""
    op.add_column(
        "sessions",
        sa.Column("sort_order", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    """Drop sort_order column from sessions table."""
    op.drop_column("sessions", "sort_order")
