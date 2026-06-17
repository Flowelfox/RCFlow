"""Additional ToolManager coverage: dispatch, updates, uninstall, version files."""

from __future__ import annotations

import asyncio
import hashlib
import tarfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.config import Settings
from src.services.tool_manager import (
    ManagedTool,
    ToolManager,
    _detect_claude_platform,
    _detect_codex_target,
)


@contextmanager
def _mock_httpx_transport(handler):
    """Patch ``httpx.AsyncClient`` so each instance uses ``handler`` as transport."""
    _orig = httpx.AsyncClient

    class _PatchedClient(_orig):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    with patch("src.services.tools.manager.httpx.AsyncClient", _PatchedClient):
        yield


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        RCFLOW_HOST="127.0.0.1",
        RCFLOW_PORT=8765,
        RCFLOW_API_KEY="test-key",
        DATABASE_URL="postgresql+asyncpg://test:test@localhost:5432/test",
        ANTHROPIC_API_KEY="test",
        TOOL_AUTO_UPDATE=True,
        TOOL_UPDATE_INTERVAL_HOURS=6.0,
    )


@pytest.fixture
def tool_manager(settings: Settings, tmp_path: Path) -> ToolManager:
    tm = ToolManager(settings)
    tm._base_dir = tmp_path / "managed-tools"
    return tm


def _managed_tool(name: str, **kw) -> ManagedTool:
    base = {
        "name": name,
        "binary_name": name,
        "binary_path": f"/managed/{name}",
        "current_version": "1.0.0",
        "managed": True,
        "managed_path": f"/managed/{name}",
    }
    base.update(kw)
    return ManagedTool(**base)


# ---------------------------------------------------------------------------
# tool_names / get_binary_path
# ---------------------------------------------------------------------------


class TestSimpleAccessors:
    def test_tool_names_always_includes_known(self, tool_manager: ToolManager):
        assert tool_manager.tool_names == {"claude_code", "codex", "opencode"}

    def test_tool_names_includes_detected(self, tool_manager: ToolManager):
        tool_manager._tools["extra"] = _managed_tool("extra")
        assert "extra" in tool_manager.tool_names

    def test_get_binary_path_present(self, tool_manager: ToolManager):
        tool_manager._tools["codex"] = _managed_tool("codex", binary_path="/x/codex")
        assert tool_manager.get_binary_path("codex") == "/x/codex"

    def test_get_binary_path_missing(self, tool_manager: ToolManager):
        assert tool_manager.get_binary_path("nope") is None

    def test_managed_binary_path_unknown_raises(self, tool_manager: ToolManager):
        with pytest.raises(ValueError, match="Unknown tool"):
            tool_manager._managed_binary_path("bogus")


# ---------------------------------------------------------------------------
# Version files
# ---------------------------------------------------------------------------


class TestVersionFiles:
    def test_write_then_read(self, tool_manager: ToolManager):
        tool_manager._base_dir.mkdir(parents=True)
        (tool_manager._base_dir / "codex").mkdir()
        tool_manager._write_version_file("codex", "0.106.0")
        assert tool_manager._read_version_file("codex") == "0.106.0"

    def test_read_missing_returns_none(self, tool_manager: ToolManager):
        assert tool_manager._read_version_file("codex") is None

    def test_read_empty_returns_none(self, tool_manager: ToolManager):
        (tool_manager._base_dir / "codex").mkdir(parents=True)
        vf = tool_manager._managed_binary_path("codex").with_suffix(".version")
        vf.write_text("   ")
        assert tool_manager._read_version_file("codex") is None

    def test_write_swallows_oserror(self, tool_manager: ToolManager):
        # Parent dir does not exist → write fails → logged, not raised.
        tool_manager._write_version_file("codex", "1.2.3")
        assert tool_manager._read_version_file("codex") is None


# ---------------------------------------------------------------------------
# detect_tool — version-file fallback
# ---------------------------------------------------------------------------


class TestDetectVersionFallback:
    @pytest.mark.asyncio
    async def test_detect_uses_version_file_when_exec_fails(self, tool_manager: ToolManager):
        managed_dir = tool_manager._base_dir / "codex"
        managed_dir.mkdir(parents=True)
        binary = managed_dir / "codex"
        binary.write_text("not a real binary")
        binary.chmod(0o755)
        (managed_dir / "codex.version").write_text("0.99.0")

        with patch.object(ToolManager, "_get_installed_version", new_callable=AsyncMock, return_value=None):
            tool = await tool_manager.detect_tool("codex")

        assert tool.current_version == "0.99.0"
        assert tool.managed is True

    @pytest.mark.asyncio
    async def test_detect_uses_inmemory_cache_when_exec_and_file_fail(self, tool_manager: ToolManager):
        managed_dir = tool_manager._base_dir / "codex"
        managed_dir.mkdir(parents=True)
        binary = managed_dir / "codex"
        binary.write_text("not a real binary")
        binary.chmod(0o755)
        tool_manager._tools["codex"] = _managed_tool("codex", binary_path=str(binary), current_version="0.5.0")

        with patch.object(ToolManager, "_get_installed_version", new_callable=AsyncMock, return_value=None):
            tool = await tool_manager.detect_tool("codex")

        assert tool.current_version == "0.5.0"


# ---------------------------------------------------------------------------
# install_tool / install_tool_streaming dispatch
# ---------------------------------------------------------------------------


class TestInstallDispatch:
    @pytest.mark.asyncio
    async def test_install_tool_unknown_raises(self, tool_manager: ToolManager):
        with pytest.raises(ValueError, match="Unknown tool"):
            await tool_manager.install_tool("bogus")

    @pytest.mark.asyncio
    async def test_install_tool_dispatches_and_stores(self, tool_manager: ToolManager):
        fake = _managed_tool("codex")
        with patch.object(tool_manager, "_install_codex", new_callable=AsyncMock, return_value=fake):
            result = await tool_manager.install_tool("codex")
        assert result is fake
        assert tool_manager._tools["codex"] is fake

    @pytest.mark.asyncio
    async def test_install_streaming_unknown_yields_error(self, tool_manager: ToolManager):
        events = [e async for e in tool_manager.install_tool_streaming("bogus")]
        assert events == [{"step": "error", "message": "Unknown tool: bogus"}]


# ---------------------------------------------------------------------------
# check_updates / update_all
# ---------------------------------------------------------------------------


class TestCheckUpdates:
    @pytest.mark.asyncio
    async def test_check_updates_sets_latest(self, tool_manager: ToolManager):
        tool_manager._tools = {
            "claude_code": _managed_tool("claude_code"),
            "codex": _managed_tool("codex"),
            "opencode": _managed_tool("opencode"),
        }
        with (
            patch.object(ToolManager, "_get_latest_claude_version", new_callable=AsyncMock, return_value="9.9.9"),
            patch.object(
                ToolManager, "_get_latest_codex_version", new_callable=AsyncMock, return_value=("8.8.8", "rust-v8.8.8")
            ),
            patch.object(ToolManager, "_get_latest_opencode_version", new_callable=AsyncMock, return_value="7.7.7"),
        ):
            result = await tool_manager.check_updates()

        assert result["claude_code"].latest_version == "9.9.9"
        assert result["codex"].latest_version == "8.8.8"
        assert result["opencode"].latest_version == "7.7.7"

    @pytest.mark.asyncio
    async def test_check_updates_swallows_errors(self, tool_manager: ToolManager):
        tool_manager._tools = {"claude_code": _managed_tool("claude_code")}
        with patch.object(
            ToolManager, "_get_latest_claude_version", new_callable=AsyncMock, side_effect=RuntimeError("boom")
        ):
            result = await tool_manager.check_updates()
        # No latest set, but no raise.
        assert result["claude_code"].latest_version is None

    @pytest.mark.asyncio
    async def test_update_all_collects_results_and_swallows(self, tool_manager: ToolManager):
        tool_manager._tools = {"codex": _managed_tool("codex"), "opencode": _managed_tool("opencode")}
        updated_codex = _managed_tool("codex", current_version="2.0.0")

        async def fake_update(name):
            if name == "codex":
                return updated_codex
            raise RuntimeError("opencode failed")

        with patch.object(tool_manager, "update_tool", side_effect=fake_update):
            result = await tool_manager.update_all()

        assert result["codex"] is updated_codex
        # Failed update falls back to the existing tool entry.
        assert result["opencode"] is tool_manager._tools["opencode"]


# ---------------------------------------------------------------------------
# uninstall_tool
# ---------------------------------------------------------------------------


class TestUninstall:
    @pytest.mark.asyncio
    async def test_uninstall_unknown_raises(self, tool_manager: ToolManager):
        with pytest.raises(ValueError, match="Unknown tool"):
            await tool_manager.uninstall_tool("codex")

    @pytest.mark.asyncio
    async def test_uninstall_no_managed_path_raises(self, tool_manager: ToolManager):
        tool_manager._tools["codex"] = ManagedTool(name="codex", binary_name="codex")
        with pytest.raises(ValueError, match="No managed installation"):
            await tool_manager.uninstall_tool("codex")

    @pytest.mark.asyncio
    async def test_uninstall_removes_binary_and_version(self, tool_manager: ToolManager):
        managed_dir = tool_manager._base_dir / "codex"
        managed_dir.mkdir(parents=True)
        binary = managed_dir / "codex"
        binary.write_text("bin")
        version = managed_dir / "codex.version"
        version.write_text("1.0.0")

        tool_manager._tools["codex"] = _managed_tool("codex", managed_path=str(binary), binary_path=str(binary))

        with patch.object(
            tool_manager,
            "detect_tool",
            new_callable=AsyncMock,
            return_value=ManagedTool(name="codex", binary_name="codex"),
        ):
            result = await tool_manager.uninstall_tool("codex")

        assert not binary.exists()
        assert not version.exists()
        assert result.binary_path is None
        assert tool_manager._tools["codex"] is result


# ---------------------------------------------------------------------------
# update_tool_streaming branches
# ---------------------------------------------------------------------------


class TestUpdateStreaming:
    @pytest.mark.asyncio
    async def test_no_binary_delegates_to_install(self, tool_manager: ToolManager):
        tool_manager._tools["codex"] = ManagedTool(name="codex", binary_name="codex")

        async def fake_install(name):
            yield {"step": "done", "message": "installed"}

        with patch.object(tool_manager, "install_tool_streaming", side_effect=fake_install):
            events = [e async for e in tool_manager.update_tool_streaming("codex")]
        assert events[-1]["step"] == "done"

    @pytest.mark.asyncio
    async def test_unmanaged_skips(self, tool_manager: ToolManager):
        tool_manager._tools["codex"] = _managed_tool("codex", managed=False)
        events = [e async for e in tool_manager.update_tool_streaming("codex")]
        assert events == [{"step": "done", "message": "External tool — skipping update"}]

    @pytest.mark.asyncio
    async def test_already_up_to_date(self, tool_manager: ToolManager):
        tool_manager._tools["claude_code"] = _managed_tool("claude_code", current_version="3.0.0")
        with patch.object(ToolManager, "_get_latest_claude_version", new_callable=AsyncMock, return_value="3.0.0"):
            events = [e async for e in tool_manager.update_tool_streaming("claude_code")]
        assert events[-1] == {"step": "done", "message": "Already up to date (v3.0.0)"}

    @pytest.mark.asyncio
    async def test_latest_check_fails(self, tool_manager: ToolManager):
        tool_manager._tools["opencode"] = _managed_tool("opencode")
        with patch.object(ToolManager, "_get_latest_opencode_version", new_callable=AsyncMock, return_value=None):
            events = [e async for e in tool_manager.update_tool_streaming("opencode")]
        assert events[-1] == {"step": "done", "message": "Could not check latest version"}

    @pytest.mark.asyncio
    async def test_newer_version_delegates_to_install(self, tool_manager: ToolManager):
        tool_manager._tools["codex"] = _managed_tool("codex", current_version="1.0.0")

        async def fake_install(name):
            yield {"step": "done", "message": "installed v2.0.0"}

        with (
            patch.object(
                ToolManager, "_get_latest_codex_version", new_callable=AsyncMock, return_value=("2.0.0", "rust-v2.0.0")
            ),
            patch.object(tool_manager, "install_tool_streaming", side_effect=fake_install),
        ):
            events = [e async for e in tool_manager.update_tool_streaming("codex")]

        assert tool_manager._tools["codex"].latest_version == "2.0.0"
        assert events[-1]["step"] == "done"

    @pytest.mark.asyncio
    async def test_unsupported_tool_name(self, tool_manager: ToolManager):
        tool_manager._tools["weird"] = _managed_tool("weird")
        events = [e async for e in tool_manager.update_tool_streaming("weird")]
        assert events[-1] == {"step": "done", "message": "Update check not supported for weird"}


# ---------------------------------------------------------------------------
# update_tool (non-streaming) extra branches
# ---------------------------------------------------------------------------


class TestUpdateTool:
    @pytest.mark.asyncio
    async def test_unmanaged_unsupported_name_returns_tool(self, tool_manager: ToolManager):
        tool = _managed_tool("weird", managed=False)
        tool_manager._tools["weird"] = tool
        result = await tool_manager.update_tool("weird")
        assert result is tool

    @pytest.mark.asyncio
    async def test_managed_dispatches_opencode(self, tool_manager: ToolManager):
        tool = _managed_tool("opencode")
        tool_manager._tools["opencode"] = tool
        updated = _managed_tool("opencode", current_version="2.0.0")
        with patch.object(tool_manager, "_update_opencode", new_callable=AsyncMock, return_value=updated):
            result = await tool_manager.update_tool("opencode")
        assert result is updated

    @pytest.mark.asyncio
    async def test_managed_unsupported_name_returns_tool(self, tool_manager: ToolManager):
        tool = _managed_tool("weird")
        tool_manager._tools["weird"] = tool
        result = await tool_manager.update_tool("weird")
        assert result is tool

    @pytest.mark.asyncio
    async def test_update_opencode_up_to_date(self, tool_manager: ToolManager):
        tool = _managed_tool("opencode", current_version="1.0.0")
        with patch.object(ToolManager, "_get_latest_opencode_version", new_callable=AsyncMock, return_value="1.0.0"):
            result = await tool_manager._update_opencode(tool)
        assert result is tool
        assert result.latest_version == "1.0.0"

    @pytest.mark.asyncio
    async def test_update_opencode_latest_none(self, tool_manager: ToolManager):
        tool = _managed_tool("opencode", current_version="1.0.0")
        with patch.object(ToolManager, "_get_latest_opencode_version", new_callable=AsyncMock, return_value=None):
            result = await tool_manager._update_opencode(tool)
        assert result is tool

    @pytest.mark.asyncio
    async def test_update_claude_code_installs_newer(self, tool_manager: ToolManager):
        tool = _managed_tool("claude_code", current_version="1.0.0")
        updated = _managed_tool("claude_code", current_version="2.0.0")
        with (
            patch.object(ToolManager, "_get_latest_claude_version", new_callable=AsyncMock, return_value="2.0.0"),
            patch.object(tool_manager, "_install_claude_code", new_callable=AsyncMock, return_value=updated),
        ):
            result = await tool_manager._update_claude_code(tool)
        assert result is updated
        assert tool_manager._tools["claude_code"] is updated

    @pytest.mark.asyncio
    async def test_update_claude_code_latest_none(self, tool_manager: ToolManager):
        tool = _managed_tool("claude_code", current_version="1.0.0")
        with patch.object(ToolManager, "_get_latest_claude_version", new_callable=AsyncMock, return_value=None):
            result = await tool_manager._update_claude_code(tool)
        assert result is tool

    @pytest.mark.asyncio
    async def test_update_codex_installs_newer(self, tool_manager: ToolManager):
        tool = _managed_tool("codex", current_version="1.0.0")
        updated = _managed_tool("codex", current_version="2.0.0")
        with (
            patch.object(
                ToolManager, "_get_latest_codex_version", new_callable=AsyncMock, return_value=("2.0.0", "rust-v2.0.0")
            ),
            patch.object(tool_manager, "_install_codex", new_callable=AsyncMock, return_value=updated),
        ):
            result = await tool_manager._update_codex(tool)
        assert result is updated
        assert tool_manager._tools["codex"] is updated

    @pytest.mark.asyncio
    async def test_update_codex_latest_none(self, tool_manager: ToolManager):
        tool = _managed_tool("codex", current_version="1.0.0")
        with patch.object(ToolManager, "_get_latest_codex_version", new_callable=AsyncMock, return_value=(None, None)):
            result = await tool_manager._update_codex(tool)
        assert result is tool

    @pytest.mark.asyncio
    async def test_update_codex_up_to_date(self, tool_manager: ToolManager):
        tool = _managed_tool("codex", current_version="1.0.0")
        with patch.object(
            ToolManager, "_get_latest_codex_version", new_callable=AsyncMock, return_value=("1.0.0", "rust-v1.0.0")
        ):
            result = await tool_manager._update_codex(tool)
        assert result is tool

    @pytest.mark.asyncio
    async def test_update_opencode_installs_newer(self, tool_manager: ToolManager):
        tool = _managed_tool("opencode", current_version="1.0.0")
        updated = _managed_tool("opencode", current_version="2.0.0")
        with (
            patch.object(ToolManager, "_get_latest_opencode_version", new_callable=AsyncMock, return_value="2.0.0"),
            patch.object(tool_manager, "_install_opencode", new_callable=AsyncMock, return_value=updated),
        ):
            result = await tool_manager._update_opencode(tool)
        assert result is updated
        assert tool_manager._tools["opencode"] is updated


# ---------------------------------------------------------------------------
# run_update_loop (single iteration)
# ---------------------------------------------------------------------------


class TestRunUpdateLoop:
    @pytest.mark.asyncio
    async def test_skips_when_auto_update_disabled(self, tool_manager: ToolManager):
        tool_manager._settings.TOOL_AUTO_UPDATE = False

        # First sleep returns, second raises to break the infinite loop.
        sleeps = {"n": 0}

        async def fake_sleep(_):
            sleeps["n"] += 1
            if sleeps["n"] >= 2:
                raise asyncio.CancelledError

        with (
            patch("src.services.tools.manager.asyncio.sleep", side_effect=fake_sleep),
            patch.object(tool_manager, "update_tool", new_callable=AsyncMock) as mock_update,
            pytest.raises(asyncio.CancelledError),
        ):
            await tool_manager.run_update_loop()

        mock_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_updates_managed_tools(self, tool_manager: ToolManager):
        tool_manager._tools["codex"] = _managed_tool("codex", current_version="1.0.0")

        sleeps = {"n": 0}

        async def sleeper(_):
            sleeps["n"] += 1
            if sleeps["n"] >= 2:
                raise asyncio.CancelledError

        updated = _managed_tool("codex", current_version="2.0.0")
        with (
            patch("src.services.tools.manager.asyncio.sleep", side_effect=sleeper),
            patch.object(tool_manager, "update_tool", new_callable=AsyncMock, return_value=updated) as mock_update,
            pytest.raises(asyncio.CancelledError),
        ):
            await tool_manager.run_update_loop()

        mock_update.assert_awaited_with("codex")

    @pytest.mark.asyncio
    async def test_loop_swallows_update_exception(self, tool_manager: ToolManager):
        tool_manager._tools["codex"] = _managed_tool("codex")

        sleeps = {"n": 0}

        async def sleeper(_):
            sleeps["n"] += 1
            if sleeps["n"] >= 2:
                raise asyncio.CancelledError

        # The RuntimeError is caught inside the loop; only CancelledError escapes.
        with (
            patch("src.services.tools.manager.asyncio.sleep", side_effect=sleeper),
            patch.object(tool_manager, "update_tool", new_callable=AsyncMock, side_effect=RuntimeError("boom")),
            pytest.raises(asyncio.CancelledError),
        ):
            await tool_manager.run_update_loop()


# ---------------------------------------------------------------------------
# _cleanup_parked_binaries (Windows path)
# ---------------------------------------------------------------------------


class TestCleanupParked:
    def test_noop_on_posix(self, tool_manager: ToolManager):
        # Should not raise even though dirs don't exist.
        with patch("src.services.tools.manager.sys.platform", "linux"):
            tool_manager._cleanup_parked_binaries("codex")

    def test_removes_stale_old_files_on_win32(self, tool_manager: ToolManager):
        managed_dir = tool_manager._base_dir / "codex"
        managed_dir.mkdir(parents=True)
        stale = managed_dir / "codex.exe.1234.old"
        stale.write_text("stale")
        keep = managed_dir / "codex.exe"
        keep.write_text("bin")

        with patch("src.services.tools.manager.sys.platform", "win32"):
            tool_manager._cleanup_parked_binaries("codex")

        assert not stale.exists()
        assert keep.exists()

    def test_win32_missing_parent_is_noop(self, tool_manager: ToolManager):
        with patch("src.services.tools.manager.sys.platform", "win32"):
            tool_manager._cleanup_parked_binaries("codex")  # no dir → returns early


# ---------------------------------------------------------------------------
# Streaming installs — full download path with progress events
# ---------------------------------------------------------------------------


class TestStreamingInstalls:
    @pytest.mark.asyncio
    async def test_claude_code_streaming_full(self, tool_manager: ToolManager):
        content = b"#!/bin/sh\necho '3.0.0 (Claude Code)'"
        checksum = hashlib.sha256(content).hexdigest()
        plat = _detect_claude_platform()

        def handler(req: httpx.Request) -> httpx.Response:
            url = str(req.url)
            if url.endswith("/latest"):
                return httpx.Response(200, text="3.0.0")
            if url.endswith("manifest.json"):
                return httpx.Response(200, json={"platforms": {plat: {"checksum": checksum}}})
            return httpx.Response(200, content=content, headers={"content-length": str(len(content))})

        with _mock_httpx_transport(handler):
            events = [e async for e in tool_manager.install_tool_streaming("claude_code")]

        steps = [e["step"] for e in events]
        assert "downloading" in steps
        assert "verifying" in steps
        assert events[-1]["step"] == "done"
        tool = tool_manager._tools["claude_code"]
        assert tool.current_version == "3.0.0"
        assert Path(tool.binary_path).exists()

    @pytest.mark.asyncio
    async def test_claude_code_streaming_checksum_mismatch(self, tool_manager: ToolManager):
        content = b"binary"
        plat = _detect_claude_platform()

        def handler(req: httpx.Request) -> httpx.Response:
            url = str(req.url)
            if url.endswith("/latest"):
                return httpx.Response(200, text="3.0.0")
            if url.endswith("manifest.json"):
                return httpx.Response(200, json={"platforms": {plat: {"checksum": "b" * 64}}})
            return httpx.Response(200, content=content, headers={"content-length": str(len(content))})

        with _mock_httpx_transport(handler), pytest.raises(ValueError, match="Checksum mismatch"):
            async for _ in tool_manager.install_tool_streaming("claude_code"):
                pass

    @pytest.mark.asyncio
    async def test_codex_streaming_full(self, tool_manager: ToolManager, tmp_path: Path):
        target = _detect_codex_target()
        member = f"codex-{target}"
        bin_src = tmp_path / member
        bin_src.write_text("#!/bin/sh\necho 'codex-cli 0.106.0'")
        bin_src.chmod(0o755)
        tar_path = tmp_path / "codex.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(str(bin_src), arcname=member)
        tar_content = tar_path.read_bytes()
        checksum = hashlib.sha256(tar_content).hexdigest()

        def handler(req: httpx.Request) -> httpx.Response:
            url = str(req.url)
            if url.endswith(".sha256") or "checksums" in url.lower():
                return httpx.Response(200, text=f"{checksum}  codex-{target}.tar.gz\n")
            return httpx.Response(200, content=tar_content, headers={"content-length": str(len(tar_content))})

        with (
            patch.object(
                ToolManager,
                "_get_latest_codex_version",
                new_callable=AsyncMock,
                return_value=("0.106.0", "rust-v0.106.0"),
            ),
            patch("src.services.tools.manager._verify_binary", new_callable=AsyncMock, return_value=(True, "")),
            patch(
                "src.services.tools.manager._verify_codex_asset_checksum",
                return_value=None,
            ),
            _mock_httpx_transport(handler),
        ):
            events = [e async for e in tool_manager.install_tool_streaming("codex")]

        assert events[-1]["step"] == "done"
        tool = tool_manager._tools["codex"]
        assert tool.current_version == "0.106.0"
        assert Path(tool.binary_path).exists()

    @pytest.mark.asyncio
    async def test_codex_streaming_version_unavailable(self, tool_manager: ToolManager):
        with patch.object(ToolManager, "_get_latest_codex_version", new_callable=AsyncMock, return_value=(None, None)):
            events = [e async for e in tool_manager.install_tool_streaming("codex")]
        assert events[-1] == {"step": "error", "message": "Could not determine latest Codex version"}

    @pytest.mark.asyncio
    async def test_opencode_streaming_version_unavailable(self, tool_manager: ToolManager):
        with patch.object(ToolManager, "_get_latest_opencode_version", new_callable=AsyncMock, return_value=None):
            events = [e async for e in tool_manager.install_tool_streaming("opencode")]
        assert events[-1] == {"step": "error", "message": "Could not determine latest OpenCode version"}
