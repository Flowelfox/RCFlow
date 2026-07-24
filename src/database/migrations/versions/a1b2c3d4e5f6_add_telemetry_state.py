"""add telemetry_state (persisted aggregation watermark).

Revision ID: a1b2c3d4e5f6
Revises: 8d2f3b1c6a40
Create Date: 2026-07-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "8d2f3b1c6a40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "telemetry_state",
        sa.Column("backend_id", sa.String(length=36), nullable=False),
        sa.Column("aggregation_watermark", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("backend_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("telemetry_state")
