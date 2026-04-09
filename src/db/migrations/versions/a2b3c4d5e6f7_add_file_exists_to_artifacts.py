"""Add file_exists column to artifacts table

Revision ID: a2b3c4d5e6f7
Revises: b2c3d4e5f6a8
Create Date: 2026-04-08 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2b3c4d5e6f7"
down_revision: str | Sequence[str] | None = "b2c3d4e5f6a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add file_exists column to artifacts, defaulting existing rows to true."""
    op.add_column(
        "artifacts",
        sa.Column("file_exists", sa.Boolean(), nullable=False, server_default="true"),
    )


def downgrade() -> None:
    """Remove file_exists column from artifacts."""
    op.drop_column("artifacts", "file_exists")
