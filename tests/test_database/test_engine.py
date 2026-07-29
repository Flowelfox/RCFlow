"""Tests for the SQLAlchemy engine setup (pool selection per DB URL)."""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool

from src import database
from src.config import Settings
from src.database.engine import _is_memory_sqlite, init_engine


@pytest.mark.parametrize(
    ("url", "is_memory"),
    [
        ("sqlite+aiosqlite://", True),
        ("sqlite+aiosqlite:///:memory:", True),
        ("sqlite+aiosqlite:////var/lib/rcflow/rcflow.db", False),
        ("sqlite+aiosqlite:///./data/rcflow.db", False),
        ("postgresql+asyncpg://u:p@h/db", False),
    ],
)
def test_is_memory_sqlite(url: str, is_memory: bool) -> None:
    assert _is_memory_sqlite(url) is is_memory


async def _pool_class_for(url: str, tmp_path):
    settings = Settings(DATABASE_URL=url, TOOLS_DIR=tmp_path)
    init_engine(settings)
    try:
        return type(database.engine._engine.pool)
    finally:
        await database.engine.dispose_engine()


@pytest.mark.asyncio
async def test_file_sqlite_does_not_use_static_pool(tmp_path) -> None:
    # File-backed SQLite must NOT share one connection across tasks (the bug).
    pool_cls = await _pool_class_for(f"sqlite+aiosqlite:///{tmp_path / 'x.db'}", tmp_path)
    assert pool_cls is not StaticPool


@pytest.mark.asyncio
async def test_memory_sqlite_uses_static_pool(tmp_path) -> None:
    # In-memory SQLite still needs StaticPool so every session shares the DB.
    pool_cls = await _pool_class_for("sqlite+aiosqlite://", tmp_path)
    assert pool_cls is StaticPool
