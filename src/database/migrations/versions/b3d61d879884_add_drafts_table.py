"""add drafts table

Revision ID: b3d61d879884
Revises: e4f5a6b7c8d9
Create Date: 2026-04-09 21:10:57.559099

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d61d879884"
down_revision: str | Sequence[str] | None = "e4f5a6b7c8d9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create drafts table — one unsent draft per session."""
    op.create_table(
        "drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_drafts_session_id"), "drafts", ["session_id"], unique=True)


def downgrade() -> None:
    """Drop drafts table."""
    op.drop_index(op.f("ix_drafts_session_id"), table_name="drafts")
    op.drop_table("drafts")
