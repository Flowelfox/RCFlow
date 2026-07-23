"""Tests for native RCFlow tools (src/core/native_tools/)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.buffer import MessageType
from src.core.native_tools import NativeToolContext, NativeToolRegistry
from src.core.native_tools import notify as notify_mod
from src.core.native_tools import register_artifact as artifact_mod
from src.core.native_tools import rename_session as rename_mod
from src.core.native_tools import session_status as status_mod
from src.core.native_tools import tasks as tasks_mod
from src.core.session import ActiveSession, SessionType


def _session() -> ActiveSession:
    return ActiveSession("sid-1", SessionType.LONG_RUNNING)


def _router() -> MagicMock:
    router = MagicMock()
    router._settings = MagicMock(RCFLOW_BACKEND_ID="be-1")
    router._session_manager = MagicMock()
    router._artifact_scanner = MagicMock()
    router._db_session_factory = None
    return router


def _ctx() -> NativeToolContext:
    return NativeToolContext(session=_session(), router=_router())


class TestRegistry:
    @pytest.mark.asyncio
    async def test_resolves_and_runs(self) -> None:
        reg = NativeToolRegistry(_router())
        out = await reg.dispatch(_session(), "src.core.native_tools.notify:run", {"message": "hi"})
        assert "hi" in out

    def test_invalid_callable_string(self) -> None:
        reg = NativeToolRegistry(_router())
        with pytest.raises(ValueError, match="Invalid native tool callable"):
            reg._resolve("no-colon")

    def test_missing_attr(self) -> None:
        reg = NativeToolRegistry(_router())
        with pytest.raises(ValueError, match="not found or not callable"):
            reg._resolve("src.core.native_tools.notify:nonexistent")


def _drain_notifications(queue) -> list:
    """Pull NOTIFICATION messages already queued for a live subscriber (ephemeral)."""
    out = []
    while not queue.empty():
        msg = queue.get_nowait()
        if msg is not None and msg.message_type == MessageType.NOTIFICATION:
            out.append(msg)
    return out


class TestNotify:
    @pytest.mark.asyncio
    async def test_pushes_notification(self) -> None:
        ctx = _ctx()
        # Subscribe first: notify is ephemeral — it reaches live subscribers only.
        queue = ctx.session.buffer.subscribe_text("sub")
        out = await notify_mod.run(ctx, {"message": "done", "level": "success"})
        assert "done" in out
        msgs = _drain_notifications(queue)
        assert msgs and msgs[0].data["content"] == "done" and msgs[0].data["level"] == "success"

    @pytest.mark.asyncio
    async def test_not_persisted_to_history(self) -> None:
        ctx = _ctx()
        await notify_mod.run(ctx, {"message": "ephemeral", "level": "info"})
        # Ephemeral → never archived, so it must not replay into the transcript.
        assert not [m for m in ctx.session.buffer.text_history if m.message_type == MessageType.NOTIFICATION]

    @pytest.mark.asyncio
    async def test_empty_message_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            await notify_mod.run(_ctx(), {"message": "  "})

    @pytest.mark.asyncio
    async def test_bad_level_defaults_info(self) -> None:
        ctx = _ctx()
        queue = ctx.session.buffer.subscribe_text("sub")
        await notify_mod.run(ctx, {"message": "x", "level": "bogus"})
        msgs = _drain_notifications(queue)
        assert msgs and msgs[0].data["level"] == "info"


class TestSessionStatus:
    @pytest.mark.asyncio
    async def test_returns_json_status(self) -> None:
        ctx = _ctx()
        ctx.session.metadata["selected_worktree_path"] = "/wt/x"
        ctx.session.update_todos([{"content": "a", "status": "pending"}])
        out = json.loads(await status_mod.run(ctx, {}))
        assert out["session_id"] == "sid-1"
        assert out["selected_worktree_path"] == "/wt/x"
        assert out["todos"] == [{"content": "a", "status": "pending"}]


class TestRenameSession:
    @pytest.mark.asyncio
    async def test_sets_title_and_broadcasts(self) -> None:
        ctx = _ctx()
        out = await rename_mod.run(ctx, {"title": "My work"})
        assert ctx.session.title == "My work"
        assert "My work" in out
        ctx.router._fire_persist_session_metadata.assert_called_once()
        ctx.router._session_manager.broadcast_session_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_empty_title_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            await rename_mod.run(_ctx(), {"title": ""})


class TestRegisterArtifact:
    @pytest.mark.asyncio
    async def test_registers_existing_path(self) -> None:
        ctx = _ctx()
        ctx.router._artifact_scanner.register_paths = AsyncMock(return_value=(1, 0))
        out = await artifact_mod.run(ctx, {"file_path": "/p/report.md"})
        assert "registered" in out
        ctx.router._artifact_scanner.register_paths.assert_awaited_once()
        ctx.router._fire_realtime_artifact_scan.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_match_reports_clearly(self) -> None:
        ctx = _ctx()
        ctx.router._artifact_scanner.register_paths = AsyncMock(return_value=(0, 0))
        out = await artifact_mod.run(ctx, {"file_path": "/p/missing.md"})
        assert "No artifact registered" in out

    @pytest.mark.asyncio
    async def test_missing_path_rejected(self) -> None:
        with pytest.raises(ValueError, match="file_path"):
            await artifact_mod.run(_ctx(), {})


class TestTaskUpdateValidation:
    @pytest.mark.asyncio
    async def test_ai_cannot_set_done(self) -> None:
        ctx = _ctx()
        ctx.router._db_session_factory = MagicMock()  # not reached; validation first
        with pytest.raises(ValueError, match="may not mark tasks done"):
            await tasks_mod.update_task(ctx, {"task_id": "00000000-0000-0000-0000-000000000001", "status": "done"})

    @pytest.mark.asyncio
    async def test_bad_uuid_rejected(self) -> None:
        ctx = _ctx()
        ctx.router._db_session_factory = MagicMock()
        with pytest.raises(ValueError, match="Invalid task_id"):
            await tasks_mod.update_task(ctx, {"task_id": "not-a-uuid", "status": "todo"})

    @pytest.mark.asyncio
    async def test_no_db_rejected(self) -> None:
        ctx = _ctx()  # router._db_session_factory is None
        with pytest.raises(RuntimeError, match="requires a database"):
            await tasks_mod.create_task(ctx, {"subject": "x"})
