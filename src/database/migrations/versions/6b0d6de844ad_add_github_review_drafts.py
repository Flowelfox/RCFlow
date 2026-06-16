"""add github_review_drafts.

Revision ID: 6b0d6de844ad
Revises: 6e7e78abc3a2
Create Date: 2026-06-04 18:11:13.189689

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6b0d6de844ad"
down_revision: str | Sequence[str] | None = "6e7e78abc3a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "github_review_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("backend_id", sa.String(length=36), nullable=False),
        sa.Column("pr_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(length=20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("comments", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.ForeignKeyConstraint(["pr_id"], ["github_prs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("backend_id", "pr_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("github_review_drafts")
