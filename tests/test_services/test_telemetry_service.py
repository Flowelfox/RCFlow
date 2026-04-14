"""Tests for TelemetryService and telemetry API endpoints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes.telemetry import router as telemetry_router
from src.database.engine import get_db_session
from src.services.telemetry_service import TelemetryService

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COUNTER_ATTRS = (
    "tokens_sent",
    "tokens_received",
    "cache_creation",
    "cache_read",
    "llm_duration_sum_us",
    "llm_duration_count",
    "tool_duration_sum_us",
    "tool_duration_count",
    "inter_tool_gap_sum_us",
    "inter_tool_gap_count",
    "inter_turn_gap_sum_us",
    "inter_turn_gap_count",
    "turn_count",
    "tool_call_count",
    "error_count",
    "parallel_tool_calls",
)


def _make_service() -> TelemetryService:
    return TelemetryService(db_factory=MagicMock(), backend_id="test-backend")


def _make_bucket() -> datetime:
    return datetime(2026, 3, 18, 12, 0, 0, tzinfo=UTC)


def _existing_row(**values) -> SimpleNamespace:
    """Simulate a flushed row already in the database with given counter values."""
    attrs = {a: 0 for a in _COUNTER_ATTRS}
    attrs.update(values)
    return SimpleNamespace(**attrs)


def _mock_db_new_row() -> tuple[AsyncMock, list]:
    """Return a mock DB where execute returns None, and capture db.add() calls."""
    added: list = []
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock(side_effect=added.append)
    return db, added


def _mock_db_existing(row: SimpleNamespace) -> AsyncMock:
    """Return a mock DB where execute resolves to an existing row."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# _upsert_bucket — new row (None attribute) path
# ---------------------------------------------------------------------------


class TestUpsertBucketNewRow:
    """When no existing bucket row is found a new TelemetryMinutely is created.

    SQLAlchemy column defaults (default=0) are applied at INSERT flush time, not
    at Python object construction time, so all counter attributes are None when
    the upsert logic first touches them.  The service must not raise TypeError.
    """

    @pytest.mark.asyncio
    async def test_new_row_turn_fields_no_type_error(self):
        """Turn-related deltas must be applied without TypeError on a fresh row."""
        service = _make_service()
        db, added = _mock_db_new_row()

        await service._upsert_bucket(
            db,
            backend_id="test-backend",
            bucket=_make_bucket(),
            session_id=uuid.uuid4(),
            tokens_sent_delta=100,
            tokens_received_delta=50,
            cache_creation_delta=10,
            cache_read_delta=5,
            llm_duration_us_delta=250_000,
            llm_count_delta=1,
            turn_count_delta=1,
        )

        assert len(added) == 1
        row = added[0]
        assert row.tokens_sent == 100
        assert row.tokens_received == 50
        assert row.cache_creation == 10
        assert row.cache_read == 5
        assert row.llm_duration_sum_us == 250_000
        assert row.llm_duration_count == 1
        assert row.turn_count == 1
        # Untouched fields must default to 0, not remain None
        assert row.tool_duration_sum_us == 0
        assert row.error_count == 0

    @pytest.mark.asyncio
    async def test_new_row_zero_deltas_all_fields_zero(self):
        """Zero deltas on a fresh row leave all counter fields at 0, not None."""
        service = _make_service()
        db, added = _mock_db_new_row()

        await service._upsert_bucket(
            db,
            backend_id="test-backend",
            bucket=_make_bucket(),
            session_id=None,
        )

        assert len(added) == 1
        row = added[0]
        for attr in _COUNTER_ATTRS:
            assert getattr(row, attr) == 0, f"{attr} expected 0, got {getattr(row, attr)}"

    @pytest.mark.asyncio
    async def test_new_row_tool_call_fields_no_type_error(self):
        """Tool-call deltas must be applied without TypeError on a fresh row."""
        service = _make_service()
        db, added = _mock_db_new_row()

        await service._upsert_bucket(
            db,
            backend_id="test-backend",
            bucket=_make_bucket(),
            session_id=None,
            tool_duration_us_delta=80_000,
            tool_count_delta=1,
            tool_call_count_delta=1,
            error_count_delta=1,
        )

        assert len(added) == 1
        row = added[0]
        assert row.tool_duration_sum_us == 80_000
        assert row.tool_duration_count == 1
        assert row.tool_call_count == 1
        assert row.error_count == 1
        assert row.tokens_sent == 0


# ---------------------------------------------------------------------------
# _upsert_bucket — existing row path
# ---------------------------------------------------------------------------


class TestUpsertBucketExistingRow:
    @pytest.mark.asyncio
    async def test_existing_row_accumulates_deltas(self):
        service = _make_service()
        row = _existing_row(
            tokens_sent=200,
            tokens_received=100,
            llm_duration_sum_us=500_000,
            llm_duration_count=2,
            turn_count=2,
        )
        db = _mock_db_existing(row)

        await service._upsert_bucket(
            db,
            backend_id="test-backend",
            bucket=_make_bucket(),
            session_id=uuid.uuid4(),
            tokens_sent_delta=50,
            tokens_received_delta=25,
            llm_duration_us_delta=100_000,
            llm_count_delta=1,
            turn_count_delta=1,
        )

        assert row.tokens_sent == 250
        assert row.tokens_received == 125
        assert row.llm_duration_sum_us == 600_000
        assert row.llm_duration_count == 3
        assert row.turn_count == 3

    @pytest.mark.asyncio
    async def test_existing_row_with_unexpected_null_tolerates_none(self):
        """A row fetched from DB with an unexpected NULL counter must not crash."""
        service = _make_service()
        row = _existing_row(tokens_sent=None, tokens_received=10, turn_count=1)
        db = _mock_db_existing(row)

        await service._upsert_bucket(
            db,
            backend_id="test-backend",
            bucket=_make_bucket(),
            session_id=None,
            tokens_sent_delta=75,
        )

        # None treated as 0, then + 75
        assert row.tokens_sent == 75
        assert row.tokens_received == 10
        assert row.turn_count == 1


# ---------------------------------------------------------------------------
# GET /api/telemetry/worker/summary — HTTP endpoint tests
# ---------------------------------------------------------------------------


def _make_telemetry_app(backend_id: str = "test-backend") -> FastAPI:
    """Build a minimal FastAPI app with the telemetry router and a mocked DB."""
    app = FastAPI()
    settings = MagicMock()
    settings.RCFLOW_BACKEND_ID = backend_id
    settings.RCFLOW_API_KEY = "test-key"
    app.state.settings = settings
    app.include_router(telemetry_router, prefix="/api")
    return app


def _mock_db_for_worker_summary(
    *,
    session_count: int = 3,
    turn_count: int = 10,
    token_totals: tuple = (5000, 3000, 100, 50),
    llm_durations: list[int] | None = None,
    tool_calls: list[SimpleNamespace] | None = None,
) -> AsyncMock:
    """Return a mock DB session that yields realistic worker summary data."""
    if llm_durations is None:
        llm_durations = [200, 300, 400]
    if tool_calls is None:
        tool_calls = [
            SimpleNamespace(duration_ms=50, status="ok", tool_name="shell"),
            SimpleNamespace(duration_ms=75, status="error", tool_name="shell"),
            SimpleNamespace(duration_ms=30, status="ok", tool_name="http"),
        ]

    db = AsyncMock()

    # execute() is called multiple times in sequence; we use side_effect list
    call_results = []

    # 1. session_count query (scalar_one)
    r0 = MagicMock()
    r0.scalar_one.return_value = session_count
    call_results.append(r0)

    # 2. token/turn totals (one() → tuple)
    r1 = MagicMock()
    r1.one.return_value = (*token_totals, turn_count)
    call_results.append(r1)

    # 3. LLM durations (all() → list of 1-tuples)
    r2 = MagicMock()
    r2.all.return_value = [(d,) for d in llm_durations]
    call_results.append(r2)

    # 4. Tool calls (scalars().all())
    r3 = MagicMock()
    r3.scalars.return_value.all.return_value = tool_calls
    call_results.append(r3)

    # 5. Top tools aggregate (all() → list of 3-tuples)
    r4 = MagicMock()
    r4.all.return_value = [("shell", 2, 62.5), ("http", 1, 30.0)]
    call_results.append(r4)

    db.execute = AsyncMock(side_effect=call_results)
    return db


class TestWorkerSummaryEndpoint:
    """Tests for GET /api/telemetry/worker/summary."""

    def _client_with_db(self, db: AsyncMock) -> TestClient:
        app = _make_telemetry_app()

        async def _override_db() -> AsyncGenerator:
            yield db

        app.dependency_overrides[get_db_session] = _override_db
        return TestClient(app)

    def test_returns_200_with_expected_shape(self):
        db = _mock_db_for_worker_summary()
        client = self._client_with_db(db)

        resp = client.get("/api/telemetry/worker/summary")

        assert resp.status_code == 200
        body = resp.json()
        assert body["worker_id"] == "test-backend"
        assert body["session_count"] == 3
        assert body["turn_count"] == 10
        assert body["total_input_tokens"] == 5000
        assert body["total_output_tokens"] == 3000
        assert body["total_cache_creation_tokens"] == 100
        assert body["total_cache_read_tokens"] == 50
        assert body["total_tool_calls"] == 3
        assert "avg_llm_duration_ms" in body
        assert "p95_llm_duration_ms" in body
        assert "avg_tool_duration_ms" in body
        assert "p95_tool_duration_ms" in body
        assert "error_rate" in body
        assert "top_tools" in body

    def test_error_rate_computed_correctly(self):
        """One error out of three tool calls → error_rate ≈ 0.333."""
        db = _mock_db_for_worker_summary()
        client = self._client_with_db(db)

        body = client.get("/api/telemetry/worker/summary").json()

        assert abs(body["error_rate"] - round(1 / 3, 4)) < 1e-4

    def test_top_tools_list_present(self):
        db = _mock_db_for_worker_summary()
        client = self._client_with_db(db)

        body = client.get("/api/telemetry/worker/summary").json()

        assert isinstance(body["top_tools"], list)
        assert body["top_tools"][0]["tool_name"] == "shell"
        assert body["top_tools"][0]["call_count"] == 2

    def test_no_turns_returns_zero_totals(self):
        """When there are no completed turns, token totals and counts are 0."""
        db = _mock_db_for_worker_summary(
            session_count=0,
            turn_count=0,
            token_totals=(None, None, None, None),
            llm_durations=[],
            tool_calls=[],
        )
        client = self._client_with_db(db)

        body = client.get("/api/telemetry/worker/summary").json()

        assert body["session_count"] == 0
        assert body["turn_count"] == 0
        assert body["total_input_tokens"] == 0
        assert body["avg_llm_duration_ms"] is None
        assert body["error_rate"] == 0.0

    def test_avg_and_p95_llm_duration_computed(self):
        """avg of [100, 200, 300] = 200; p95 uses idx = int(n*0.95)-1 clamped to 0."""
        # For n=3: idx = max(0, int(3*0.95)-1) = max(0, 2-1) = 1 → sorted[1] = 200
        db = _mock_db_for_worker_summary(llm_durations=[100, 200, 300])
        client = self._client_with_db(db)

        body = client.get("/api/telemetry/worker/summary").json()

        assert body["avg_llm_duration_ms"] == 200.0
        assert body["p95_llm_duration_ms"] == 200.0


# ---------------------------------------------------------------------------
# Helpers for tests that need a mock db_factory
# ---------------------------------------------------------------------------


def _make_db_factory(db: AsyncMock) -> MagicMock:
    """Return a callable that behaves like async_sessionmaker, yielding *db*."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=db)
    cm.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=cm)


def _make_db_factory_sequence(dbs: list[AsyncMock]) -> MagicMock:
    """Return a factory that yields each db in *dbs* in turn (one per call)."""
    cms = []
    for db in dbs:
        cm = AsyncMock()
        cm.__aenter__ = AsyncMock(return_value=db)
        cm.__aexit__ = AsyncMock(return_value=False)
        cms.append(cm)
    return MagicMock(side_effect=cms)


def _fresh_db(*, session_exists: bool = True) -> AsyncMock:
    """Return a mock DB where get() returns a row or None based on *session_exists*."""
    db = AsyncMock()
    db.get = AsyncMock(return_value=MagicMock() if session_exists else None)
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# _ensure_session_stub
# ---------------------------------------------------------------------------


SESSION_ID = "e82690ec-3c68-418f-b84b-f4258b433668"
_TS = datetime(2026, 3, 23, 10, 0, 0, tzinfo=UTC)


class TestEnsureSessionStub:
    """Unit tests for TelemetryService._ensure_session_stub."""

    @pytest.mark.asyncio
    async def test_inserts_stub_row_when_session_missing(self):
        """When no sessions row exists, a Session stub is added and committed."""
        from src.database.models import Session as SessionModel  # noqa: PLC0415

        db = _fresh_db(session_exists=False)
        service = TelemetryService(db_factory=_make_db_factory(db), backend_id="be-1")

        await service._ensure_session_stub(SESSION_ID, _TS)

        db.add.assert_called_once()
        inserted = db.add.call_args[0][0]
        assert isinstance(inserted, SessionModel)
        assert str(inserted.id) == SESSION_ID
        assert inserted.backend_id == "be-1"
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_insert_when_session_already_exists(self):
        """When a sessions row already exists, nothing is added or committed."""
        db = _fresh_db(session_exists=True)
        service = TelemetryService(db_factory=_make_db_factory(db), backend_id="be-1")

        await service._ensure_session_stub(SESSION_ID, _TS)

        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_db_error_is_swallowed_not_propagated(self):
        """DB errors inside _ensure_session_stub do not propagate to the caller."""
        db = AsyncMock()
        db.get = AsyncMock(side_effect=Exception("connection refused"))
        service = TelemetryService(db_factory=_make_db_factory(db), backend_id="be-1")

        # Must not raise
        await service._ensure_session_stub(SESSION_ID, _TS)


# ---------------------------------------------------------------------------
# record_turn_start — FK guard integration
# ---------------------------------------------------------------------------


class TestRecordTurnStartFKGuard:
    """record_turn_start calls _ensure_session_stub before inserting the turn row."""

    @pytest.mark.asyncio
    async def test_calls_ensure_stub_then_inserts_turn(self):
        """When session is missing, a stub is created and the SessionTurn row is inserted."""
        from src.database.models import Session as SessionModel  # noqa: PLC0415
        from src.database.models import SessionTurn  # noqa: PLC0415

        stub_db = _fresh_db(session_exists=False)  # for _ensure_session_stub
        turn_db = _fresh_db(session_exists=True)  # for the SessionTurn insert
        service = TelemetryService(
            db_factory=_make_db_factory_sequence([stub_db, turn_db]),
            backend_id="be-1",
        )

        turn = await service.record_turn_start(session_id=SESSION_ID, turn_index=0)

        # Stub path
        stub_db.add.assert_called_once()
        assert isinstance(stub_db.add.call_args[0][0], SessionModel)

        # Turn insert path
        turn_db.add.assert_called_once()
        assert isinstance(turn_db.add.call_args[0][0], SessionTurn)

        # InFlightTurn is always returned
        assert turn.session_id == SESSION_ID
        assert turn.turn_index == 0

    @pytest.mark.asyncio
    async def test_returns_inflight_turn_even_when_stub_and_insert_both_fail(self):
        """InFlightTurn is returned even if DB is completely unavailable."""
        broken_db = AsyncMock()
        broken_db.get = AsyncMock(side_effect=Exception("DB down"))
        broken_db.add = MagicMock()
        broken_db.commit = AsyncMock(side_effect=Exception("DB down"))

        service = TelemetryService(
            db_factory=_make_db_factory_sequence([broken_db, broken_db]),
            backend_id="be-1",
        )

        # Must not raise
        turn = await service.record_turn_start(session_id=SESSION_ID)
        assert turn.session_id == SESSION_ID

    @pytest.mark.asyncio
    async def test_skips_stub_insert_when_session_already_exists(self):
        """When the session row already exists, only the turn is inserted (no stub write)."""
        from src.database.models import Session as SessionModel  # noqa: PLC0415

        stub_db = _fresh_db(session_exists=True)  # get() returns existing row
        turn_db = _fresh_db(session_exists=True)
        service = TelemetryService(
            db_factory=_make_db_factory_sequence([stub_db, turn_db]),
            backend_id="be-1",
        )

        await service.record_turn_start(session_id=SESSION_ID, turn_index=1)

        # Stub path did a get() but no add/commit
        stub_db.add.assert_not_called()
        stub_db.commit.assert_not_awaited()

        # Turn was still inserted
        turn_db.add.assert_called_once()
        assert not isinstance(turn_db.add.call_args[0][0], SessionModel)


# ---------------------------------------------------------------------------
# record_tool_start — FK guard integration
# ---------------------------------------------------------------------------


class TestRecordToolStartFKGuard:
    """record_tool_start also calls _ensure_session_stub before inserting."""

    @pytest.mark.asyncio
    async def test_creates_stub_when_session_missing(self):
        """When session is missing, a stub is created before the ToolCall insert."""
        from src.database.models import Session as SessionModel  # noqa: PLC0415
        from src.database.models import ToolCall  # noqa: PLC0415

        stub_db = _fresh_db(session_exists=False)
        tool_db = _fresh_db(session_exists=True)
        service = TelemetryService(
            db_factory=_make_db_factory_sequence([stub_db, tool_db]),
            backend_id="be-1",
        )

        tc = await service.record_tool_start(
            session_id=SESSION_ID,
            tool_name="shell",
            executor_type="direct",
        )

        stub_db.add.assert_called_once()
        assert isinstance(stub_db.add.call_args[0][0], SessionModel)

        tool_db.add.assert_called_once()
        assert isinstance(tool_db.add.call_args[0][0], ToolCall)

        assert tc.session_id == SESSION_ID

    @pytest.mark.asyncio
    async def test_returns_inflight_tool_call_even_when_db_fails(self):
        """InFlightToolCall is always returned regardless of DB failures."""
        broken_db = AsyncMock()
        broken_db.get = AsyncMock(side_effect=Exception("FK error"))
        broken_db.add = MagicMock()
        broken_db.commit = AsyncMock(side_effect=Exception("FK error"))

        service = TelemetryService(
            db_factory=_make_db_factory_sequence([broken_db, broken_db]),
            backend_id="be-1",
        )

        tc = await service.record_tool_start(
            session_id=SESSION_ID,
            tool_name="shell",
            executor_type="direct",
        )
        assert tc.session_id == SESSION_ID
