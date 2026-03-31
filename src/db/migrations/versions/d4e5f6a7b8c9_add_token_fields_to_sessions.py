"""add_token_fields_to_sessions

Revision ID: d4e5f6a7b8c9
Revises: 3e86fc9970cb
Create Date: 2026-03-08 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "3e86fc9970cb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("sessions", sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column(
        "sessions",
        sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("sessions", sa.Column("cache_read_input_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("sessions", sa.Column("tool_input_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("sessions", sa.Column("tool_output_tokens", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("sessions", sa.Column("tool_cost_usd", sa.Float(), nullable=False, server_default="0.0"))


def downgrade() -> None:
    op.drop_column("sessions", "tool_cost_usd")
    op.drop_column("sessions", "tool_output_tokens")
    op.drop_column("sessions", "tool_input_tokens")
    op.drop_column("sessions", "cache_read_input_tokens")
    op.drop_column("sessions", "cache_creation_input_tokens")
    op.drop_column("sessions", "output_tokens")
    op.drop_column("sessions", "input_tokens")
