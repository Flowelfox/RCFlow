"""merge heads

Revision ID: fea687bf3218
Revises: c9d5e1f3a7b2, d4e5f6a7b8c9
Create Date: 2026-03-08 01:09:48.484948

"""
from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = 'fea687bf3218'
down_revision: str | Sequence[str] | None = ('c9d5e1f3a7b2', 'd4e5f6a7b8c9')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
