"""add tasks table

Revision ID: ea58dc65fd1f
Revises: fea687bf3218
Create Date: 2026-03-08 02:06:57.901726

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ea58dc65fd1f'
down_revision: Union[str, Sequence[str], None] = 'fea687bf3218'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create tasks table
    op.create_table(
        'tasks',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('backend_id', sa.String(length=36), nullable=False),
        sa.Column('title', sa.String(length=300), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('source', sa.String(length=10), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_tasks_backend_id', 'tasks', ['backend_id'], unique=False)
    op.create_index('ix_tasks_status', 'tasks', ['status'], unique=False)

    # Create task_sessions association table
    op.create_table(
        'task_sessions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('task_id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('attached_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('task_id', 'session_id')
    )


def downgrade() -> None:
    """Downgrade schema."""
    # Drop task_sessions table
    op.drop_table('task_sessions')

    # Drop indexes and tasks table
    op.drop_index('ix_tasks_status', table_name='tasks')
    op.drop_index('ix_tasks_backend_id', table_name='tasks')
    op.drop_table('tasks')
