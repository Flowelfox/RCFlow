"""Async SQLAlchemy engine and session-factory setup."""

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.config import Settings
from src.exceptions import DatabaseNotInitializedError

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _is_memory_sqlite(url: str) -> bool:
    """Return True for an in-memory SQLite URL (``sqlite+aiosqlite://`` or ``/:memory:``)."""
    return ":memory:" in url or url.split("://", 1)[-1] == ""


def init_engine(settings: Settings) -> None:
    """Init engine."""
    global _engine, _session_factory

    kwargs: dict = {}
    if _is_sqlite(settings.DATABASE_URL):
        kwargs["connect_args"] = {"check_same_thread": False}
        if _is_memory_sqlite(settings.DATABASE_URL):
            # An in-memory database lives inside a single connection — StaticPool
            # keeps every session on that one connection so they share state.
            kwargs["poolclass"] = StaticPool
        else:
            # File-backed SQLite: use the default async pool so each AsyncSession
            # gets its OWN connection. StaticPool here shared one connection across
            # all tasks, so one task's commit/rollback could commit another task's
            # half-written rows or wipe its in-flight transaction. WAL +
            # busy_timeout (below) handle writer contention safely.
            db_path = settings.DATABASE_URL.split("///", 1)[-1]
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    else:
        kwargs["pool_pre_ping"] = True

    _engine = create_async_engine(settings.DATABASE_URL, **kwargs)

    if _is_sqlite(settings.DATABASE_URL):

        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            # Wait up to 5s for a competing writer's lock instead of erroring
            # immediately — now that sessions no longer share one connection.
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def check_connection() -> None:
    """Verify the database is reachable by executing a simple query.

    Raises:
        DatabaseNotInitializedError: If the database engine is not initialized.
    """
    if _engine is None:
        raise DatabaseNotInitializedError("Database engine not initialized. Call init_engine() first.")
    async with _engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def dispose_engine() -> None:
    """Dispose engine."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory for use outside of FastAPI dependency injection.

    Raises:
        DatabaseNotInitializedError: If the database engine is not initialized.
    """
    if _session_factory is None:
        raise DatabaseNotInitializedError("Database engine not initialized. Call init_engine() first.")
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """Get db session."""
    if _session_factory is None:
        raise DatabaseNotInitializedError("Database engine not initialized. Call init_engine() first.")
    async with _session_factory() as session:
        yield session
