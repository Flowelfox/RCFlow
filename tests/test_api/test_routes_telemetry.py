"""Tests for src/api/routes/telemetry.py.

Exercises all four read endpoints against an empty in-memory database (which
drives every aggregation query through its zero-row path) plus the invalid
UUID / missing-parameter guards.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.database.engine import get_db_session
from src.database.models import Base

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from fastapi import FastAPI


@pytest.fixture
async def db_factory(tmp_path: Path) -> AsyncGenerator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'telemetry.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
def client(test_app: FastAPI, db_factory: async_sessionmaker[AsyncSession]) -> TestClient:
    async def _override() -> AsyncGenerator[AsyncSession]:
        async with db_factory() as session:
            yield session

    test_app.dependency_overrides[get_db_session] = _override
    return TestClient(test_app)


class TestGlobalSummary:
    def test_empty_db_returns_zeros(self, client: TestClient) -> None:
        resp = client.get("/api/telemetry/summary")
        assert resp.status_code == 200
        body = resp.json()
        assert body["turn_count"] == 0
        assert body["total_input_tokens"] == 0
        assert body["total_output_tokens"] == 0
        assert body["top_tools"] == []
        assert "backend_id" in body


class TestWorkerSummary:
    def test_empty_db(self, client: TestClient) -> None:
        resp = client.get("/api/telemetry/worker/summary")
        assert resp.status_code == 200
        assert isinstance(resp.json(), dict)


class TestSessionSummary:
    def test_valid_uuid_empty(self, client: TestClient) -> None:
        resp = client.get(f"/api/telemetry/sessions/{uuid.uuid4()}/summary")
        assert resp.status_code == 200

    def test_invalid_uuid_rejected(self, client: TestClient) -> None:
        resp = client.get("/api/telemetry/sessions/not-a-uuid/summary")
        assert resp.status_code == 422


class TestTimeseries:
    def test_valid_window(self, client: TestClient) -> None:
        resp = client.get(
            "/api/telemetry/timeseries",
            params={
                "zoom": "hour",
                "start": "2026-06-01T00:00:00+00:00",
                "end": "2026-06-02T00:00:00+00:00",
            },
        )
        assert resp.status_code == 200

    def test_missing_params_rejected(self, client: TestClient) -> None:
        # zoom/start/end are required query params.
        assert client.get("/api/telemetry/timeseries").status_code == 422

    def test_invalid_session_filter_rejected(self, client: TestClient) -> None:
        resp = client.get(
            "/api/telemetry/timeseries",
            params={
                "zoom": "day",
                "start": "2026-06-01T00:00:00+00:00",
                "end": "2026-06-02T00:00:00+00:00",
                "session_id": "not-a-uuid",
            },
        )
        assert resp.status_code == 422
