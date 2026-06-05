"""add review_decision + merge_status to github_prs.

Revision ID: 7c1e2a9f4b30
Revises: 6b0d6de844ad
Create Date: 2026-06-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c1e2a9f4b30"
down_revision: str | Sequence[str] | None = "6b0d6de844ad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("github_prs", sa.Column("review_decision", sa.String(length=20), nullable=True))
    op.add_column("github_prs", sa.Column("merge_status", sa.String(length=20), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("github_prs", "merge_status")
    op.drop_column("github_prs", "review_decision")
