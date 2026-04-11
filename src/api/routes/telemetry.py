"""Telemetry API routes — time-series metrics, per-session summaries, and global stats.

Endpoints
---------
GET /api/telemetry/summary
    Global summary for this backend: lifetime token totals, average response times,
    most-used tools.

GET /api/telemetry/worker/summary
    Worker-level aggregate stats across all sessions: turn/token/tool totals,
    avg/p95 latency, error rate, session count, and top tools.

GET /api/telemetry/sessions/{session_id}/summary
    Per-session turn table and aggregate stats (avg/p95 latency, error rate, etc.).

GET /api/telemetry/timeseries
    Bucketed time-series data with selectable zoom level (minute / hour / day)
    and optional per-session filter.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from src.db.engine import get_db_session
from src.models.db import SessionTurn, TelemetryMinutely, ToolCall

router = APIRouter(prefix="/telemetry", tags=["Telemetry"])


def _backend_id(request: Request) -> str:
    settings = request.app.state.settings
    return settings.RCFLOW_BACKEND_ID


# ------------------------------------------------------------------
# Response helpers
# ------------------------------------------------------------------


def _avg_ms(sum_us: int, count: int) -> float | None:
    if count == 0:
        return None
    return round(sum_us / count / 1000, 2)


def _p95_ms(values_ms: list[int]) -> float | None:
    if not values_ms:
        return None
    sorted_vals = sorted(values_ms)
    idx = max(0, int(len(sorted_vals) * 0.95) - 1)
    return float(sorted_vals[idx])


# ------------------------------------------------------------------
# GET /api/telemetry/summary
# ------------------------------------------------------------------


@router.get(
    "/summary",
    summary="Global telemetry summary",
    description=(
        "Returns lifetime totals and averages across all sessions for this backend: "
        "token usage, LLM and tool latency averages, most-used tools."
    ),
    response_model=dict[str, Any],
)
async def get_global_summary(
    request: Request,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Return global telemetry summary for this backend."""
    backend_id = _backend_id(request)

    # Token totals from completed turns
    token_stmt = select(
        func.sum(SessionTurn.input_tokens),
        func.sum(SessionTurn.output_tokens),
        func.sum(SessionTurn.cache_creation_tokens),
        func.sum(SessionTurn.cache_read_tokens),
        func.count(SessionTurn.id),
    ).where(
        SessionTurn.backend_id == backend_id,
        SessionTurn.ts_end.is_not(None),
        SessionTurn.interrupted.is_(False),
    )
    token_row = (await db.execute(token_stmt)).one()
    total_input, total_output, total_cache_creation, total_cache_read, turn_count = token_row

    # Average LLM duration
    llm_dur_stmt = select(SessionTurn.llm_duration_ms).where(
        SessionTurn.backend_id == backend_id,
        SessionTurn.llm_duration_ms.is_not(None),
        SessionTurn.interrupted.is_(False),
    )
    llm_durations = [r[0] for r in (await db.execute(llm_dur_stmt)).all()]
    avg_llm_ms = _p95_ms(llm_durations[: max(0, len(llm_durations) - int(len(llm_durations) * 0.05))])
    if llm_durations:
        avg_llm_ms = round(sum(llm_durations) / len(llm_durations), 2)

    # Tool stats
    tool_stmt = (
        select(
            ToolCall.tool_name,
            func.count(ToolCall.id),
            func.avg(ToolCall.duration_ms),
        )
        .where(
            ToolCall.backend_id == backend_id,
            ToolCall.ts_end.is_not(None),
        )
        .group_by(ToolCall.tool_name)
        .order_by(func.count(ToolCall.id).desc())
        .limit(10)
    )
    tool_rows = (await db.execute(tool_stmt)).all()
    top_tools = [
        {
            "tool_name": r[0],
            "call_count": r[1],
            "avg_duration_ms": round(float(r[2]), 2) if r[2] is not None else None,
        }
        for r in tool_rows
    ]

    return {
        "backend_id": backend_id,
        "turn_count": turn_count or 0,
        "total_input_tokens": int(total_input or 0),
        "total_output_tokens": int(total_output or 0),
        "total_cache_creation_tokens": int(total_cache_creation or 0),
        "total_cache_read_tokens": int(total_cache_read or 0),
        "avg_llm_duration_ms": avg_llm_ms,
        "top_tools": top_tools,
    }


# ------------------------------------------------------------------
# GET /api/telemetry/worker/summary
# ------------------------------------------------------------------


@router.get(
    "/worker/summary",
    summary="Worker-level telemetry summary",
    description=(
        "Returns aggregate statistics across all sessions for this backend/worker: "
        "session count, turn/token/tool totals, avg and p95 LLM and tool latency, "
        "error rate, and the ten most-used tools."
    ),
    response_model=dict[str, Any],
)
async def get_worker_summary(
    request: Request,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Return worker-level aggregated telemetry across all sessions."""
    backend_id = _backend_id(request)

    # Count distinct sessions
    session_count_stmt = select(func.count(func.distinct(SessionTurn.session_id))).where(
        SessionTurn.backend_id == backend_id,
    )
    session_count: int = (await db.execute(session_count_stmt)).scalar_one() or 0

    # Token/turn totals from completed, non-interrupted turns
    token_stmt = select(
        func.sum(SessionTurn.input_tokens),
        func.sum(SessionTurn.output_tokens),
        func.sum(SessionTurn.cache_creation_tokens),
        func.sum(SessionTurn.cache_read_tokens),
        func.count(SessionTurn.id),
    ).where(
        SessionTurn.backend_id == backend_id,
        SessionTurn.ts_end.is_not(None),
        SessionTurn.interrupted.is_(False),
    )
    token_row = (await db.execute(token_stmt)).one()
    total_input, total_output, total_cache_creation, total_cache_read, turn_count = token_row

    # LLM duration for avg + p95
    llm_dur_stmt = select(SessionTurn.llm_duration_ms).where(
        SessionTurn.backend_id == backend_id,
        SessionTurn.llm_duration_ms.is_not(None),
        SessionTurn.interrupted.is_(False),
    )
    llm_durations = [r[0] for r in (await db.execute(llm_dur_stmt)).all()]
    avg_llm_ms = round(sum(llm_durations) / len(llm_durations), 2) if llm_durations else None
    p95_llm_ms = _p95_ms(llm_durations)

    # Tool stats
    tool_stmt = select(ToolCall).where(
        ToolCall.backend_id == backend_id,
        ToolCall.ts_end.is_not(None),
    )
    tool_calls = (await db.execute(tool_stmt)).scalars().all()
    tool_durations = [tc.duration_ms for tc in tool_calls if tc.duration_ms is not None]
    error_count = sum(1 for tc in tool_calls if tc.status == "error")
    avg_tool_ms = round(sum(tool_durations) / len(tool_durations), 2) if tool_durations else None
    p95_tool_ms = _p95_ms(tool_durations)
    error_rate = round(error_count / len(tool_calls), 4) if tool_calls else 0.0

    # Top tools
    top_tools_stmt = (
        select(
            ToolCall.tool_name,
            func.count(ToolCall.id),
            func.avg(ToolCall.duration_ms),
        )
        .where(ToolCall.backend_id == backend_id, ToolCall.ts_end.is_not(None))
        .group_by(ToolCall.tool_name)
        .order_by(func.count(ToolCall.id).desc())
        .limit(10)
    )
    top_tools = [
        {
            "tool_name": r[0],
            "call_count": r[1],
            "avg_duration_ms": round(float(r[2]), 2) if r[2] is not None else None,
        }
        for r in (await db.execute(top_tools_stmt)).all()
    ]

    return {
        "worker_id": backend_id,
        "session_count": session_count,
        "turn_count": turn_count or 0,
        "total_input_tokens": int(total_input or 0),
        "total_output_tokens": int(total_output or 0),
        "total_cache_creation_tokens": int(total_cache_creation or 0),
        "total_cache_read_tokens": int(total_cache_read or 0),
        "total_tool_calls": len(tool_calls),
        "avg_llm_duration_ms": avg_llm_ms,
        "p95_llm_duration_ms": p95_llm_ms,
        "avg_tool_duration_ms": avg_tool_ms,
        "p95_tool_duration_ms": p95_tool_ms,
        "error_rate": error_rate,
        "top_tools": top_tools,
    }


# ------------------------------------------------------------------
# GET /api/telemetry/sessions/{session_id}/summary
# ------------------------------------------------------------------


@router.get(
    "/sessions/{session_id}/summary",
    summary="Per-session telemetry summary",
    description=(
        "Returns per-turn breakdown and aggregate statistics for a single session: "
        "token counts, LLM duration averages and p95, error rate, TTFT."
    ),
    response_model=dict[str, Any],
)
async def get_session_summary(
    session_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Return telemetry summary for a single session."""
    backend_id = _backend_id(request)
    try:
        sid = uuid.UUID(session_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid session_id UUID") from exc

    turns_stmt = (
        select(SessionTurn)
        .where(
            SessionTurn.session_id == sid,
            SessionTurn.backend_id == backend_id,
        )
        .order_by(SessionTurn.turn_index)
    )
    turns = (await db.execute(turns_stmt)).scalars().all()

    tool_stmt = select(ToolCall).where(
        ToolCall.session_id == sid,
        ToolCall.backend_id == backend_id,
        ToolCall.ts_end.is_not(None),
    )
    tool_calls = (await db.execute(tool_stmt)).scalars().all()

    # Aggregate
    total_input = sum(t.input_tokens for t in turns)
    total_output = sum(t.output_tokens for t in turns)
    llm_durations = [t.llm_duration_ms for t in turns if t.llm_duration_ms is not None]
    tool_durations = [tc.duration_ms for tc in tool_calls if tc.duration_ms is not None]
    error_count = sum(1 for tc in tool_calls if tc.status == "error")

    avg_llm_ms = round(sum(llm_durations) / len(llm_durations), 2) if llm_durations else None
    avg_tool_ms = round(sum(tool_durations) / len(tool_durations), 2) if tool_durations else None
    p95_llm_ms = _p95_ms(llm_durations)
    p95_tool_ms = _p95_ms(tool_durations)
    error_rate = round(error_count / len(tool_calls), 4) if tool_calls else 0.0

    # Session duration: ts_start of first turn → ts_end of last completed turn
    session_duration_ms = None
    completed_turns = [t for t in turns if t.ts_end is not None]
    if turns and completed_turns:
        last_ts_end = completed_turns[-1].ts_end
        assert last_ts_end is not None  # guaranteed by the filter above
        session_duration_ms = int((last_ts_end - turns[0].ts_start).total_seconds() * 1000)

    # Per-turn breakdown
    turn_list = []
    for t in turns:
        tool_count = sum(1 for tc in tool_calls if tc.turn_id == t.id)
        ttft_ms = None
        if t.ts_first_token is not None:
            ttft_ms = int((t.ts_first_token - t.ts_start).total_seconds() * 1000)
        turn_list.append(
            {
                "turn_index": t.turn_index,
                "ts_start": t.ts_start.isoformat() if t.ts_start else None,
                "ts_end": t.ts_end.isoformat() if t.ts_end else None,
                "llm_duration_ms": t.llm_duration_ms,
                "ttft_ms": ttft_ms,
                "input_tokens": t.input_tokens,
                "output_tokens": t.output_tokens,
                "cache_creation_tokens": t.cache_creation_tokens,
                "cache_read_tokens": t.cache_read_tokens,
                "tool_calls": tool_count,
                "model": t.model,
                "interrupted": t.interrupted,
            }
        )

    return {
        "session_id": session_id,
        "turn_count": len(turns),
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_tool_calls": len(tool_calls),
        "avg_llm_duration_ms": avg_llm_ms,
        "avg_tool_duration_ms": avg_tool_ms,
        "p95_llm_duration_ms": p95_llm_ms,
        "p95_tool_duration_ms": p95_tool_ms,
        "error_rate": error_rate,
        "session_duration_ms": session_duration_ms,
        "turns": turn_list,
    }


# ------------------------------------------------------------------
# GET /api/telemetry/timeseries
# ------------------------------------------------------------------


@router.get(
    "/timeseries",
    summary="Telemetry time-series data",
    description=(
        "Returns pre-aggregated token and latency metrics bucketed by minute, hour, or day. "
        "Supports optional per-session filtering. When session_id is omitted, returns the "
        "global rollup across all sessions."
    ),
    response_model=dict[str, Any],
)
async def get_timeseries(
    request: Request,
    zoom: str = Query(..., description="Bucket granularity: 'minute', 'hour', or 'day'"),
    start: datetime = Query(..., description="Start of window (ISO8601 UTC)"),  # noqa: B008
    end: datetime = Query(..., description="End of window (ISO8601 UTC)"),  # noqa: B008
    session_id: str | None = Query(None, description="Filter to a single session UUID"),
    metric: str | None = Query(None, description="Specific metric to return (all if omitted)"),
    db: AsyncSession = Depends(get_db_session),  # noqa: B008
) -> dict[str, Any]:
    """Return bucketed time-series metrics for the requested window."""
    backend_id = _backend_id(request)

    if zoom not in ("minute", "hour", "day"):
        raise HTTPException(status_code=422, detail="zoom must be 'minute', 'hour', or 'day'")

    sid: uuid.UUID | None = None
    if session_id is not None:
        try:
            sid = uuid.UUID(session_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid session_id UUID") from exc

    # Ensure timezone-aware
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)

    stmt = (
        select(TelemetryMinutely)
        .where(
            TelemetryMinutely.backend_id == backend_id,
            TelemetryMinutely.bucket >= start,
            TelemetryMinutely.bucket < end,
            TelemetryMinutely.session_id == sid,
        )
        .order_by(TelemetryMinutely.bucket)
    )
    rows = (await db.execute(stmt)).scalars().all()

    # Group rows into zoom-level buckets
    def _truncate(dt: datetime, level: str) -> datetime:
        if level == "minute":
            return dt.replace(second=0, microsecond=0)
        if level == "hour":
            return dt.replace(minute=0, second=0, microsecond=0)
        # day
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)

    buckets: dict[datetime, dict[str, Any]] = {}
    for row in rows:
        bk = _truncate(row.bucket, zoom)
        if bk not in buckets:
            buckets[bk] = {
                "bucket": bk.isoformat(),
                "tokens_sent": 0,
                "tokens_received": 0,
                "cache_creation": 0,
                "cache_read": 0,
                "llm_duration_sum_us": 0,
                "llm_duration_count": 0,
                "tool_duration_sum_us": 0,
                "tool_duration_count": 0,
                "turn_count": 0,
                "tool_call_count": 0,
                "error_count": 0,
            }
        b = buckets[bk]
        b["tokens_sent"] += row.tokens_sent
        b["tokens_received"] += row.tokens_received
        b["cache_creation"] += row.cache_creation
        b["cache_read"] += row.cache_read
        b["llm_duration_sum_us"] += row.llm_duration_sum_us
        b["llm_duration_count"] += row.llm_duration_count
        b["tool_duration_sum_us"] += row.tool_duration_sum_us
        b["tool_duration_count"] += row.tool_duration_count
        b["turn_count"] += row.turn_count
        b["tool_call_count"] += row.tool_call_count
        b["error_count"] += row.error_count

    series = []
    for bk in sorted(buckets):
        b = buckets[bk]
        entry: dict[str, Any] = {
            "bucket": b["bucket"],
            "tokens_sent": b["tokens_sent"],
            "tokens_received": b["tokens_received"],
            "cache_creation": b["cache_creation"],
            "cache_read": b["cache_read"],
            "avg_llm_duration_ms": _avg_ms(b["llm_duration_sum_us"], b["llm_duration_count"]),
            "avg_tool_duration_ms": _avg_ms(b["tool_duration_sum_us"], b["tool_duration_count"]),
            "turn_count": b["turn_count"],
            "tool_call_count": b["tool_call_count"],
            "error_count": b["error_count"],
        }
        if metric is not None:
            # Filter to the requested metric plus the bucket key
            entry = {"bucket": entry["bucket"], metric: entry.get(metric)}
        series.append(entry)

    return {
        "zoom": zoom,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "session_id": session_id,
        "last_updated_at": datetime.now(UTC).isoformat(),
        "series": series,
    }
