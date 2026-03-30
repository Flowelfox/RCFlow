from __future__ import annotations

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
    _detect_opencode_asset,
    _parse_version,
)

# ---------------------------------------------------------------------------
# Helper: patch httpx.AsyncClient to use a mock transport
# ---------------------------------------------------------------------------


@contextmanager
def _mock_httpx_transport(handler):
    """Patch ``httpx.AsyncClient`` so that every new instance uses the given
    ``handler`` callable as its transport.  The handler receives an
    ``httpx.Request`` and must return an ``httpx.Response``.
    """
    _orig = httpx.AsyncClient

    class _PatchedClient(_orig):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    with patch("src.services.tool_manager.httpx.AsyncClient", _PatchedClient):
        yield


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# _parse_version
# ---------------------------------------------------------------------------


class TestParseVersion:
    def test_claude_code_version(self):
        assert _parse_version("claude_code", "2.1.63 (Claude Code)") == "2.1.63"

    def test_claude_code_version_bare(self):
        assert _parse_version("claude_code", "2.1.63") == "2.1.63"

    def test_codex_version(self):
        assert _parse_version("codex", "codex-cli 0.91.0") == "0.91.0"

    def test_codex_version_bare(self):
        assert _parse_version("codex", "0.106.0") == "0.106.0"

    def test_unknown_tool(self):
        assert _parse_version("unknown", "1.0.0") is None

    def test_empty_string(self):
        assert _parse_version("claude_code", "") is None

    def test_no_version_found(self):
        assert _parse_version("codex", "no version here") is None

    def test_opencode_version_bare(self):
        assert _parse_version("opencode", "1.3.7") == "1.3.7"

    def test_opencode_version_prefixed(self):
        # tolerate "opencode 1.3.7" just in case
        assert _parse_version("opencode", "opencode 1.3.7") == "1.3.7"


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


class TestPlatformDetection:
    @patch("src.services.tool_manager.platform.machine", return_value="x86_64")
    @patch("src.services.tool_manager._is_musl", return_value=False)
    def test_claude_platform_x64(self, _musl, _machine):
        assert _detect_claude_platform() == "linux-x64"

    @patch("src.services.tool_manager.platform.machine", return_value="aarch64")
    @patch("src.services.tool_manager._is_musl", return_value=False)
    def test_claude_platform_arm64(self, _musl, _machine):
        assert _detect_claude_platform() == "linux-arm64"

    @patch("src.services.tool_manager.platform.machine", return_value="x86_64")
    @patch("src.services.tool_manager._is_musl", return_value=True)
    def test_claude_platform_musl(self, _musl, _machine):
        assert _detect_claude_platform() == "linux-x64-musl"

    @patch("src.services.tool_manager.platform.machine", return_value="x86_64")
    @patch("src.services.tool_manager._is_musl", return_value=False)
    @patch("src.services.tool_manager._glibc_too_old", return_value=False)
    def test_codex_target_x64_gnu(self, _glibc, _musl, _machine):
        assert _detect_codex_target() == "x86_64-unknown-linux-gnu"

    @patch("src.services.tool_manager.platform.machine", return_value="aarch64")
    @patch("src.services.tool_manager._is_musl", return_value=True)
    def test_codex_target_arm64_musl(self, _musl, _machine):
        assert _detect_codex_target() == "aarch64-unknown-linux-musl"

    @patch("src.services.tool_manager.platform.machine", return_value="ppc64le")
    def test_unsupported_arch_claude(self, _machine):
        with pytest.raises(RuntimeError, match="Unsupported"):
            _detect_claude_platform()

    @patch("src.services.tool_manager.platform.machine", return_value="ppc64le")
    def test_unsupported_arch_codex(self, _machine):
        with pytest.raises(RuntimeError, match="Unsupported"):
            _detect_codex_target()


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


class TestDetection:
    @pytest.mark.asyncio
    async def test_detect_managed_dir(self, tool_manager: ToolManager, tmp_path: Path):
        """When a binary exists in the managed directory, detect it."""
        managed_dir = tool_manager._base_dir / "claude-code"
        managed_dir.mkdir(parents=True)
        fake_binary = managed_dir / "claude"
        fake_binary.write_text("#!/bin/sh\necho '2.1.63 (Claude Code)'")
        fake_binary.chmod(0o755)

        tool = await tool_manager.detect_tool("claude_code")
        assert tool.binary_path == str(fake_binary)
        assert tool.managed is True

    @pytest.mark.asyncio
    async def test_detect_from_path(self, tool_manager: ToolManager):
        """When binary is found on PATH, detect it as unmanaged."""
        with (
            patch("src.services.tool_manager.shutil.which", return_value="/usr/bin/codex"),
            patch.object(
                ToolManager,
                "_get_installed_version",
                new_callable=AsyncMock,
                return_value="0.91.0",
            ),
        ):
            tool = await tool_manager.detect_tool("codex")
            assert tool.binary_path == "/usr/bin/codex"
            assert tool.managed is False
            assert tool.current_version == "0.91.0"

    @pytest.mark.asyncio
    async def test_detect_not_found(self, tool_manager: ToolManager):
        """When binary is not found anywhere, return empty tool."""
        with patch("src.services.tool_manager.shutil.which", return_value=None):
            tool = await tool_manager.detect_tool("codex")
            assert tool.binary_path is None
            assert tool.managed is False


# ---------------------------------------------------------------------------
# Installation — Claude Code
# ---------------------------------------------------------------------------


class TestInstallClaudeCode:
    @pytest.mark.asyncio
    async def test_install_claude_code(self, tool_manager: ToolManager, tmp_path: Path):
        """Test Claude Code installation with mocked HTTP responses."""
        fake_binary_content = b"#!/bin/sh\necho '3.0.0 (Claude Code)'"
        checksum = hashlib.sha256(fake_binary_content).hexdigest()
        plat = _detect_claude_platform()

        responses = [
            httpx.Response(200, text="3.0.0"),
            httpx.Response(200, json={"platforms": {plat: {"checksum": checksum}}}),
            httpx.Response(200, content=fake_binary_content),
        ]

        with _mock_httpx_transport(lambda req: responses.pop(0)):
            tool = await tool_manager._install_claude_code()

        assert tool.managed is True
        assert tool.current_version == "3.0.0"
        assert tool.binary_path is not None
        assert Path(tool.binary_path).exists()

    @pytest.mark.asyncio
    async def test_install_claude_code_checksum_mismatch(self, tool_manager: ToolManager, tmp_path: Path):
        """Installation should fail if checksum doesn't match."""
        plat = _detect_claude_platform()
        responses = [
            httpx.Response(200, text="3.0.0"),
            httpx.Response(200, json={"platforms": {plat: {"checksum": "a" * 64}}}),
            httpx.Response(200, content=b"bad binary content"),
        ]

        with (
            _mock_httpx_transport(lambda req: responses.pop(0)),
            pytest.raises(ValueError, match="Checksum mismatch"),
        ):
            await tool_manager._install_claude_code()


# ---------------------------------------------------------------------------
# Installation — Codex
# ---------------------------------------------------------------------------


class TestInstallCodex:
    @pytest.mark.asyncio
    async def test_install_codex(self, tool_manager: ToolManager, tmp_path: Path):
        """Test Codex installation with mocked HTTP responses."""
        target = _detect_codex_target()
        member_name = f"codex-{target}"

        # Create a tarball in memory with a fake binary
        tar_path = tmp_path / "codex.tar.gz"
        fake_binary = tmp_path / member_name
        fake_binary.write_text("#!/bin/sh\necho 'codex-cli 0.106.0'")
        fake_binary.chmod(0o755)

        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(str(fake_binary), arcname=member_name)

        tar_content = tar_path.read_bytes()

        with (
            patch.object(
                ToolManager,
                "_get_latest_codex_version",
                new_callable=AsyncMock,
                return_value=("0.106.0", "rust-v0.106.0"),
            ),
            _mock_httpx_transport(lambda req: httpx.Response(200, content=tar_content)),
        ):
            tool = await tool_manager._install_codex()

        assert tool.managed is True
        assert tool.current_version == "0.106.0"
        assert tool.binary_path is not None
        assert Path(tool.binary_path).exists()


# ---------------------------------------------------------------------------
# Updates
# ---------------------------------------------------------------------------


class TestUpdates:
    @pytest.mark.asyncio
    async def test_update_skips_unmanaged(self, tool_manager: ToolManager):
        """Unmanaged tools should not be updated, only checked."""
        tool_manager._tools["codex"] = ManagedTool(
            name="codex",
            binary_name="codex",
            binary_path="/usr/bin/codex",
            current_version="0.90.0",
            managed=False,
        )

        with patch.object(
            ToolManager,
            "_get_latest_codex_version",
            new_callable=AsyncMock,
            return_value=("0.106.0", "rust-v0.106.0"),
        ):
            result = await tool_manager.update_tool("codex")

        assert result.latest_version == "0.106.0"
        assert result.current_version == "0.90.0"  # unchanged
        assert result.managed is False

    @pytest.mark.asyncio
    async def test_update_skips_when_current(self, tool_manager: ToolManager):
        """No download if version is already up to date."""
        tool_manager._tools["claude_code"] = ManagedTool(
            name="claude_code",
            binary_name="claude",
            binary_path="/managed/claude",
            current_version="3.0.0",
            managed=True,
        )

        with patch.object(
            ToolManager,
            "_get_latest_claude_version",
            new_callable=AsyncMock,
            return_value="3.0.0",
        ):
            result = await tool_manager._update_claude_code(tool_manager._tools["claude_code"])

        assert result.current_version == "3.0.0"

    @pytest.mark.asyncio
    async def test_update_installs_missing(self, tool_manager: ToolManager):
        """If a tool has no binary_path, update_tool should try to install it."""
        tool_manager._tools["codex"] = ManagedTool(
            name="codex",
            binary_name="codex",
        )

        with patch.object(
            tool_manager,
            "install_tool",
            new_callable=AsyncMock,
            return_value=ManagedTool(
                name="codex",
                binary_name="codex",
                binary_path="/managed/codex",
                current_version="0.106.0",
                managed=True,
            ),
        ) as mock_install:
            result = await tool_manager.update_tool("codex")

        mock_install.assert_awaited_once_with("codex")
        assert result.binary_path == "/managed/codex"


# ---------------------------------------------------------------------------
# ensure_tools
# ---------------------------------------------------------------------------


class TestEnsureTools:
    @pytest.mark.asyncio
    async def test_ensure_tools_never_raises(self, tool_manager: ToolManager):
        """ensure_tools should always succeed, even if detection/install fails."""
        with patch.object(
            tool_manager,
            "detect_tool",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network down"),
        ):
            result = await tool_manager.ensure_tools()

        assert "claude_code" in result
        assert "codex" in result
        assert result["claude_code"].error is not None
        assert result["codex"].error is not None

    @pytest.mark.asyncio
    async def test_ensure_tools_does_not_auto_install(self, tool_manager: ToolManager):
        """If detect returns no binary_path, ensure_tools should NOT auto-install."""
        detect_results = {
            "claude_code": ManagedTool(name="claude_code", binary_name="claude"),
            "codex": ManagedTool(name="codex", binary_name="codex"),
        }

        async def fake_detect(name):
            return detect_results[name]

        with (
            patch.object(tool_manager, "detect_tool", side_effect=fake_detect),
            patch.object(tool_manager, "install_tool", new_callable=AsyncMock) as mock_install,
        ):
            results = await tool_manager.ensure_tools()

        mock_install.assert_not_awaited()
        assert results["claude_code"].binary_path is None
        assert results["codex"].binary_path is None

    @pytest.mark.asyncio
    async def test_ensure_tools_skips_install_when_found(self, tool_manager: ToolManager):
        """If detect finds the binary, ensure_tools should not install."""
        found = ManagedTool(
            name="claude_code",
            binary_name="claude",
            binary_path="/usr/bin/claude",
            current_version="2.1.63",
            managed=False,
        )

        async def fake_detect(name):
            return found

        with (
            patch.object(tool_manager, "detect_tool", side_effect=fake_detect),
            patch.object(tool_manager, "install_tool", new_callable=AsyncMock) as mock_install,
        ):
            await tool_manager.ensure_tools()

        mock_install.assert_not_awaited()


# ---------------------------------------------------------------------------
# Version queries
# ---------------------------------------------------------------------------


class TestVersionQueries:
    @pytest.mark.asyncio
    async def test_get_installed_version_claude(self, tmp_path: Path):
        """Parse version from a real script's stdout."""
        script = tmp_path / "claude"
        script.write_text("#!/bin/sh\necho '2.1.63 (Claude Code)'")
        script.chmod(0o755)

        version = await ToolManager._get_installed_version(str(script), "claude_code")
        assert version == "2.1.63"

    @pytest.mark.asyncio
    async def test_get_installed_version_codex(self, tmp_path: Path):
        script = tmp_path / "codex"
        script.write_text("#!/bin/sh\necho 'codex-cli 0.91.0'")
        script.chmod(0o755)

        version = await ToolManager._get_installed_version(str(script), "codex")
        assert version == "0.91.0"

    @pytest.mark.asyncio
    async def test_get_installed_version_missing_binary(self):
        version = await ToolManager._get_installed_version("/nonexistent", "codex")
        assert version is None

    @pytest.mark.asyncio
    async def test_get_latest_claude_version(self):
        with _mock_httpx_transport(lambda req: httpx.Response(200, text="3.0.0\n")):
            version = await ToolManager._get_latest_claude_version()
        assert version == "3.0.0"

    @pytest.mark.asyncio
    async def test_get_latest_codex_version(self):
        with _mock_httpx_transport(lambda req: httpx.Response(200, json={"tag_name": "rust-v0.106.0"})):
            version, tag = await ToolManager._get_latest_codex_version()
        assert version == "0.106.0"
        assert tag == "rust-v0.106.0"

    @pytest.mark.asyncio
    async def test_get_latest_claude_version_network_error(self):
        with _mock_httpx_transport(lambda req: httpx.Response(500)):
            version = await ToolManager._get_latest_claude_version()
        assert version is None

    @pytest.mark.asyncio
    async def test_get_latest_opencode_version(self):
        with _mock_httpx_transport(lambda req: httpx.Response(200, json={"tag_name": "v1.3.7"})):
            version = await ToolManager._get_latest_opencode_version()
        assert version == "1.3.7"

    @pytest.mark.asyncio
    async def test_get_latest_opencode_version_follows_redirect(self):
        """GitHub sometimes issues a 301 redirect; the client must follow it."""
        call_count = 0

        def _redirecting_handler(req):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    301,
                    headers={"location": "https://api.github.com/repos/sst/opencode/releases/123456789"},
                )
            return httpx.Response(200, json={"tag_name": "v2.0.0"})

        with _mock_httpx_transport(_redirecting_handler):
            version = await ToolManager._get_latest_opencode_version()
        assert version == "2.0.0"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_get_latest_opencode_version_network_error(self):
        with _mock_httpx_transport(lambda req: httpx.Response(500)):
            version = await ToolManager._get_latest_opencode_version()
        assert version is None


# ---------------------------------------------------------------------------
# Platform detection — OpenCode
# ---------------------------------------------------------------------------


class TestOpenCodePlatformDetection:
    @patch("src.services.tool_manager.sys.platform", "linux")
    @patch("src.services.tool_manager.platform.machine", return_value="x86_64")
    @patch("src.services.tool_manager._is_musl", return_value=False)
    def test_linux_x64_glibc(self, _musl, _machine):
        base, ext = _detect_opencode_asset()
        assert base == "opencode-linux-x64"
        assert ext == ".tar.gz"

    @patch("src.services.tool_manager.sys.platform", "linux")
    @patch("src.services.tool_manager.platform.machine", return_value="x86_64")
    @patch("src.services.tool_manager._is_musl", return_value=True)
    def test_linux_x64_musl(self, _musl, _machine):
        base, ext = _detect_opencode_asset()
        assert base == "opencode-linux-x64-musl"
        assert ext == ".tar.gz"

    @patch("src.services.tool_manager.sys.platform", "linux")
    @patch("src.services.tool_manager.platform.machine", return_value="aarch64")
    @patch("src.services.tool_manager._is_musl", return_value=False)
    def test_linux_arm64(self, _musl, _machine):
        base, ext = _detect_opencode_asset()
        assert base == "opencode-linux-arm64"
        assert ext == ".tar.gz"

    @patch("src.services.tool_manager.sys.platform", "darwin")
    @patch("src.services.tool_manager.platform.machine", return_value="arm64")
    def test_darwin_arm64(self, _machine):
        base, ext = _detect_opencode_asset()
        assert base == "opencode-darwin-arm64"
        assert ext == ".zip"

    @patch("src.services.tool_manager.sys.platform", "darwin")
    @patch("src.services.tool_manager.platform.machine", return_value="x86_64")
    def test_darwin_x64(self, _machine):
        base, ext = _detect_opencode_asset()
        assert base == "opencode-darwin-x64"
        assert ext == ".zip"

    @patch("src.services.tool_manager.sys.platform", "linux")
    @patch("src.services.tool_manager.platform.machine", return_value="ppc64le")
    def test_unsupported_arch(self, _machine):
        with pytest.raises(RuntimeError, match="Unsupported"):
            _detect_opencode_asset()


# ---------------------------------------------------------------------------
# Installation — OpenCode
# ---------------------------------------------------------------------------


class TestInstallOpenCode:
    @pytest.mark.asyncio
    @patch("src.services.tool_manager.sys.platform", "linux")
    @patch("src.services.tool_manager.platform.machine", return_value="x86_64")
    @patch("src.services.tool_manager._is_musl", return_value=False)
    async def test_install_opencode(self, _musl, _machine, tool_manager: ToolManager, tmp_path: Path):
        """Test OpenCode installation with mocked HTTP responses."""
        member_name = "opencode"

        # Build a tarball in memory with a fake binary
        tar_path = tmp_path / "opencode.tar.gz"
        fake_bin = tmp_path / member_name
        fake_bin.write_text("#!/bin/sh\necho '1.3.7'")
        fake_bin.chmod(0o755)
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(str(fake_bin), arcname=member_name)
        tar_content = tar_path.read_bytes()

        with (
            patch.object(
                ToolManager,
                "_get_latest_opencode_version",
                new_callable=AsyncMock,
                return_value="1.3.7",
            ),
            patch(
                "src.services.tool_manager._verify_binary",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            _mock_httpx_transport(lambda req: httpx.Response(200, content=tar_content)),
        ):
            tool = await tool_manager._install_opencode()

        assert tool.managed is True
        assert tool.current_version == "1.3.7"
        assert tool.binary_path is not None
        assert Path(tool.binary_path).exists()

    @pytest.mark.asyncio
    @patch("src.services.tool_manager.sys.platform", "linux")
    @patch("src.services.tool_manager.platform.machine", return_value="x86_64")
    @patch("src.services.tool_manager._is_musl", return_value=False)
    async def test_install_opencode_streaming_updates_tools_dict(
        self, _musl, _machine, tool_manager: ToolManager, tmp_path: Path
    ):
        """install_tool_streaming for opencode must update _tools with managed=True.

        The frontend relies on this so that a subsequent GET /tools/{name}/settings
        returns managed-only fields (provider, model, API keys) immediately after
        installation — without requiring a server restart.
        """
        member_name = "opencode"

        tar_path = tmp_path / "opencode.tar.gz"
        fake_bin = tmp_path / member_name
        fake_bin.write_text("#!/bin/sh\necho '1.3.7'")
        fake_bin.chmod(0o755)
        with tarfile.open(tar_path, "w:gz") as tf:
            tf.add(str(fake_bin), arcname=member_name)
        tar_content = tar_path.read_bytes()

        events: list[dict] = []
        with (
            patch.object(
                ToolManager,
                "_get_latest_opencode_version",
                new_callable=AsyncMock,
                return_value="1.3.7",
            ),
            patch(
                "src.services.tool_manager._verify_binary",
                new_callable=AsyncMock,
                return_value=(True, ""),
            ),
            _mock_httpx_transport(lambda req: httpx.Response(200, content=tar_content)),
        ):
            async for event in tool_manager.install_tool_streaming("opencode"):
                events.append(event)

        # Streaming must end with a "done" event
        assert events[-1]["step"] == "done"

        # _tools must be updated in-process so settings requests see managed=True
        tool = tool_manager._tools.get("opencode")
        assert tool is not None
        assert tool.managed is True
        assert tool.current_version == "1.3.7"
