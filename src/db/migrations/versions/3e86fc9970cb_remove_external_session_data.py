"""remove_external_session_data

Revision ID: 3e86fc9970cb
Revises: a1b2c3d4e5f6
Create Date: 2026-02-28 13:00:46.357189

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3e86fc9970cb"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Delete all external session data (messages first, then sessions)."""
    op.execute(
        "DELETE FROM session_messages WHERE session_id IN (SELECT id FROM sessions WHERE session_type = 'external')"
    )
    op.execute("DELETE FROM sessions WHERE session_type = 'external'")


def downgrade() -> None:
    """Data migration — deleted rows cannot be restored."""
