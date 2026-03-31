"""Add main_project_path to sessions

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-03-17 12:01:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd2e3f4a5b6c7'
down_revision: str | Sequence[str] | None = 'c1d2e3f4a5b6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add main_project_path column to sessions table."""
    op.add_column(
        'sessions',
        sa.Column('main_project_path', sa.String(length=1024), nullable=True),
    )


def downgrade() -> None:
    """Drop main_project_path column from sessions table."""
    op.drop_column('sessions', 'main_project_path')
