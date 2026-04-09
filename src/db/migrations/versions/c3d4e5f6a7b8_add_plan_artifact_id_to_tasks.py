"""Add plan_artifact_id to tasks table

Revision ID: c3d4e5f6a7b8
Revises: a2b3c4d5e6f7
Create Date: 2026-04-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add plan_artifact_id nullable FK column to tasks."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("tasks")}

    if "plan_artifact_id" not in existing_columns:
        op.add_column(
            "tasks",
            sa.Column("plan_artifact_id", sa.Uuid(), nullable=True),
        )
        op.create_foreign_key(
            "fk_tasks_plan_artifact_id",
            "tasks",
            "artifacts",
            ["plan_artifact_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    """Remove plan_artifact_id from tasks."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("tasks")}

    if "plan_artifact_id" in existing_columns:
        op.drop_constraint("fk_tasks_plan_artifact_id", "tasks", type_="foreignkey")
        op.drop_column("tasks", "plan_artifact_id")
