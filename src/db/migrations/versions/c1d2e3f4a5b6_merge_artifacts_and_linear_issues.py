"""Merge artifacts and linear_issues migration heads

Revision ID: c1d2e3f4a5b6
Revises: a1b2c3d4e5f7, b1c2d3e4f5a6
Create Date: 2026-03-17 12:00:00.000000

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = 'c1d2e3f4a5b6'
down_revision: str | Sequence[str] | None = ('a1b2c3d4e5f7', 'b1c2d3e4f5a6')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
