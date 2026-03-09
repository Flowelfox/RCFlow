"""Add artifacts table

Revision ID: a1b2c3d4e5f7
Revises: ea58dc65fd1f
Create Date: 2026-03-08 02:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f7'
down_revision: Union[str, None] = 'ea58dc65fd1f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create artifacts table
    op.create_table('artifacts',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('backend_id', sa.String(length=36), nullable=False),
        sa.Column('file_path', sa.Text(), nullable=False),
        sa.Column('file_name', sa.String(length=500), nullable=False),
        sa.Column('file_extension', sa.String(length=50), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('mime_type', sa.String(length=100), nullable=True),
        sa.Column('discovered_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('modified_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('backend_id', 'file_path')
    )
    op.create_index('ix_artifacts_backend_id', 'artifacts', ['backend_id'], unique=False)
    op.create_index('ix_artifacts_session_id', 'artifacts', ['session_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_artifacts_session_id', table_name='artifacts')
    op.drop_index('ix_artifacts_backend_id', table_name='artifacts')
    op.drop_table('artifacts')