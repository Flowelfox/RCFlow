---
updated: 2026-07-23
---

# Telemetry Subsystem

A built-in three-phase telemetry pipeline records per-turn and per-tool-call timing and token usage, aggregates raw events into minutely buckets for fast time-series queries, and enforces a configurable retention window.

**See also:**
- [Database](database.md) — `session_turns`, `tool_calls`, `telemetry_minutely` tables
- [HTTP API](http-api.md) — `/api/telemetry/*` endpoints
- [Architecture — Statistics Pane](architecture.md#split-view-desktop) — client-side rendering

---

## Phase 1 — Raw Event Capture

`TelemetryService` (`src/services/telemetry_service.py`) inserts one row per LLM turn and one row per tool call into `session_turns` and `tool_calls` respectively. The `PromptRouter` calls the service at four boundaries:

| Call | When |
|------|------|
| `record_turn_start(session_id, turn_index?)` | Before each `stream_turn()` call in the agentic loop |
| `record_first_token(turn)` | On the first `TextChunk` or first `ToolCallRequest` yielded by the stream |
| `record_turn_end(turn, usage)` | On each `StreamDone` with usage |
| `mark_turn_interrupted(turn)` | When the outer `handle_prompt` catches an exception |
| `record_tool_start(session_id, tool_name, executor_type, turn?, tool_call_index?)` | Before `executor.execute()` |
| `record_tool_end(tool_call, status, error?)` | After execution completes or raises |

All calls are best-effort: exceptions are logged but never propagated so telemetry never disrupts the prompt pipeline.

## Phase 2 — Minutely Aggregation

A background task (`_run_telemetry_loop` in `main.py`) calls `aggregate_pending()` every 60 seconds. The aggregator reads `session_turns` and `tool_calls` rows with `ts_end > watermark` and upserts into `telemetry_minutely` — one row per `(backend_id, bucket, session_id)` pair plus a global `session_id=NULL` rollup. Sums maintained per bucket: `tokens_sent`, `tokens_received`, `cache_creation`, `cache_read`, `llm_duration_sum_us`, `llm_duration_count`, `tool_duration_sum_us`, `tool_duration_count`, `turn_count`, `tool_call_count`, `error_count`, `parallel_tool_calls`.

The `telemetry_minutely` upserts are **additive** (not idempotent), so the aggregation watermark is **persisted** in the `telemetry_state` table (one row per backend, `aggregation_watermark`) and loaded on startup. This prevents a restart from re-adding already-counted history. The watermark is advanced on `ts_end` (the completion time) — not `ts_start` — so a turn that began before the watermark but finished after it is still counted exactly once.

All four `/api/telemetry/*` endpoints require the worker API key (router-level dependency).

## Phase 3 — Retention Cleanup

`cleanup_old_records()` is called once per day (~1440 aggregation ticks). It deletes rows from `session_turns`, `tool_calls`, and `telemetry_minutely` whose `ts_start` / `bucket` is older than `TELEMETRY_RETENTION_DAYS` (default 90).

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| `TELEMETRY_RETENTION_DAYS` | `90` | Days to keep raw and aggregated telemetry rows |

## REST API

See [HTTP API](http-api.md) for endpoint signatures. The four endpoints are:

- **`GET /api/telemetry/summary`** — global lifetime stats (tokens, latencies, top tools).
- **`GET /api/telemetry/worker/summary`** — same scope as global summary but richer: adds `session_count`, `total_tool_calls`, `p95_llm_duration_ms`, `avg_tool_duration_ms`, `p95_tool_duration_ms`, `error_rate`. Used by the worker stats dialog.
- **`GET /api/telemetry/sessions/{session_id}/summary`** — per-turn breakdown with TTFT and aggregate p95 latencies for one session.
- **`GET /api/telemetry/timeseries`** — bucketed series from `telemetry_minutely`, with zoom-level roll-up (`minute`/`hour`/`day`) applied on read. `avg_llm_duration_ms` and `avg_tool_duration_ms` are derived from the stored sum + count.

## Flutter Client

Data models: `lib/models/telemetry.dart` — `ZoomLevel` enum, `BucketPoint`, `TurnSummary`, `SessionTelemetrySummary`, `WorkerTelemetrySummary`.
State: `lib/state/statistics_pane_state.dart` — `StatisticsPaneState` (zoom level, time range, series data, session summary, loading/error flags).
UI: see `StatisticsPane` and `WorkerStatsPane` under the [Architecture — Split View](architecture.md#split-view-desktop) section.
