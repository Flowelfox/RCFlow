"""DB-backed tests for ArtifactScanner scan/upsert methods.

Complements ``test_artifact_scanner.py`` (pure-unit regex/filter tests) by
exercising the async ``scan`` / ``scan_texts`` / ``scan_from_history`` paths and
``_upsert_artifacts`` against an in-memory SQLite database.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.config import Settings
from src.database.models import Artifact, Base, SessionMessage
from src.database.models import Session as SessionModel
from src.services.artifact_scanner import ArtifactScanner

if TYPE_CHECKING:
    from pathlib import Path

_BACKEND_ID = "backend-test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        RCFLOW_HOST="127.0.0.1",
        RCFLOW_PORT=8765,
        RCFLOW_API_KEY="test-key",
        DATABASE_URL="sqlite+aiosqlite:///test.db",
        ANTHROPIC_API_KEY="test",
        TOOLS_DIR=tmp_path / "tools",
        RCFLOW_BACKEND_ID=_BACKEND_ID,
        ARTIFACT_INCLUDE_PATTERN="*",
        ARTIFACT_EXCLUDE_PATTERN="node_modules/**,__pycache__/**,.git/**,.venv/**,*.pyc",
        ARTIFACT_MAX_FILE_SIZE=1024,
    )


@pytest.fixture
async def db_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


def _scanner(settings: Settings, db_factory: async_sessionmaker) -> ArtifactScanner:
    return ArtifactScanner(settings, db_factory)


async def _make_session_row(
    db_factory: async_sessionmaker,
    *,
    conversation_history: list | None = None,
    main_project_path: str | None = None,
) -> uuid.UUID:
    sid = uuid.uuid4()
    async with db_factory() as db:
        db.add(
            SessionModel(
                id=sid,
                backend_id=_BACKEND_ID,
                created_at=datetime.now(UTC),
                session_type="conversational",
                status="active",
                conversation_history=conversation_history,
                main_project_path=main_project_path,
            )
        )
        await db.commit()
    return sid


async def _count_artifacts(db_factory: async_sessionmaker) -> int:
    async with db_factory() as db:
        return (await db.execute(select(func.count()).select_from(Artifact))).scalar_one()


async def _get_artifact(db_factory: async_sessionmaker, file_path: str) -> Artifact | None:
    async with db_factory() as db:
        return (await db.execute(select(Artifact).where(Artifact.file_path == file_path))).scalar_one_or_none()


# ---------------------------------------------------------------------------
# scan_texts (real-time)
# ---------------------------------------------------------------------------


class TestScanTexts:
    async def test_discovers_new_artifact(self, settings: Settings, db_factory, tmp_path: Path):
        f = tmp_path / "found.md"
        f.write_text("# hi")
        scanner = _scanner(settings, db_factory)

        new, updated = await scanner.scan_texts(uuid.uuid4(), [f"see {f}"])
        assert new == 1 and updated == 0
        art = await _get_artifact(db_factory, str(f.resolve()))
        assert art is not None
        assert art.file_name == "found.md"
        assert art.session_id is None  # session not in DB

    async def test_accepts_str_session_id(self, settings: Settings, db_factory, tmp_path: Path):
        f = tmp_path / "doc.txt"
        f.write_text("x")
        scanner = _scanner(settings, db_factory)
        new, _ = await scanner.scan_texts(str(uuid.uuid4()), [str(f)])
        assert new == 1

    async def test_no_candidate_paths_returns_zero(self, settings: Settings, db_factory):
        scanner = _scanner(settings, db_factory)
        assert await scanner.scan_texts(uuid.uuid4(), ["nothing here"]) == (0, 0)
        assert await _count_artifacts(db_factory) == 0

    async def test_session_id_backfilled_when_session_exists(self, settings: Settings, db_factory, tmp_path: Path):
        f = tmp_path / "real.md"
        f.write_text("hi")
        sid = await _make_session_row(db_factory)
        scanner = _scanner(settings, db_factory)
        new, _ = await scanner.scan_texts(sid, [str(f)])
        assert new == 1
        art = await _get_artifact(db_factory, str(f.resolve()))
        assert art is not None and art.session_id == sid

    async def test_skips_file_over_max_size(self, settings: Settings, db_factory, tmp_path: Path):
        big = tmp_path / "big.md"
        big.write_text("y" * 5000)  # > ARTIFACT_MAX_FILE_SIZE (1024)
        scanner = _scanner(settings, db_factory)
        new, updated = await scanner.scan_texts(uuid.uuid4(), [str(big)])
        assert (new, updated) == (0, 0)
        assert await _count_artifacts(db_factory) == 0

    async def test_skips_excluded_file(self, settings: Settings, db_factory, tmp_path: Path):
        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        f = nm / "index.md"
        f.write_text("x")
        scanner = _scanner(settings, db_factory)
        new, updated = await scanner.scan_texts(uuid.uuid4(), [str(f)])
        assert (new, updated) == (0, 0)

    async def test_unresolvable_path_skipped(self, settings: Settings, db_factory):
        scanner = _scanner(settings, db_factory)
        new, updated = await scanner.scan_texts(uuid.uuid4(), ["/nonexistent/zzz.md"])
        assert (new, updated) == (0, 0)

    async def test_update_existing_artifact_on_change(self, settings: Settings, db_factory, tmp_path: Path):
        f = tmp_path / "evolve.md"
        f.write_text("v1")
        scanner = _scanner(settings, db_factory)
        new, _ = await scanner.scan_texts(uuid.uuid4(), [str(f)])
        assert new == 1

        # Mutate file so size + mtime differ, then rescan -> update path.
        future = datetime(2030, 1, 1, tzinfo=UTC).timestamp()
        f.write_text("v2-much-longer-content")
        os.utime(f, (future, future))
        new2, updated2 = await scanner.scan_texts(uuid.uuid4(), [str(f)])
        assert new2 == 0 and updated2 == 1

    async def test_existing_artifact_not_recreated(self, settings: Settings, db_factory, tmp_path: Path):
        f = tmp_path / "stable.md"
        f.write_text("same")
        scanner = _scanner(settings, db_factory)
        await scanner.scan_texts(uuid.uuid4(), [str(f)])
        new2, _ = await scanner.scan_texts(uuid.uuid4(), [str(f)])
        # Rescanning the same path must not insert a duplicate artifact row.
        assert new2 == 0
        assert await _count_artifacts(db_factory) == 1

    async def test_backfills_session_id_on_existing_artifact(self, settings: Settings, db_factory, tmp_path: Path):
        f = tmp_path / "backfill.md"
        f.write_text("hi")
        scanner = _scanner(settings, db_factory)
        # First scan: no session row -> session_id stays None.
        await scanner.scan_texts(uuid.uuid4(), [str(f)])
        art = await _get_artifact(db_factory, str(f.resolve()))
        assert art is not None and art.session_id is None

        # Now create a session and rescan with that id -> back-fill.
        sid = await _make_session_row(db_factory)
        await scanner.scan_texts(sid, [str(f)])
        art2 = await _get_artifact(db_factory, str(f.resolve()))
        assert art2 is not None and art2.session_id == sid


# ---------------------------------------------------------------------------
# scan_from_history (real-time)
# ---------------------------------------------------------------------------


class TestScanFromHistory:
    async def test_discovers_from_conversation(self, settings: Settings, db_factory, tmp_path: Path):
        f = tmp_path / "from_hist.md"
        f.write_text("hi")
        scanner = _scanner(settings, db_factory)
        history = [{"role": "assistant", "content": f"wrote {f}"}]
        new, updated = await scanner.scan_from_history(uuid.uuid4(), history)
        assert new == 1 and updated == 0

    async def test_empty_history_returns_zero(self, settings: Settings, db_factory):
        scanner = _scanner(settings, db_factory)
        assert await scanner.scan_from_history(str(uuid.uuid4()), []) == (0, 0)

    async def test_resolves_relative_against_project_path(self, settings: Settings, db_factory, tmp_path: Path):
        f = tmp_path / "rel.md"
        f.write_text("hi")
        scanner = _scanner(settings, db_factory)
        history = [{"role": "assistant", "content": "edited ./rel.md"}]
        new, _ = await scanner.scan_from_history(uuid.uuid4(), history, project_path=tmp_path)
        assert new == 1


# ---------------------------------------------------------------------------
# scan (archived session)
# ---------------------------------------------------------------------------


class TestScan:
    async def test_scan_conversation_history(self, settings: Settings, db_factory, tmp_path: Path):
        f = tmp_path / "archived.md"
        f.write_text("hi")
        sid = await _make_session_row(
            db_factory,
            conversation_history=[{"role": "user", "content": f"made {f}"}],
        )
        scanner = _scanner(settings, db_factory)
        assert await scanner.scan(sid) == 1
        art = await _get_artifact(db_factory, str(f.resolve()))
        assert art is not None and art.session_id == sid

    async def test_scan_accepts_str_session_id(self, settings: Settings, db_factory, tmp_path: Path):
        f = tmp_path / "s.md"
        f.write_text("hi")
        sid = await _make_session_row(db_factory, conversation_history=[{"role": "user", "content": str(f)}])
        scanner = _scanner(settings, db_factory)
        assert await scanner.scan(str(sid)) == 1

    async def test_scan_session_messages(self, settings: Settings, db_factory, tmp_path: Path):
        f = tmp_path / "msg.md"
        f.write_text("hi")
        meta_file = tmp_path / "meta.md"
        meta_file.write_text("meta")
        sid = await _make_session_row(db_factory)
        async with db_factory() as db:
            db.add(
                SessionMessage(
                    session_id=sid,
                    sequence=0,
                    message_type="text_chunk",
                    content=f"created {f}",
                    metadata_={"note": f"also {meta_file}"},
                )
            )
            await db.commit()
        scanner = _scanner(settings, db_factory)
        assert await scanner.scan(sid) == 2

    async def test_scan_no_paths_returns_zero(self, settings: Settings, db_factory):
        sid = await _make_session_row(db_factory, conversation_history=[{"role": "user", "content": "nothing here"}])
        scanner = _scanner(settings, db_factory)
        assert await scanner.scan(sid) == 0

    async def test_scan_uses_main_project_path_for_relative(self, settings: Settings, db_factory, tmp_path: Path):
        f = tmp_path / "proj.md"
        f.write_text("hi")
        sid = await _make_session_row(
            db_factory,
            conversation_history=[{"role": "assistant", "content": "edited ./proj.md"}],
            main_project_path=str(tmp_path),
        )
        scanner = _scanner(settings, db_factory)
        assert await scanner.scan(sid) == 1

    async def test_scan_missing_session_no_history(self, settings: Settings, db_factory):
        scanner = _scanner(settings, db_factory)
        # Session id with no row and no messages -> nothing found.
        assert await scanner.scan(uuid.uuid4()) == 0
