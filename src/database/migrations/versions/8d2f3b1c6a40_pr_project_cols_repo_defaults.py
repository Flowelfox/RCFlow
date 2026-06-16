"""add PR project columns + github_repo_defaults.

Revision ID: 8d2f3b1c6a40
Revises: 7c1e2a9f4b30
Create Date: 2026-06-05 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8d2f3b1c6a40"
down_revision: str | Sequence[str] | None = "7c1e2a9f4b30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("github_prs", sa.Column("project_name", sa.String(length=255), nullable=True))
    op.add_column("github_prs", sa.Column("project_path", sa.Text(), nullable=True))
    op.create_table(
        "github_repo_defaults",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("backend_id", sa.String(length=36), nullable=False),
        sa.Column("repo_owner", sa.String(length=255), nullable=False),
        sa.Column("repo_name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("backend_id", "repo_owner", "repo_name"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("github_repo_defaults")
    op.drop_column("github_prs", "project_path")
    op.drop_column("github_prs", "project_name")
