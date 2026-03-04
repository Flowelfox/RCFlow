"""add_conversation_history_to_sessions

Revision ID: b7a4f2e8c1d3
Revises: 3e86fc9970cb
Create Date: 2026-02-28 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7a4f2e8c1d3"
down_revision: str | Sequence[str] | None = "3e86fc9970cb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add conversation_history JSONB column to sessions table."""
    op.add_column("sessions", sa.Column("conversation_history", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove conversation_history column from sessions table."""
    op.drop_column("sessions", "conversation_history")
