"""Telemetry service — records per-turn and per-tool-call timing and token usage.

Phases:
  Phase 1 — raw event capture: SessionTurn and ToolCall rows.
  Phase 2 — minutely aggregation into TelemetryMinutely for fast time-series queries.
  Phase 3 — retention cleanup of rows older than TELEMETRY_RETENTION_DAYS.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.models.db import Session as SessionModel
from src.models.db import SessionTurn, ToolCall, TelemetryMinutely

if TYPE_CHECKING:
    from src.core.llm import TurnUsage

logger = logging.getLogger(__name__)

# Cap inter-turn gap at 30 minutes when computing averages to exclude idle overnight gaps.
_INTER_TURN_GAP_CAP_SECONDS = 30 * 60


@dataclass
class InFlightTurn:
    """Lightweight in-memory handle for a turn being recorded."""

    id: uuid.UUID
    session_id: str
    backend_id: str
    turn_index: int
    ts_start: datetime
    ts_first_token: datetime | None = None


@dataclass
class InFlightToolCall:
    """Lightweight in-memory handle for a tool call being recorded."""

    id: uuid.UUID
    session_id: str
    ts_start: datetime


class TelemetryService:
    """Records telemetry events and aggregates them into minutely buckets.

    Wire this into app.state at startup and pass it into PromptRouter so it
    can call record_turn_* and record_tool_* at execution boundaries.
    """

    def __init__(
        self,
        db_factory: async_sessionmaker[AsyncSession],
        backend_id: str,
        retention_days: int = 90,
    ) -> None:
        self._db_factory = db_factory
        self._backend_id = backend_id
        self._retention_days = retention_days
        # Per-session turn counter so callers don't need to query the DB.
        self._turn_counters: dict[str, int] = {}
        # Watermark: last ts_start processed for aggregation (exclusive lower bound).
        self._aggregation_watermark: datetime | None = None

    # ------------------------------------------------------------------
    # Session stub guard
    # ------------------------------------------------------------------

    async def _ensure_session_stub(self, session_id: str, ts_now: datetime) -> None:
        """Ensure a minimal sessions row exists so FK constraints are satisfied.

        session_turns and tool_calls both reference sessions.id via a FK.
        Sessions are normally archived to the DB only after completion, but
        telemetry rows are written during the active lifetime of the session.
        This method pre-creates a minimal stub so the FK is satisfied from the
        first telemetry insert.  The stub is always superseded by the full row
        written by archive_session on session completion.

        If a row already exists (the common case, created by
        _ensure_session_row_in_db in BackgroundTasksMixin) this is a cheap
        no-op — one PK lookup and no write.

        Exceptions are logged and swallowed; callers must still handle the FK
        failure from the subsequent insert if the stub could not be created.
        """
        session_uuid = uuid.UUID(session_id)
        try:
            async with self._db_factory() as db:
                existing = await db.get(SessionModel, session_uuid)
                if existing is None:
                    db.add(SessionModel(
                        id=session_uuid,
                        backend_id=self._backend_id,
                        created_at=ts_now,
                        session_type="conversational",
                        status="active",
                        metadata_={},
                    ))
                    await db.commit()
                    logger.debug(
                        "TelemetryService: created session stub for %s (will be "
                        "superseded by archive_session on completion)",
                        session_id,
                    )
        except Exception:
            logger.exception(
                "TelemetryService: failed to ensure session stub for %s", session_id
            )

    # ------------------------------------------------------------------
    # Turn tracking
    # ------------------------------------------------------------------

    async def record_turn_start(
        self,
        session_id: str,
        turn_index: int | None = None,
    ) -> InFlightTurn:
        """Insert a new SessionTurn row and return an in-flight handle.

        If *turn_index* is None, an auto-incrementing per-session index is used.

        Before inserting the turn row the method calls :meth:`_ensure_session_stub`
        so that the sessions FK-target always exists even when the normal
        _ensure_session_row_in_db path in BackgroundTasksMixin failed silently.
        """
        if turn_index is None:
            turn_index = self._turn_counters.get(session_id, 0)

        ts_start = datetime.now(UTC)
        row_id = uuid.uuid4()

        # Guarantee the sessions row exists before inserting the FK child row.
        await self._ensure_session_stub(session_id, ts_start)

        try:
            async with self._db_factory() as db:
                db.add(
                    SessionTurn(
                        id=row_id,
                        session_id=uuid.UUID(session_id),
                        backend_id=self._backend_id,
                        turn_index=turn_index,
                        ts_start=ts_start,
                        provider=None,
                    )
                )
                await db.commit()
        except Exception:
            logger.exception("TelemetryService: failed to record turn start for session %s", session_id)

        return InFlightTurn(
            id=row_id,
            session_id=session_id,
            backend_id=self._backend_id,
            turn_index=turn_index,
            ts_start=ts_start,
        )

    async def record_first_token(self, turn: InFlightTurn) -> None:
        """Set ts_first_token on the turn (idempotent — only records the first call)."""
        if turn.ts_first_token is not None:
            return
        ts = datetime.now(UTC)
        turn.ts_first_token = ts
        try:
            async with self._db_factory() as db:
                row = await db.get(SessionTurn, turn.id)
                if row is not None and row.ts_first_token is None:
                    row.ts_first_token = ts
                    await db.commit()
        except Exception:
            logger.exception("TelemetryService: failed to record first token for turn %s", turn.id)

    async def record_turn_end(self, turn: InFlightTurn, usage: "TurnUsage") -> None:
        """Close out a turn with token counts and timing from the LLM response."""
        ts_end = datetime.now(UTC)
        duration_ms = int((ts_end - turn.ts_start).total_seconds() * 1000)
        # Advance the per-session turn counter for subsequent turns.
        self._turn_counters[turn.session_id] = turn.turn_index + 1
        try:
            async with self._db_factory() as db:
                row = await db.get(SessionTurn, turn.id)
                if row is not None:
                    row.ts_end = ts_end
                    row.llm_duration_ms = duration_ms
                    row.input_tokens = usage.input_tokens
                    row.output_tokens = usage.output_tokens
                    row.cache_creation_tokens = usage.cache_creation_input_tokens
                    row.cache_read_tokens = usage.cache_read_input_tokens
                    row.model = usage.model
                    row.provider = None  # filled from LLM provider context if needed
                    await db.commit()
        except Exception:
            logger.exception("TelemetryService: failed to record turn end for turn %s", turn.id)

    async def mark_turn_interrupted(self, turn: InFlightTurn) -> None:
        """Mark a turn as interrupted (e.g. session cancelled mid-stream)."""
        try:
            async with self._db_factory() as db:
                row = await db.get(SessionTurn, turn.id)
                if row is not None:
                    row.interrupted = True
                    row.ts_end = datetime.now(UTC)
                    await db.commit()
        except Exception:
            logger.exception("TelemetryService: failed to mark turn interrupted for turn %s", turn.id)

    # ------------------------------------------------------------------
    # Tool call tracking
    # ------------------------------------------------------------------

    async def record_tool_start(
        self,
        session_id: str,
        tool_name: str,
        executor_type: str,
        turn: InFlightTurn | None = None,
        tool_call_index: int = 0,
    ) -> InFlightToolCall:
        """Insert a new ToolCall row and return an in-flight handle.

        Calls :meth:`_ensure_session_stub` defensively — in the normal flow a
        sessions row was already created by :meth:`record_turn_start`, but tool
        calls can arrive without a preceding turn (e.g. direct tool mode), so
        the guard is applied here too.
        """
        ts_start = datetime.now(UTC)
        row_id = uuid.uuid4()

        await self._ensure_session_stub(session_id, ts_start)

        try:
            async with self._db_factory() as db:
                db.add(
                    ToolCall(
                        id=row_id,
                        session_id=uuid.UUID(session_id),
                        turn_id=turn.id if turn is not None else None,
                        backend_id=self._backend_id,
                        turn_index=turn.turn_index if turn is not None else None,
                        tool_call_index=tool_call_index,
                        tool_name=tool_name,
                        ts_start=ts_start,
                        status="ok",
                        executor_type=executor_type,
                    )
                )
                await db.commit()
        except Exception:
            logger.exception(
                "TelemetryService: failed to record tool start for %s in session %s",
                tool_name,
                session_id,
            )

        return InFlightToolCall(id=row_id, session_id=session_id, ts_start=ts_start)

    async def record_tool_end(
        self,
        tool_call: InFlightToolCall,
        status: str = "ok",
        error: str | None = None,
    ) -> None:
        """Close out a tool call row with timing and status."""
        ts_end = datetime.now(UTC)
        duration_ms = int((ts_end - tool_call.ts_start).total_seconds() * 1000)
        try:
            async with self._db_factory() as db:
                row = await db.get(ToolCall, tool_call.id)
                if row is not None:
                    row.ts_end = ts_end
                    row.duration_ms = duration_ms
                    row.status = status
                    row.error_message = error
                    await db.commit()
        except Exception:
            logger.exception(
                "TelemetryService: failed to record tool end for tool_call %s", tool_call.id
            )

    # ------------------------------------------------------------------
    # Minutely aggregation
    # ------------------------------------------------------------------

    async def aggregate_pending(self) -> None:
        """Aggregate new session_turns and tool_calls into telemetry_minutely.

        Reads rows with ts_start > watermark (exclusive) and upserts minute
        buckets for both the per-session and global (session_id=NULL) rollup.
        Runs in a background task every 60 seconds.
        """
        try:
            await self._run_aggregation()
        except Exception:
            logger.exception("TelemetryService: aggregation failed")

    async def _run_aggregation(self) -> None:
        watermark = self._aggregation_watermark

        async with self._db_factory() as db:
            # ---- Turn data ----
            stmt = select(SessionTurn).where(
                SessionTurn.backend_id == self._backend_id,
                SessionTurn.ts_end.is_not(None),
                SessionTurn.interrupted.is_(False),
            )
            if watermark is not None:
                stmt = stmt.where(SessionTurn.ts_start > watermark)
            turns = (await db.execute(stmt)).scalars().all()

            # ---- Tool call data ----
            stmt2 = select(ToolCall).where(
                ToolCall.backend_id == self._backend_id,
                ToolCall.ts_end.is_not(None),
            )
            if watermark is not None:
                stmt2 = stmt2.where(ToolCall.ts_start > watermark)
            tool_calls = (await db.execute(stmt2)).scalars().all()

            if not turns and not tool_calls:
                return

            new_watermark = watermark

            # Aggregate turns into per-minute buckets
            for turn in turns:
                bucket = turn.ts_start.replace(second=0, microsecond=0)
                duration_us = (turn.llm_duration_ms or 0) * 1000
                session_id_str = str(turn.session_id)

                for sid in (session_id_str, None):  # per-session + global
                    await self._upsert_bucket(
                        db,
                        backend_id=self._backend_id,
                        bucket=bucket,
                        session_id=uuid.UUID(session_id_str) if sid is not None else None,
                        tokens_sent_delta=turn.input_tokens,
                        tokens_received_delta=turn.output_tokens,
                        cache_creation_delta=turn.cache_creation_tokens,
                        cache_read_delta=turn.cache_read_tokens,
                        llm_duration_us_delta=duration_us,
                        llm_count_delta=1 if duration_us > 0 else 0,
                        turn_count_delta=1,
                    )

                if new_watermark is None or turn.ts_start > new_watermark:
                    new_watermark = turn.ts_start

            # Aggregate tool calls into per-minute buckets
            for tc in tool_calls:
                if tc.duration_ms is None:
                    continue
                bucket = tc.ts_start.replace(second=0, microsecond=0)
                duration_us = tc.duration_ms * 1000
                session_id_str = str(tc.session_id)
                error_delta = 1 if tc.status == "error" else 0

                for sid in (session_id_str, None):
                    await self._upsert_bucket(
                        db,
                        backend_id=self._backend_id,
                        bucket=bucket,
                        session_id=uuid.UUID(session_id_str) if sid is not None else None,
                        tool_duration_us_delta=duration_us,
                        tool_count_delta=1,
                        tool_call_count_delta=1,
                        error_count_delta=error_delta,
                    )

                if new_watermark is None or tc.ts_start > new_watermark:
                    new_watermark = tc.ts_start

            await db.commit()

        if new_watermark is not None:
            self._aggregation_watermark = new_watermark

    async def _upsert_bucket(
        self,
        db: AsyncSession,
        *,
        backend_id: str,
        bucket: datetime,
        session_id: uuid.UUID | None,
        tokens_sent_delta: int = 0,
        tokens_received_delta: int = 0,
        cache_creation_delta: int = 0,
        cache_read_delta: int = 0,
        llm_duration_us_delta: int = 0,
        llm_count_delta: int = 0,
        tool_duration_us_delta: int = 0,
        tool_count_delta: int = 0,
        inter_tool_gap_us_delta: int = 0,
        inter_tool_gap_count_delta: int = 0,
        inter_turn_gap_us_delta: int = 0,
        inter_turn_gap_count_delta: int = 0,
        turn_count_delta: int = 0,
        tool_call_count_delta: int = 0,
        error_count_delta: int = 0,
        parallel_delta: int = 0,
    ) -> None:
        """SELECT-then-INSERT-or-UPDATE a telemetry_minutely row (DB-agnostic upsert)."""
        stmt = select(TelemetryMinutely).where(
            TelemetryMinutely.backend_id == backend_id,
            TelemetryMinutely.bucket == bucket,
            TelemetryMinutely.session_id == session_id,
        )
        row = (await db.execute(stmt)).scalar_one_or_none()

        if row is None:
            row = TelemetryMinutely(
                backend_id=backend_id,
                bucket=bucket,
                session_id=session_id,
            )
            db.add(row)

        # Use `(field or 0) + delta` instead of `+=` because SQLAlchemy's column
        # `default=0` is applied at INSERT flush time, not at object construction.
        # A newly-added but unflushed row has None for all counter attributes.
        row.tokens_sent = (row.tokens_sent or 0) + tokens_sent_delta
        row.tokens_received = (row.tokens_received or 0) + tokens_received_delta
        row.cache_creation = (row.cache_creation or 0) + cache_creation_delta
        row.cache_read = (row.cache_read or 0) + cache_read_delta
        row.llm_duration_sum_us = (row.llm_duration_sum_us or 0) + llm_duration_us_delta
        row.llm_duration_count = (row.llm_duration_count or 0) + llm_count_delta
        row.tool_duration_sum_us = (row.tool_duration_sum_us or 0) + tool_duration_us_delta
        row.tool_duration_count = (row.tool_duration_count or 0) + tool_count_delta
        row.inter_tool_gap_sum_us = (row.inter_tool_gap_sum_us or 0) + inter_tool_gap_us_delta
        row.inter_tool_gap_count = (row.inter_tool_gap_count or 0) + inter_tool_gap_count_delta
        row.inter_turn_gap_sum_us = (row.inter_turn_gap_sum_us or 0) + inter_turn_gap_us_delta
        row.inter_turn_gap_count = (row.inter_turn_gap_count or 0) + inter_turn_gap_count_delta
        row.turn_count = (row.turn_count or 0) + turn_count_delta
        row.tool_call_count = (row.tool_call_count or 0) + tool_call_count_delta
        row.error_count = (row.error_count or 0) + error_count_delta
        row.parallel_tool_calls = (row.parallel_tool_calls or 0) + parallel_delta

    # ------------------------------------------------------------------
    # Retention cleanup
    # ------------------------------------------------------------------

    async def cleanup_old_records(self) -> None:
        """Delete telemetry rows older than TELEMETRY_RETENTION_DAYS."""
        cutoff = datetime.now(UTC) - timedelta(days=self._retention_days)
        try:
            async with self._db_factory() as db:
                from sqlalchemy import delete  # noqa: PLC0415
                await db.execute(
                    delete(SessionTurn).where(SessionTurn.ts_start < cutoff)
                )
                await db.execute(
                    delete(ToolCall).where(ToolCall.ts_start < cutoff)
                )
                await db.execute(
                    delete(TelemetryMinutely).where(TelemetryMinutely.bucket < cutoff)
                )
                await db.commit()
                logger.info("TelemetryService: pruned records older than %s", cutoff.date())
        except Exception:
            logger.exception("TelemetryService: cleanup failed")
