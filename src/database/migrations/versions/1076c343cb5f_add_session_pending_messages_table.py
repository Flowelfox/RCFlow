"""add session_pending_messages table

Revision ID: 1076c343cb5f
Revises: b3d61d879884
Create Date: 2026-04-22 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "1076c343cb5f"
down_revision: str | Sequence[str] | None = "b3d61d879884"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create session_pending_messages table — one row per queued user message."""
    op.create_table(
        "session_pending_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("queued_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("display_content", sa.Text(), nullable=False),
        sa.Column("attachments_path", sa.Text(), nullable=True),
        sa.Column("project_name", sa.Text(), nullable=True),
        sa.Column("selected_worktree_path", sa.Text(), nullable=True),
        sa.Column("task_id", sa.String(length=36), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("queued_id"),
    )
    op.create_index(
        op.f("ix_session_pending_messages_session_id"),
        "session_pending_messages",
        ["session_id"],
        unique=False,
    )
    op.create_index(
        "ix_pending_session_position",
        "session_pending_messages",
        ["session_id", "position"],
        unique=False,
    )


def downgrade() -> None:
    """Drop session_pending_messages table."""
    op.drop_index("ix_pending_session_position", table_name="session_pending_messages")
    op.drop_index(op.f("ix_session_pending_messages_session_id"), table_name="session_pending_messages")
    op.drop_table("session_pending_messages")
