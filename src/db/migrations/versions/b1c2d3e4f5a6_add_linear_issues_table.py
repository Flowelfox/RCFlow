"""add linear_issues table

Revision ID: b1c2d3e4f5a6
Revises: ea58dc65fd1f
Create Date: 2026-03-17 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: str | Sequence[str] | None = 'ea58dc65fd1f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create linear_issues table."""
    op.create_table(
        'linear_issues',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('backend_id', sa.String(length=36), nullable=False),
        sa.Column('linear_id', sa.String(length=255), nullable=False),
        sa.Column('identifier', sa.String(length=50), nullable=False),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('state_name', sa.String(length=100), nullable=False),
        sa.Column('state_type', sa.String(length=30), nullable=False),
        sa.Column('assignee_id', sa.String(length=255), nullable=True),
        sa.Column('assignee_name', sa.String(length=255), nullable=True),
        sa.Column('team_id', sa.String(length=255), nullable=False),
        sa.Column('team_name', sa.String(length=255), nullable=True),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('labels', sa.Text(), nullable=False, server_default='[]'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            'synced_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('(CURRENT_TIMESTAMP)'),
            nullable=False,
        ),
        sa.Column('task_id', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('backend_id', 'linear_id'),
    )
    op.create_index('ix_linear_issues_backend_id', 'linear_issues', ['backend_id'], unique=False)
    op.create_index('ix_linear_issues_state_type', 'linear_issues', ['state_type'], unique=False)


def downgrade() -> None:
    """Drop linear_issues table."""
    op.drop_index('ix_linear_issues_state_type', table_name='linear_issues')
    op.drop_index('ix_linear_issues_backend_id', table_name='linear_issues')
    op.drop_table('linear_issues')
