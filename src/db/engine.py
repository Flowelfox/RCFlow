from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.config import Settings

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def init_engine(settings: Settings) -> None:
    global _engine, _session_factory

    kwargs: dict = {}
    if _is_sqlite(settings.DATABASE_URL):
        # Ensure the parent directory exists for the SQLite database file
        db_path = settings.DATABASE_URL.split("///", 1)[-1]
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # StaticPool shares a single connection across async tasks (required for aiosqlite)
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_pre_ping"] = True

    _engine = create_async_engine(settings.DATABASE_URL, **kwargs)

    if _is_sqlite(settings.DATABASE_URL):

        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def check_connection() -> None:
    """Verify the database is reachable by executing a simple query.

    Raises:
        RuntimeError: If the database engine is not initialized or the connection fails.
    """
    if _engine is None:
        raise RuntimeError("Database engine not initialized. Call init_engine() first.")
    async with _engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the session factory for use outside of FastAPI dependency injection."""
    if _session_factory is None:
        raise RuntimeError("Database engine not initialized. Call init_engine() first.")
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Database engine not initialized. Call init_engine() first.")
    async with _session_factory() as session:
        yield session
