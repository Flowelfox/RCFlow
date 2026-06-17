"""Branch-coverage tests for ContextBuilder._build_file_context and the
success paths of ``_handle_direct_prompt``.

``test_context.py`` covers the regex/parse helpers and the no-DB fast path;
this file exercises the DB-backed ``$file`` resolution (text/binary/missing/
truncated/dedup/not-found) and the direct-tool execution success branches.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.core.buffer import MessageType
from src.core.context import ContextBuilder
from src.core.session import ActiveSession, SessionStatus, SessionType
from src.database.models import Artifact, Base

if TYPE_CHECKING:
    from pathlib import Path

_BACKEND_ID = "backend-ctx"


# ---------------------------------------------------------------------------
# Hosts
# ---------------------------------------------------------------------------


class _ContextHost(ContextBuilder):
    def __init__(self, tool_registry=None, settings=None, db_session_factory=None) -> None:
        router = MagicMock()
        router._tool_registry = tool_registry or MagicMock()
        router._settings = settings
        router._db_session_factory = db_session_factory
        super().__init__(router)


# ---------------------------------------------------------------------------
# DB fixture + artifact seeding
# ---------------------------------------------------------------------------


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


async def _seed_artifact(db_factory, *, file_path: str, file_name: str, file_extension: str, **kw) -> None:
    async with db_factory() as db:
        db.add(
            Artifact(
                backend_id=_BACKEND_ID,
                file_path=file_path,
                file_name=file_name,
                file_extension=file_extension,
                file_size=kw.get("file_size", 10),
                mime_type=kw.get("mime_type"),
                modified_at=kw.get("modified_at", datetime.now(UTC)),
            )
        )
        await db.commit()


def _settings() -> MagicMock:
    s = MagicMock()
    s.RCFLOW_BACKEND_ID = _BACKEND_ID
    return s


# ---------------------------------------------------------------------------
# _build_file_context
# ---------------------------------------------------------------------------


class TestBuildFileContext:
    async def test_text_file_content_included(self, db_factory, tmp_path: Path):
        f = tmp_path / "notes.md"
        f.write_text("hello world")
        await _seed_artifact(db_factory, file_path=str(f), file_name="notes.md", file_extension=".md")
        host = _ContextHost(settings=_settings(), db_session_factory=db_factory)
        ctx = await host._build_file_context(["notes.md"])
        assert ctx is not None
        assert "hello world" in ctx
        assert "```md" in ctx

    async def test_unknown_reference_returns_none(self, db_factory):
        host = _ContextHost(settings=_settings(), db_session_factory=db_factory)
        assert await host._build_file_context(["does_not_exist.md"]) is None

    async def test_missing_on_disk_reports_not_found(self, db_factory, tmp_path: Path):
        missing = str(tmp_path / "gone.md")
        await _seed_artifact(db_factory, file_path=missing, file_name="gone.md", file_extension=".md")
        host = _ContextHost(settings=_settings(), db_session_factory=db_factory)
        ctx = await host._build_file_context(["gone.md"])
        assert ctx is not None
        assert "File not found on disk" in ctx

    async def test_binary_file_metadata_only(self, db_factory, tmp_path: Path):
        f = tmp_path / "logo.png"
        f.write_bytes(b"\x89PNG\r\n")
        await _seed_artifact(
            db_factory,
            file_path=str(f),
            file_name="logo.png",
            file_extension=".png",
            mime_type="image/png",
            file_size=2048,
        )
        host = _ContextHost(settings=_settings(), db_session_factory=db_factory)
        ctx = await host._build_file_context(["logo.png"])
        assert ctx is not None
        assert "Binary/non-text file" in ctx
        assert "image/png" in ctx
        assert "KB" in ctx  # size formatted

    async def test_large_text_file_truncated(self, db_factory, tmp_path: Path):
        f = tmp_path / "big.txt"
        f.write_text("z" * (ContextBuilder._MAX_FILE_CONTEXT_SIZE + 500))
        await _seed_artifact(db_factory, file_path=str(f), file_name="big.txt", file_extension=".txt")
        host = _ContextHost(settings=_settings(), db_session_factory=db_factory)
        ctx = await host._build_file_context(["big.txt"])
        assert ctx is not None
        assert "truncated" in ctx

    async def test_duplicate_references_deduplicated(self, db_factory, tmp_path: Path):
        f = tmp_path / "dup.md"
        f.write_text("content")
        await _seed_artifact(db_factory, file_path=str(f), file_name="dup.md", file_extension=".md")
        host = _ContextHost(settings=_settings(), db_session_factory=db_factory)
        ctx = await host._build_file_context(["dup.md", "DUP.md"])
        assert ctx is not None
        # The file appears only once despite case-variant duplicate references.
        assert ctx.count("content") == 1


# ---------------------------------------------------------------------------
# _handle_direct_prompt — success paths
# ---------------------------------------------------------------------------


class _MockTool:
    def __init__(self, name, description, executor, parameters=None):
        self.name = name
        self.description = description
        self.executor = executor
        self.parameters = parameters or {"properties": {}, "required": []}


def _registry_with(*tools) -> MagicMock:
    reg = MagicMock()
    tmap = {t.name.lower(): t for t in tools}
    reg.get.side_effect = lambda n: tmap.get(n.lower())
    reg.list_tools.return_value = list(tools)
    return reg


class _DirectHost(_ContextHost):
    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self._r._execute_tool = AsyncMock()
        self._r._fire_persist_session_metadata = MagicMock()
        self._r._fire_archive_task = MagicMock()


class TestHandleDirectPromptSuccess:
    async def test_non_agent_tool_completes_and_sets_title(self):
        tool = _MockTool("run_shell", "shell", "shell")
        host = _DirectHost(tool_registry=_registry_with(tool))
        session = ActiveSession("s1", SessionType.CONVERSATIONAL)
        session.set_active()

        await host._handle_direct_prompt(session, "#run_shell echo hello world this is a long prompt to truncate")

        host._r._execute_tool.assert_awaited_once()
        # Non-agent tool -> turn complete emitted + title set.
        types = [m.message_type for m in session.buffer.text_history]
        assert MessageType.TURN_COMPLETE in types
        assert session.title is not None
        host._r._fire_persist_session_metadata.assert_called_once()

    async def test_working_directory_defaults_to_main_project_path(self):
        tool = _MockTool("run_shell", "shell", "shell")
        host = _DirectHost(tool_registry=_registry_with(tool))
        session = ActiveSession("s2", SessionType.CONVERSATIONAL)
        session.set_active()
        session.main_project_path = "/home/user/Projects/RCFlow"

        await host._handle_direct_prompt(session, "#run_shell ls")

        call = host._r._execute_tool.call_args.args[1]
        assert call.tool_input.get("working_directory") == "/home/user/Projects/RCFlow"

    async def test_execute_tool_error_pushes_error(self):
        tool = _MockTool("run_shell", "shell", "shell")
        host = _DirectHost(tool_registry=_registry_with(tool))
        host._r._execute_tool = AsyncMock(side_effect=RuntimeError("boom"))
        session = ActiveSession("s3", SessionType.CONVERSATIONAL)
        session.set_active()

        await host._handle_direct_prompt(session, "#run_shell do it")

        types = [m.message_type for m in session.buffer.text_history]
        assert MessageType.ERROR in types
        # Tool errored before turn complete; no TURN_COMPLETE emitted.
        assert MessageType.TURN_COMPLETE not in types

    async def test_agent_tool_does_not_emit_turn_complete(self):
        tool = _MockTool("claude_code", "agent", "claude_code")
        host = _DirectHost(tool_registry=_registry_with(tool))
        session = ActiveSession("s4", SessionType.CONVERSATIONAL)
        session.set_active()
        # Simulate that the executor was attached during the agent launch.
        session.claude_code_executor = object()

        await host._handle_direct_prompt(session, "#claude_code fix the bug")

        host._r._execute_tool.assert_awaited_once()
        types = [m.message_type for m in session.buffer.text_history]
        # Agent executors emit their own terminal messages; no TURN_COMPLETE here.
        assert MessageType.TURN_COMPLETE not in types
        assert session.status == SessionStatus.ACTIVE
