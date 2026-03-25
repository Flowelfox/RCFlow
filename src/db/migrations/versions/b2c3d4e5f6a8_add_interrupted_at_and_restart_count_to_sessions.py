"""Add interrupted_at and restart_count to sessions

Revision ID: b2c3d4e5f6a8
Revises: e3f4a5b6c7d8
Create Date: 2026-03-20 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a8'
down_revision: Union[str, Sequence[str], None] = 'e3f4a5b6c7d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add interrupted_at and restart_count columns to sessions table."""
    op.add_column(
        'sessions',
        sa.Column('interrupted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'sessions',
        sa.Column('restart_count', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    """Drop interrupted_at and restart_count columns from sessions table."""
    op.drop_column('sessions', 'restart_count')
    op.drop_column('sessions', 'interrupted_at')
