"""initial schema (squashed)

Squashes all previous migrations into a single initial migration that
creates the full database schema in one shot.

Revision ID: 0001
Revises:
Create Date: 2026-04-09 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the full initial schema."""
    # --- api_keys ---
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key_hash", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )

    # --- sessions ---
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("backend_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("session_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.Column("main_project_path", sa.String(length=1024), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("conversation_history", sa.JSON(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_cost_usd", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("interrupted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restart_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_backend_id", "sessions", ["backend_id"])

    # --- session_messages ---
    op.create_table(
        "session_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("message_type", sa.String(length=30), nullable=False),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "sequence"),
    )

    # --- tool_executions ---
    op.create_table(
        "tool_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column("tool_input", sa.JSON(), nullable=False),
        sa.Column("tool_output", sa.Text(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- llm_calls ---
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("model", sa.String(length=255), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_creation_input_tokens", sa.Integer(), nullable=False),
        sa.Column("cache_read_input_tokens", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stop_reason", sa.String(length=50), nullable=False),
        sa.Column("has_tool_calls", sa.Boolean(), nullable=False),
        sa.Column("request_messages", sa.JSON(), nullable=False),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("service_tier", sa.String(length=50), nullable=True),
        sa.Column("inference_geo", sa.String(length=100), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_calls_session_id", "llm_calls", ["session_id"])
    op.create_index("ix_llm_calls_started_at", "llm_calls", ["started_at"])

    # --- artifacts ---
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("backend_id", sa.String(length=36), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_name", sa.String(length=500), nullable=False),
        sa.Column("file_extension", sa.String(length=50), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=True),
        sa.Column("file_exists", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("backend_id", "file_path"),
    )
    op.create_index("ix_artifacts_backend_id", "artifacts", ["backend_id"])
    op.create_index("ix_artifacts_session_id", "artifacts", ["session_id"])

    # --- tasks ---
    op.create_table(
        "tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("backend_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("plan_artifact_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["plan_artifact_id"], ["artifacts.id"], ondelete="SET NULL", name="fk_tasks_plan_artifact_id"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_backend_id", "tasks", ["backend_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])

    # --- task_sessions ---
    op.create_table(
        "task_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column(
            "attached_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "session_id"),
    )

    # --- linear_issues ---
    op.create_table(
        "linear_issues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("backend_id", sa.String(length=36), nullable=False),
        sa.Column("linear_id", sa.String(length=255), nullable=False),
        sa.Column("identifier", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state_name", sa.String(length=100), nullable=False),
        sa.Column("state_type", sa.String(length=30), nullable=False),
        sa.Column("assignee_id", sa.String(length=255), nullable=True),
        sa.Column("assignee_name", sa.String(length=255), nullable=True),
        sa.Column("team_id", sa.String(length=255), nullable=False),
        sa.Column("team_name", sa.String(length=255), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("labels", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("backend_id", "linear_id"),
    )
    op.create_index("ix_linear_issues_backend_id", "linear_issues", ["backend_id"])
    op.create_index("ix_linear_issues_state_type", "linear_issues", ["state_type"])

    # --- session_turns ---
    op.create_table(
        "session_turns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("backend_id", sa.String(length=36), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=False),
        sa.Column("ts_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ts_first_token", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ts_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("llm_duration_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_creation_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("interrupted", sa.Boolean(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_session_turns_session_id", "session_turns", ["session_id"])
    op.create_index("idx_session_turns_backend_id_ts", "session_turns", ["backend_id", "ts_start"])

    # --- tool_calls ---
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=True),
        sa.Column("backend_id", sa.String(length=36), nullable=False),
        sa.Column("turn_index", sa.Integer(), nullable=True),
        sa.Column("tool_call_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_name", sa.String(length=255), nullable=False),
        sa.Column("ts_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ts_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ok"),
        sa.Column("executor_type", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["turn_id"], ["session_turns.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_tool_calls_session_id", "tool_calls", ["session_id"])
    op.create_index("idx_tool_calls_backend_id_ts", "tool_calls", ["backend_id", "ts_start"])
    op.create_index("idx_tool_calls_tool_name", "tool_calls", ["backend_id", "tool_name"])

    # --- telemetry_minutely ---
    op.create_table(
        "telemetry_minutely",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("backend_id", sa.String(length=36), nullable=False),
        sa.Column("bucket", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.Uuid(), nullable=True),
        sa.Column("tokens_sent", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tokens_received", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cache_creation", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cache_read", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("llm_duration_sum_us", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("llm_duration_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_duration_sum_us", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("tool_duration_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inter_tool_gap_sum_us", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("inter_tool_gap_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inter_turn_gap_sum_us", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("inter_turn_gap_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tool_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parallel_tool_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("backend_id", "bucket", "session_id", name="uq_telemetry_minutely"),
    )
    op.create_index("idx_telemetry_minutely_lookup", "telemetry_minutely", ["backend_id", "bucket", "session_id"])


def downgrade() -> None:
    """Drop all tables."""
    op.drop_index("idx_telemetry_minutely_lookup", table_name="telemetry_minutely")
    op.drop_table("telemetry_minutely")

    op.drop_index("idx_tool_calls_tool_name", table_name="tool_calls")
    op.drop_index("idx_tool_calls_backend_id_ts", table_name="tool_calls")
    op.drop_index("idx_tool_calls_session_id", table_name="tool_calls")
    op.drop_table("tool_calls")

    op.drop_index("idx_session_turns_backend_id_ts", table_name="session_turns")
    op.drop_index("idx_session_turns_session_id", table_name="session_turns")
    op.drop_table("session_turns")

    op.drop_index("ix_linear_issues_state_type", table_name="linear_issues")
    op.drop_index("ix_linear_issues_backend_id", table_name="linear_issues")
    op.drop_table("linear_issues")

    op.drop_table("task_sessions")

    op.drop_index("ix_tasks_status", table_name="tasks")
    op.drop_index("ix_tasks_backend_id", table_name="tasks")
    op.drop_table("tasks")

    op.drop_index("ix_artifacts_session_id", table_name="artifacts")
    op.drop_index("ix_artifacts_backend_id", table_name="artifacts")
    op.drop_table("artifacts")

    op.drop_index("ix_llm_calls_started_at", table_name="llm_calls")
    op.drop_index("ix_llm_calls_session_id", table_name="llm_calls")
    op.drop_table("llm_calls")

    op.drop_table("tool_executions")
    op.drop_table("session_messages")

    op.drop_index("ix_sessions_backend_id", table_name="sessions")
    op.drop_table("sessions")

    op.drop_table("api_keys")
