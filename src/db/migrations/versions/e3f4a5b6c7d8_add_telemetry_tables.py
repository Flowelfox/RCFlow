"""Add telemetry tables: session_turns, tool_calls, telemetry_minutely

Revision ID: e3f4a5b6c7d8
Revises: d2e3f4a5b6c7
Create Date: 2026-03-18 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e3f4a5b6c7d8'
down_revision: Union[str, Sequence[str], None] = 'd2e3f4a5b6c7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add session_turns, tool_calls, and telemetry_minutely tables."""
    op.create_table(
        'session_turns',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('backend_id', sa.String(length=36), nullable=False),
        sa.Column('turn_index', sa.Integer(), nullable=False),
        sa.Column('ts_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ts_first_token', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ts_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('llm_duration_ms', sa.Integer(), nullable=True),
        sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cache_creation_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cache_read_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('model', sa.String(length=255), nullable=True),
        sa.Column('provider', sa.String(length=50), nullable=True),
        sa.Column('interrupted', sa.Boolean(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_session_turns_session_id', 'session_turns', ['session_id'])
    op.create_index('idx_session_turns_backend_id_ts', 'session_turns', ['backend_id', 'ts_start'])

    op.create_table(
        'tool_calls',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('turn_id', sa.Uuid(), nullable=True),
        sa.Column('backend_id', sa.String(length=36), nullable=False),
        sa.Column('turn_index', sa.Integer(), nullable=True),
        sa.Column('tool_call_index', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tool_name', sa.String(length=255), nullable=False),
        sa.Column('ts_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ts_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='ok'),
        sa.Column('executor_type', sa.String(length=50), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['turn_id'], ['session_turns.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_tool_calls_session_id', 'tool_calls', ['session_id'])
    op.create_index('idx_tool_calls_backend_id_ts', 'tool_calls', ['backend_id', 'ts_start'])
    op.create_index('idx_tool_calls_tool_name', 'tool_calls', ['backend_id', 'tool_name'])

    op.create_table(
        'telemetry_minutely',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('backend_id', sa.String(length=36), nullable=False),
        sa.Column('bucket', sa.DateTime(timezone=True), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=True),
        sa.Column('tokens_sent', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('tokens_received', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('cache_creation', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('cache_read', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('llm_duration_sum_us', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('llm_duration_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tool_duration_sum_us', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('tool_duration_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('inter_tool_gap_sum_us', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('inter_tool_gap_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('inter_turn_gap_sum_us', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('inter_turn_gap_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('turn_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tool_call_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('error_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('parallel_tool_calls', sa.Integer(), nullable=False, server_default='0'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('backend_id', 'bucket', 'session_id', name='uq_telemetry_minutely'),
    )
    op.create_index('idx_telemetry_minutely_lookup', 'telemetry_minutely', ['backend_id', 'bucket', 'session_id'])


def downgrade() -> None:
    """Drop telemetry tables."""
    op.drop_index('idx_telemetry_minutely_lookup', table_name='telemetry_minutely')
    op.drop_table('telemetry_minutely')
    op.drop_index('idx_tool_calls_tool_name', table_name='tool_calls')
    op.drop_index('idx_tool_calls_backend_id_ts', table_name='tool_calls')
    op.drop_index('idx_tool_calls_session_id', table_name='tool_calls')
    op.drop_table('tool_calls')
    op.drop_index('idx_session_turns_backend_id_ts', table_name='session_turns')
    op.drop_index('idx_session_turns_session_id', table_name='session_turns')
    op.drop_table('session_turns')
