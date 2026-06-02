"""ToolManager: detects, installs, and updates managed CLI tool binaries."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from src.paths import get_managed_cc_plugins_dir, get_managed_tools_dir
from src.services.tools.binary_install import (
    _atomic_install_binary,
    _fetch_codex_checksums,
    _find_codex_binary,
    _find_opencode_binary,
    _is_executable,
    _verify_binary,
    _verify_codex_asset_checksum,
)
from src.services.tools.constants import (
    _CHECK_TIMEOUT,
    _DOWNLOAD_TIMEOUT,
    CLAUDE_GCS_BUCKET,
    CODEX_GITHUB_RELEASES_API,
    CODEX_RELEASE_BASE,
    OPENCODE_GITHUB_RELEASES_API,
    OPENCODE_RELEASE_BASE,
)
from src.services.tools.models import ManagedTool
from src.services.tools.platform_detect import (
    _detect_claude_platform,
    _detect_codex_target,
    _detect_opencode_asset,
    _parse_version,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from src.config import Settings

logger = logging.getLogger(__name__)


class ToolManager:
    """Detects, installs, and updates Claude Code and Codex CLI binaries.

    Only manages tools whose Settings value is the bare default (``"claude"``
    / ``"codex"``).  If the user has set a custom path, the tool is treated
    as externally managed and left alone.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._base_dir = self._default_base_dir()
        self._tools: dict[str, ManagedTool] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _default_base_dir() -> Path:
        """Return the platform-appropriate base directory for managed tools."""
        return get_managed_tools_dir()

    @property
    def tool_names(self) -> set[str]:
        """Return the set of known tool names."""
        return set(self._tools.keys()) | {"claude_code", "codex", "opencode"}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def ensure_tools(self) -> dict[str, ManagedTool]:
        """Detect tools (does not auto-install).  Never raises — logs errors and continues.

        Tools that are not found are reported with ``binary_path=None``.
        Use ``install_tool()`` or ``install_tool_streaming()`` to install
        on-demand when the user requests it via the UI.
        """
        binary_names = {"claude_code": "claude", "codex": "codex", "opencode": "opencode"}
        results: dict[str, ManagedTool] = {}
        for name in ("claude_code", "codex", "opencode"):
            try:
                # Sweep any ``<binary>.<pid>.old`` files left by a previous
                # in-place update on Windows.  Cheap no-op on POSIX.
                self._cleanup_parked_binaries(name)
                tool = await self.detect_tool(name)
                results[name] = tool
                if tool.binary_path:
                    logger.info(
                        "Tool '%s' ready: %s (v%s, managed=%s)",
                        name,
                        tool.binary_path,
                        tool.current_version,
                        tool.managed,
                    )
                else:
                    logger.info("Tool '%s' not found — install via the UI when needed", name)
            except Exception as exc:
                logger.exception("Failed to set up tool '%s'", name)
                results[name] = ManagedTool(
                    name=name,
                    binary_name=binary_names.get(name, name),
                    error=str(exc),
                )
        self._tools = results
        return results

    async def check_updates(self) -> dict[str, ManagedTool]:
        """Check for available updates for all tools (does not install)."""
        for name, tool in self._tools.items():
            try:
                if name == "claude_code":
                    tool.latest_version = await self._get_latest_claude_version()
                elif name == "codex":
                    version, _ = await self._get_latest_codex_version()
                    tool.latest_version = version
                elif name == "opencode":
                    tool.latest_version = await self._get_latest_opencode_version()
            except Exception:
                logger.warning("Failed to check updates for '%s'", name, exc_info=True)
        return dict(self._tools)

    async def update_all(self) -> dict[str, ManagedTool]:
        """Check for and install updates for all managed tools."""
        results: dict[str, ManagedTool] = {}
        for name in list(self._tools):
            try:
                results[name] = await self.update_tool(name)
            except Exception:
                logger.exception("Failed to update tool '%s'", name)
                results[name] = self._tools[name]
        return results

    async def uninstall_tool(self, name: str) -> ManagedTool:
        """Remove the managed binary (and .version file) but preserve settings.

        For Codex, also removes the ``codex-proxy`` sibling binary.
        Returns the re-detected tool (which may fall back to an external PATH binary).
        """
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")
        if not tool.managed_path:
            raise ValueError(f"No managed installation found for {name}")

        mp = Path(tool.managed_path)
        if mp.is_file():
            mp.unlink()
        vf = mp.with_suffix(".version")
        if vf.is_file():
            vf.unlink()

        # Clean up legacy codex-proxy sibling if present from older installs
        if name == "codex":
            exe = ".exe" if sys.platform == "win32" else ""
            for legacy_name in (f"codex-proxy{exe}",):
                legacy = mp.parent / legacy_name
                if legacy.is_file():
                    legacy.unlink()

        updated = await self.detect_tool(name)
        self._tools[name] = updated
        return updated

    async def run_update_loop(self) -> None:
        """Periodically check for updates.  Runs as a background asyncio task."""
        interval = self._settings.TOOL_UPDATE_INTERVAL_HOURS * 3600
        while True:
            await asyncio.sleep(interval)
            if not self._settings.TOOL_AUTO_UPDATE:
                continue
            try:
                for name in list(self._tools):
                    tool = self._tools[name]
                    if tool.managed and tool.binary_path:
                        updated = await self.update_tool(name)
                        if updated.current_version != tool.current_version:
                            logger.info(
                                "Tool '%s' updated: %s -> %s",
                                name,
                                tool.current_version,
                                updated.current_version,
                            )
            except Exception:
                logger.exception("Error in tool update loop")

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    async def detect_tool(self, name: str) -> ManagedTool:
        """Detect whether a tool is installed and resolve its binary path.

        Only the RCFlow-managed install is honoured.  External binaries on
        ``PATH`` are intentionally **not** picked up: every coding-agent
        invocation must run under RCFlow's managed copy so the user's per-tool
        config (``CLAUDE_CONFIG_DIR``, ``CODEX_HOME``, …) is the authoritative
        source.  When no managed binary is on disk, the tool is reported as
        not installed and the UI prompts the user to install it.
        """
        binary_names = {"claude_code": "claude", "codex": "codex", "opencode": "opencode"}
        binary_name = binary_names.get(name, name)

        mp = self._managed_binary_path(name)
        managed_str = str(mp) if mp.is_file() and _is_executable(mp) else None

        if managed_str:
            version = await self._get_installed_version(managed_str, name)
            # Fall back to persisted .version file or in-memory cache when
            # ``--version`` fails (e.g. GLIBC mismatch).
            if version is None:
                version = self._read_version_file(name)
            if version is None:
                existing = self._tools.get(name)
                if existing and existing.binary_path == managed_str:
                    version = existing.current_version
            return ManagedTool(
                name=name,
                binary_name=binary_name,
                binary_path=managed_str,
                current_version=version,
                managed=True,
                managed_path=managed_str,
                external_path=None,
            )

        # Not installed (managed binary not on disk).
        return ManagedTool(name=name, binary_name=binary_name)

    # ------------------------------------------------------------------
    # Installation
    # ------------------------------------------------------------------

    async def install_tool(self, name: str) -> ManagedTool:
        """Install a tool to the RCFlow managed directory."""
        async with self._lock:
            if name == "claude_code":
                tool = await self._install_claude_code()
            elif name == "codex":
                tool = await self._install_codex()
            elif name == "opencode":
                tool = await self._install_opencode()
            else:
                raise ValueError(f"Unknown tool: {name}")
            self._tools[name] = tool
            return tool

    async def _install_claude_code(self) -> ManagedTool:
        """Download and install Claude Code native binary from Anthropic GCS."""
        install_dir = self._base_dir / "claude-code"
        install_dir.mkdir(parents=True, exist_ok=True)
        # Ensure the RCFlow-managed plugins directory exists on every install so
        # the slash-commands endpoint can discover it on any machine without
        # needing a prior endpoint call to create it.
        get_managed_cc_plugins_dir()

        exe = ".exe" if sys.platform == "win32" else ""
        binary_name = f"claude{exe}"
        binary_path = install_dir / binary_name

        claude_platform = _detect_claude_platform()

        async with httpx.AsyncClient(follow_redirects=True) as client:
            # 1. Get latest version
            resp = await client.get(f"{CLAUDE_GCS_BUCKET}/latest", timeout=_CHECK_TIMEOUT)
            resp.raise_for_status()
            version = resp.text.strip()

            # 2. Get manifest with checksums
            resp = await client.get(
                f"{CLAUDE_GCS_BUCKET}/{version}/manifest.json",
                timeout=_CHECK_TIMEOUT,
            )
            resp.raise_for_status()
            manifest = resp.json()
            expected_checksum = manifest["platforms"][claude_platform]["checksum"]

            # 3. Download binary to temp file
            tmp_path = install_dir / f".claude-{version}.tmp"
            try:
                resp = await client.get(
                    f"{CLAUDE_GCS_BUCKET}/{version}/{claude_platform}/{binary_name}",
                    timeout=_DOWNLOAD_TIMEOUT,
                )
                resp.raise_for_status()
                tmp_path.write_bytes(resp.content)

                # 4. Verify SHA256
                actual = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
                if actual != expected_checksum:
                    raise ValueError(
                        f"Checksum mismatch for Claude Code {version}: expected {expected_checksum}, got {actual}"
                    )

                # 5. Set executable (POSIX only) and atomic replace
                if sys.platform != "win32":
                    tmp_path.chmod(0o755)
                _atomic_install_binary(tmp_path, binary_path)
            finally:
                tmp_path.unlink(missing_ok=True)

        logger.info("Installed Claude Code %s to %s", version, binary_path)
        self._write_version_file("claude_code", version)
        which_str = shutil.which("claude")
        if which_str and Path(which_str).resolve() == binary_path.resolve():
            which_str = None
        return ManagedTool(
            name="claude_code",
            binary_name="claude",
            binary_path=str(binary_path),
            current_version=version,
            latest_version=version,
            managed=True,
            managed_path=str(binary_path),
            external_path=which_str,
        )

    async def _install_codex(self) -> ManagedTool:
        """Download and install Codex native binary from GitHub Releases."""
        install_dir = self._base_dir / "codex"
        install_dir.mkdir(parents=True, exist_ok=True)

        exe = ".exe" if sys.platform == "win32" else ""
        binary_path = install_dir / f"codex{exe}"

        version, tag = await self._get_latest_codex_version()
        if not version or not tag:
            raise RuntimeError("Could not determine latest Codex version")

        target = _detect_codex_target()
        await self._download_codex_binary(install_dir, binary_path, tag, version, target)

        # Verify the binary can actually run on this system
        if sys.platform != "win32":
            ok, err = await _verify_binary(str(binary_path))
            if not ok and "GLIBC" in err and "musl" not in target:
                musl_target = target.replace("-gnu", "-musl")
                logger.warning(
                    "Codex gnu binary requires newer glibc (%s), retrying with musl variant",
                    err.splitlines()[0] if err else "unknown",
                )
                await self._download_codex_binary(install_dir, binary_path, tag, version, musl_target)

        logger.info("Installed Codex %s to %s", version, binary_path)
        self._write_version_file("codex", version)
        which_str = shutil.which("codex")
        if which_str and Path(which_str).resolve() == binary_path.resolve():
            which_str = None
        return ManagedTool(
            name="codex",
            binary_name="codex",
            binary_path=str(binary_path),
            current_version=version,
            latest_version=version,
            managed=True,
            managed_path=str(binary_path),
            external_path=which_str,
        )

    @staticmethod
    async def _download_codex_binary(install_dir: Path, binary_path: Path, tag: str, version: str, target: str) -> None:
        """Download and place the codex binary for a specific target triple."""
        if sys.platform == "win32":
            asset_name = f"codex-{target}.exe"
            download_url = f"{CODEX_RELEASE_BASE}/{tag}/{asset_name}"
            async with httpx.AsyncClient(follow_redirects=True) as client:
                checksums = await _fetch_codex_checksums(client, tag)
                resp = await client.get(download_url, timeout=_DOWNLOAD_TIMEOUT)
                resp.raise_for_status()
                _verify_codex_asset_checksum(resp.content, asset_name, checksums)
                tmp_path = install_dir / f".codex-{version}.tmp"
                try:
                    tmp_path.write_bytes(resp.content)
                    _atomic_install_binary(tmp_path, binary_path)
                finally:
                    tmp_path.unlink(missing_ok=True)
        else:
            asset_name = f"codex-{target}.tar.gz"
            download_url = f"{CODEX_RELEASE_BASE}/{tag}/{asset_name}"

            async with httpx.AsyncClient(follow_redirects=True) as client:
                checksums = await _fetch_codex_checksums(client, tag)
                resp = await client.get(download_url, timeout=_DOWNLOAD_TIMEOUT)
                resp.raise_for_status()
                _verify_codex_asset_checksum(resp.content, asset_name, checksums)

                with tempfile.TemporaryDirectory(dir=str(install_dir)) as tmp_dir:
                    tar_path = Path(tmp_dir) / asset_name
                    tar_path.write_bytes(resp.content)

                    with tarfile.open(tar_path, "r:gz") as tf:
                        members = tf.getnames()
                        if not members:
                            raise RuntimeError("Codex tarball is empty")
                        tf.extractall(tmp_dir, filter="data")
                        extracted = _find_codex_binary(Path(tmp_dir), members)
                        if not extracted:
                            raise RuntimeError(f"Could not find codex binary in tarball: {members}")
                        extracted.chmod(0o755)
                        shutil.move(str(extracted), str(binary_path))

    async def _install_opencode(self) -> ManagedTool:
        """Download and install OpenCode native binary from GitHub Releases."""
        install_dir = self._base_dir / "opencode"
        install_dir.mkdir(parents=True, exist_ok=True)

        exe = ".exe" if sys.platform == "win32" else ""
        binary_path = install_dir / f"opencode{exe}"

        version = await self._get_latest_opencode_version()
        if not version:
            raise RuntimeError("Could not determine latest OpenCode version")

        asset_base, ext = _detect_opencode_asset()
        asset_name = f"{asset_base}{ext}"
        tag = f"v{version}"
        download_url = f"{OPENCODE_RELEASE_BASE}/{tag}/{asset_name}"

        await self._download_opencode_binary(install_dir, binary_path, download_url, asset_name, version)

        # Verify the binary can actually run; fall back to musl on glibc-too-old Linux
        if sys.platform not in ("win32", "darwin"):
            ok, err = await _verify_binary(str(binary_path))
            if not ok and "GLIBC" in err and "musl" not in asset_base:
                musl_asset_base = asset_base + "-musl"
                musl_asset = f"{musl_asset_base}{ext}"
                musl_url = f"{OPENCODE_RELEASE_BASE}/{tag}/{musl_asset}"
                logger.warning(
                    "OpenCode gnu binary requires newer glibc (%s), retrying with musl variant",
                    err.splitlines()[0] if err else "unknown",
                )
                await self._download_opencode_binary(install_dir, binary_path, musl_url, musl_asset, version)

        logger.info("Installed OpenCode %s to %s", version, binary_path)
        self._write_version_file("opencode", version)
        which_str = shutil.which("opencode")
        if which_str and Path(which_str).resolve() == binary_path.resolve():
            which_str = None
        return ManagedTool(
            name="opencode",
            binary_name="opencode",
            binary_path=str(binary_path),
            current_version=version,
            latest_version=version,
            managed=True,
            managed_path=str(binary_path),
            external_path=which_str,
        )

    @staticmethod
    async def _download_opencode_binary(
        install_dir: Path, binary_path: Path, download_url: str, asset_name: str, version: str
    ) -> None:
        """Download and extract the opencode binary from a release archive."""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(download_url, timeout=_DOWNLOAD_TIMEOUT)
            resp.raise_for_status()

            with tempfile.TemporaryDirectory(dir=str(install_dir)) as tmp_dir:
                archive_path = Path(tmp_dir) / asset_name
                archive_path.write_bytes(resp.content)

                if asset_name.endswith(".tar.gz"):
                    with tarfile.open(archive_path, "r:gz") as tf:
                        members = tf.getnames()
                        if not members:
                            raise RuntimeError("OpenCode tarball is empty")
                        tf.extractall(tmp_dir, filter="data")
                        extracted = _find_opencode_binary(Path(tmp_dir), members)
                        if not extracted:
                            raise RuntimeError(f"Could not find opencode binary in tarball: {members}")
                        extracted.chmod(0o755)
                        shutil.move(str(extracted), str(binary_path))
                else:
                    # .zip (macOS and Windows)
                    with zipfile.ZipFile(archive_path) as zf:
                        names = zf.namelist()
                        zf.extractall(tmp_dir)  # noqa: S202
                        extracted = _find_opencode_binary(Path(tmp_dir), names)
                        if not extracted:
                            raise RuntimeError(f"Could not find opencode binary in zip: {names}")
                        if sys.platform != "win32":
                            extracted.chmod(0o755)
                        shutil.move(str(extracted), str(binary_path))

    # ------------------------------------------------------------------
    # Streaming install (with progress events)
    # ------------------------------------------------------------------

    async def install_tool_streaming(self, name: str) -> AsyncGenerator[dict[str, Any], None]:
        """Install a tool, yielding NDJSON progress events.

        Final event has ``step="done"`` with the tool dict.
        """
        async with self._lock:
            self._cleanup_parked_binaries(name)
            if name == "claude_code":
                async for event in self._install_claude_code_streaming():
                    yield event
            elif name == "codex":
                async for event in self._install_codex_streaming():
                    yield event
            elif name == "opencode":
                async for event in self._install_opencode_streaming():
                    yield event
            else:
                yield {"step": "error", "message": f"Unknown tool: {name}"}

    def _cleanup_parked_binaries(self, name: str) -> None:
        """Best-effort delete of ``<binary>.<pid>.old`` files left over from prior in-place
        Windows updates.  Files still memory-mapped by a running process stay on disk and
        are retried at the next call.  No-op on POSIX where ``replace`` already overwrites.
        """
        if sys.platform != "win32":
            return
        mp = self._managed_binary_path(name)
        if not mp.parent.exists():
            return
        for stale in mp.parent.glob(f"{mp.name}.*.old"):
            with contextlib.suppress(OSError, PermissionError):
                stale.unlink()

    async def _install_claude_code_streaming(self) -> AsyncGenerator[dict[str, Any], None]:
        """Download Claude Code with streaming progress."""
        install_dir = self._base_dir / "claude-code"
        install_dir.mkdir(parents=True, exist_ok=True)
        get_managed_cc_plugins_dir()  # ensure plugins dir exists on every machine

        exe = ".exe" if sys.platform == "win32" else ""
        binary_name = f"claude{exe}"
        binary_path = install_dir / binary_name

        yield {"step": "checking_version", "message": "Checking latest version..."}
        claude_platform = _detect_claude_platform()

        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await client.get(f"{CLAUDE_GCS_BUCKET}/latest", timeout=_CHECK_TIMEOUT)
            resp.raise_for_status()
            version = resp.text.strip()

            yield {"step": "checking_version", "message": f"Found version {version}"}

            resp = await client.get(
                f"{CLAUDE_GCS_BUCKET}/{version}/manifest.json",
                timeout=_CHECK_TIMEOUT,
            )
            resp.raise_for_status()
            manifest = resp.json()
            expected_checksum = manifest["platforms"][claude_platform]["checksum"]

            yield {"step": "downloading", "progress": 0.0, "message": "Starting download..."}

            # Stream the download to track progress
            tmp_path = install_dir / f".claude-{version}.tmp"
            try:
                async with client.stream(
                    "GET",
                    f"{CLAUDE_GCS_BUCKET}/{version}/{claude_platform}/{binary_name}",
                    timeout=_DOWNLOAD_TIMEOUT,
                ) as stream:
                    total = int(stream.headers.get("content-length", 0))
                    received = 0
                    chunks: list[bytes] = []
                    async for chunk in stream.aiter_bytes(65536):
                        chunks.append(chunk)
                        received += len(chunk)
                        if total > 0:
                            pct = received / total
                            mb_recv = received / 1_048_576
                            mb_total = total / 1_048_576
                            yield {
                                "step": "downloading",
                                "progress": round(pct, 3),
                                "message": f"Downloading... {mb_recv:.1f} / {mb_total:.1f} MB",
                            }

                tmp_path.write_bytes(b"".join(chunks))

                yield {"step": "verifying", "message": "Verifying checksum..."}
                actual = hashlib.sha256(tmp_path.read_bytes()).hexdigest()
                if actual != expected_checksum:
                    raise ValueError(
                        f"Checksum mismatch for Claude Code {version}: expected {expected_checksum}, got {actual}"
                    )

                yield {"step": "installing", "message": "Installing..."}
                if sys.platform != "win32":
                    tmp_path.chmod(0o755)
                _atomic_install_binary(tmp_path, binary_path)
            finally:
                tmp_path.unlink(missing_ok=True)

        self._write_version_file("claude_code", version)
        which_str = shutil.which("claude")
        if which_str and Path(which_str).resolve() == binary_path.resolve():
            which_str = None
        tool = ManagedTool(
            name="claude_code",
            binary_name="claude",
            binary_path=str(binary_path),
            current_version=version,
            latest_version=version,
            managed=True,
            managed_path=str(binary_path),
            external_path=which_str,
        )
        self._tools["claude_code"] = tool
        logger.info("Installed Claude Code %s to %s", version, binary_path)
        yield {"step": "done", "message": f"Installed v{version}"}

    async def _install_codex_streaming(self) -> AsyncGenerator[dict[str, Any], None]:
        """Download Codex with streaming progress."""
        install_dir = self._base_dir / "codex"
        install_dir.mkdir(parents=True, exist_ok=True)

        exe = ".exe" if sys.platform == "win32" else ""
        binary_path = install_dir / f"codex{exe}"

        yield {"step": "checking_version", "message": "Checking latest version..."}
        version, tag = await self._get_latest_codex_version()
        if not version or not tag:
            yield {"step": "error", "message": "Could not determine latest Codex version"}
            return

        yield {"step": "checking_version", "message": f"Found version {version}"}

        target = _detect_codex_target()

        async for event in self._stream_codex_download(install_dir, binary_path, tag, version, target):
            yield event

        # Verify the binary can actually run on this system (glibc compat)
        if sys.platform != "win32":
            ok, err = await _verify_binary(str(binary_path))
            if not ok and "GLIBC" in err and "musl" not in target:
                musl_target = target.replace("-gnu", "-musl")
                logger.warning("Codex gnu binary requires newer glibc, retrying with musl")
                yield {"step": "installing", "message": "Incompatible glibc, downloading musl variant..."}
                async for event in self._stream_codex_download(install_dir, binary_path, tag, version, musl_target):
                    yield event

        self._write_version_file("codex", version)
        which_str = shutil.which("codex")
        if which_str and Path(which_str).resolve() == binary_path.resolve():
            which_str = None
        tool = ManagedTool(
            name="codex",
            binary_name="codex",
            binary_path=str(binary_path),
            current_version=version,
            latest_version=version,
            managed=True,
            managed_path=str(binary_path),
            external_path=which_str,
        )
        self._tools["codex"] = tool
        logger.info("Installed Codex %s to %s", version, binary_path)
        yield {"step": "done", "message": f"Installed v{version}"}

    @staticmethod
    async def _stream_codex_download(
        install_dir: Path, binary_path: Path, tag: str, version: str, target: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Download and install codex for a target triple, yielding progress events."""
        if sys.platform == "win32":
            asset_name = f"codex-{target}.exe"
            download_url = f"{CODEX_RELEASE_BASE}/{tag}/{asset_name}"

            yield {"step": "downloading", "progress": 0.0, "message": "Starting download..."}

            async with httpx.AsyncClient(follow_redirects=True) as client:
                checksums = await _fetch_codex_checksums(client, tag)
                total = 0
                received = 0
                chunks: list[bytes] = []
                async with client.stream("GET", download_url, timeout=_DOWNLOAD_TIMEOUT) as stream:
                    total = int(stream.headers.get("content-length", 0))
                    async for chunk in stream.aiter_bytes(65536):
                        chunks.append(chunk)
                        received += len(chunk)
                        if total > 0:
                            pct = received / total
                            mb_recv = received / 1_048_576
                            mb_total = total / 1_048_576
                            yield {
                                "step": "downloading",
                                "progress": round(pct, 3),
                                "message": f"Downloading... {mb_recv:.1f} / {mb_total:.1f} MB",
                            }

            content = b"".join(chunks)
            yield {"step": "verifying", "message": "Verifying checksum..."}
            _verify_codex_asset_checksum(content, asset_name, checksums)

            yield {"step": "installing", "message": "Installing..."}
            tmp_path = install_dir / f".codex-{version}.tmp"
            try:
                tmp_path.write_bytes(content)
                _atomic_install_binary(tmp_path, binary_path)
            finally:
                tmp_path.unlink(missing_ok=True)
        else:
            asset_name = f"codex-{target}.tar.gz"
            download_url = f"{CODEX_RELEASE_BASE}/{tag}/{asset_name}"

            yield {"step": "downloading", "progress": 0.0, "message": "Starting download..."}

            async with httpx.AsyncClient(follow_redirects=True) as client:
                checksums = await _fetch_codex_checksums(client, tag)
                total = 0
                received = 0
                chunks: list[bytes] = []
                async with client.stream("GET", download_url, timeout=_DOWNLOAD_TIMEOUT) as stream:
                    total = int(stream.headers.get("content-length", 0))
                    async for chunk in stream.aiter_bytes(65536):
                        chunks.append(chunk)
                        received += len(chunk)
                        if total > 0:
                            pct = received / total
                            mb_recv = received / 1_048_576
                            mb_total = total / 1_048_576
                            yield {
                                "step": "downloading",
                                "progress": round(pct, 3),
                                "message": f"Downloading... {mb_recv:.1f} / {mb_total:.1f} MB",
                            }

            content = b"".join(chunks)
            yield {"step": "verifying", "message": "Verifying checksum..."}
            _verify_codex_asset_checksum(content, asset_name, checksums)

            yield {"step": "installing", "message": "Installing..."}

            with tempfile.TemporaryDirectory(dir=str(install_dir)) as tmp_dir:
                tar_path = Path(tmp_dir) / asset_name
                tar_path.write_bytes(content)

                with tarfile.open(tar_path, "r:gz") as tf:
                    members = tf.getnames()
                    if not members:
                        raise RuntimeError("Codex tarball is empty")
                    tf.extractall(tmp_dir, filter="data")
                    extracted = _find_codex_binary(Path(tmp_dir), members)
                    if not extracted:
                        raise RuntimeError(f"Could not find codex binary in tarball: {members}")
                    extracted.chmod(0o755)
                    shutil.move(str(extracted), str(binary_path))

    async def _install_opencode_streaming(self) -> AsyncGenerator[dict[str, Any], None]:
        """Download and install OpenCode from GitHub Releases, yielding progress events."""
        install_dir = self._base_dir / "opencode"
        install_dir.mkdir(parents=True, exist_ok=True)

        yield {"step": "checking_version", "message": "Checking latest OpenCode version..."}

        version = await self._get_latest_opencode_version()
        if not version:
            yield {"step": "error", "message": "Could not determine latest OpenCode version"}
            return

        try:
            asset_base, ext = _detect_opencode_asset()
        except RuntimeError as exc:
            yield {"step": "error", "message": str(exc)}
            return

        asset_name = f"{asset_base}{ext}"
        tag = f"v{version}"
        download_url = f"{OPENCODE_RELEASE_BASE}/{tag}/{asset_name}"

        exe = ".exe" if sys.platform == "win32" else ""
        binary_path = install_dir / f"opencode{exe}"

        yield {"step": "downloading", "progress": 0.0, "message": f"Downloading {asset_name}..."}

        async with (
            httpx.AsyncClient(follow_redirects=True) as client,
            client.stream("GET", download_url, timeout=_DOWNLOAD_TIMEOUT) as stream,
        ):
            total = int(stream.headers.get("content-length", 0))
            received = 0
            chunks: list[bytes] = []
            async for chunk in stream.aiter_bytes(65536):
                chunks.append(chunk)
                received += len(chunk)
                if total > 0:
                    pct = received / total
                    mb_recv = received / 1_048_576
                    mb_total = total / 1_048_576
                    yield {
                        "step": "downloading",
                        "progress": round(pct, 3),
                        "message": f"Downloading... {mb_recv:.1f} / {mb_total:.1f} MB",
                    }

        yield {"step": "installing", "message": "Extracting binary..."}

        with tempfile.TemporaryDirectory(dir=str(install_dir)) as tmp_dir:
            archive_path = Path(tmp_dir) / asset_name
            archive_path.write_bytes(b"".join(chunks))

            if asset_name.endswith(".tar.gz"):
                with tarfile.open(archive_path, "r:gz") as tf:
                    members = tf.getnames()
                    if not members:
                        yield {"step": "error", "message": "OpenCode tarball is empty"}
                        return
                    tf.extractall(tmp_dir, filter="data")
                    extracted = _find_opencode_binary(Path(tmp_dir), members)
                    if not extracted:
                        yield {"step": "error", "message": f"Could not find opencode binary in tarball: {members}"}
                        return
                    extracted.chmod(0o755)
                    shutil.move(str(extracted), str(binary_path))
            else:
                with zipfile.ZipFile(archive_path) as zf:
                    names = zf.namelist()
                    zf.extractall(tmp_dir)  # noqa: S202
                    extracted = _find_opencode_binary(Path(tmp_dir), names)
                    if not extracted:
                        yield {"step": "error", "message": f"Could not find opencode binary in zip: {names}"}
                        return
                    if sys.platform != "win32":
                        extracted.chmod(0o755)
                    shutil.move(str(extracted), str(binary_path))

        # Verify + musl fallback on old-glibc Linux
        if sys.platform not in ("win32", "darwin"):
            ok, err = await _verify_binary(str(binary_path))
            if not ok and "GLIBC" in err and "musl" not in asset_base:
                yield {"step": "installing", "message": "glibc too old; switching to musl variant..."}
                musl_asset_base = asset_base + "-musl"
                musl_asset = f"{musl_asset_base}{ext}"
                musl_url = f"{OPENCODE_RELEASE_BASE}/{tag}/{musl_asset}"
                await self._download_opencode_binary(install_dir, binary_path, musl_url, musl_asset, version)

        self._write_version_file("opencode", version)
        which_str = shutil.which("opencode")
        if which_str and Path(which_str).resolve() == binary_path.resolve():
            which_str = None

        tool = ManagedTool(
            name="opencode",
            binary_name="opencode",
            binary_path=str(binary_path),
            current_version=version,
            latest_version=version,
            managed=True,
            managed_path=str(binary_path),
            external_path=which_str,
        )
        self._tools["opencode"] = tool
        logger.info("Installed OpenCode %s to %s", version, binary_path)
        yield {"step": "done", "message": f"Installed v{version}"}

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------

    async def update_tool_streaming(
        self,
        name: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Update a tool, yielding NDJSON progress events.

        If already up to date, yields a single ``done`` event.
        Falls through to ``install_tool_streaming`` if no binary exists.
        """
        tool = self._tools.get(name)
        if not tool or not tool.binary_path:
            async for event in self.install_tool_streaming(name):
                yield event
            return

        if not tool.managed:
            yield {"step": "done", "message": "External tool — skipping update"}
            return

        yield {"step": "checking_version", "message": "Checking for updates..."}
        if name == "claude_code":
            latest = await self._get_latest_claude_version()
        elif name == "codex":
            latest, _ = await self._get_latest_codex_version()
        elif name == "opencode":
            latest = await self._get_latest_opencode_version()
        else:
            yield {"step": "done", "message": f"Update check not supported for {name}"}
            return

        if not latest:
            yield {"step": "done", "message": "Could not check latest version"}
            return

        tool.latest_version = latest
        if tool.current_version == latest:
            yield {"step": "done", "message": f"Already up to date (v{latest})"}
            return

        yield {"step": "checking_version", "message": f"Updating {tool.current_version} → {latest}..."}
        # Delegate to streaming install (acquires lock, handles download + progress)
        async for event in self.install_tool_streaming(name):
            yield event

    async def update_tool(self, name: str) -> ManagedTool:
        """Update a single tool if a newer version is available."""
        tool = self._tools.get(name)
        if not tool or not tool.binary_path:
            return await self.install_tool(name)

        if not tool.managed:
            # Not our responsibility — just check and log
            if name == "claude_code":
                latest = await self._get_latest_claude_version()
            elif name == "codex":
                latest, _ = await self._get_latest_codex_version()
            elif name == "opencode":
                latest = await self._get_latest_opencode_version()
            else:
                return tool
            if latest and tool.current_version and latest != tool.current_version:
                logger.info(
                    "%s update available: %s -> %s (not RCFlow-managed, skipping)",
                    name,
                    tool.current_version,
                    latest,
                )
                tool.latest_version = latest
            return tool

        if name == "claude_code":
            return await self._update_claude_code(tool)
        if name == "codex":
            return await self._update_codex(tool)
        if name == "opencode":
            return await self._update_opencode(tool)
        return tool

    async def _update_claude_code(self, tool: ManagedTool) -> ManagedTool:
        """Re-download Claude Code if a newer version is available."""
        latest = await self._get_latest_claude_version()
        if not latest:
            return tool

        tool.latest_version = latest
        if tool.current_version == latest:
            logger.debug("Claude Code is up to date (%s)", latest)
            return tool

        logger.info("Updating Claude Code: %s -> %s", tool.current_version, latest)
        async with self._lock:
            updated = await self._install_claude_code()
        self._tools["claude_code"] = updated
        return updated

    async def _update_codex(self, tool: ManagedTool) -> ManagedTool:
        """Re-download Codex if a newer version is available."""
        latest, _ = await self._get_latest_codex_version()
        if not latest:
            return tool

        tool.latest_version = latest
        if tool.current_version == latest:
            logger.debug("Codex is up to date (%s)", latest)
            return tool

        logger.info("Updating Codex: %s -> %s", tool.current_version, latest)
        async with self._lock:
            updated = await self._install_codex()
        self._tools["codex"] = updated
        return updated

    async def _update_opencode(self, tool: ManagedTool) -> ManagedTool:
        """Re-download OpenCode from GitHub Releases if a newer version is available."""
        latest = await self._get_latest_opencode_version()
        if not latest:
            return tool

        tool.latest_version = latest
        if tool.current_version == latest:
            logger.debug("OpenCode is up to date (%s)", latest)
            return tool

        logger.info("Updating OpenCode: %s -> %s", tool.current_version, latest)
        async with self._lock:
            updated = await self._install_opencode()
        self._tools["opencode"] = updated
        return updated

    # ------------------------------------------------------------------
    # Version queries
    # ------------------------------------------------------------------

    @staticmethod
    async def _get_installed_version(binary_path: str, name: str) -> str | None:
        """Run ``<binary> --version`` and parse the output."""
        try:
            proc = await asyncio.create_subprocess_exec(
                binary_path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            return _parse_version(name, stdout.decode().strip())
        except Exception:
            logger.debug("Could not get version for %s at %s", name, binary_path, exc_info=True)
            return None

    @staticmethod
    async def _get_latest_claude_version() -> str | None:
        """Fetch latest Claude Code version string from GCS."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{CLAUDE_GCS_BUCKET}/latest", timeout=_CHECK_TIMEOUT)
                resp.raise_for_status()
                return resp.text.strip()
        except Exception:
            logger.warning("Failed to check latest Claude Code version", exc_info=True)
            return None

    @staticmethod
    async def _get_latest_codex_version() -> tuple[str | None, str | None]:
        """Fetch latest Codex version from GitHub Releases API.

        Returns ``(version, tag_name)`` — e.g. ``("0.106.0", "rust-v0.106.0")``.
        """
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(CODEX_GITHUB_RELEASES_API, timeout=_CHECK_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                tag: str = data["tag_name"]
                version = tag.removeprefix("rust-v")
                return version, tag
        except Exception:
            logger.warning("Failed to check latest Codex version", exc_info=True)
            return None, None

    @staticmethod
    async def _get_latest_opencode_version() -> str | None:
        """Fetch latest OpenCode version from GitHub Releases API."""
        try:
            async with httpx.AsyncClient(follow_redirects=True) as client:
                resp = await client.get(OPENCODE_GITHUB_RELEASES_API, timeout=_CHECK_TIMEOUT)
                resp.raise_for_status()
                data = resp.json()
                tag: str = data["tag_name"]
                return tag.lstrip("v")
        except Exception:
            logger.warning("Failed to check latest OpenCode version", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def get_binary_path(self, name: str) -> str | None:
        """Return the resolved binary path for a tool, or None if not available."""
        tool = self._tools.get(name)
        return tool.binary_path if tool else None

    def _managed_binary_path(self, name: str) -> Path:
        """Return the expected path for a managed binary."""
        exe = ".exe" if sys.platform == "win32" else ""
        if name == "claude_code":
            return self._base_dir / "claude-code" / f"claude{exe}"
        if name == "codex":
            return self._base_dir / "codex" / f"codex{exe}"
        if name == "opencode":
            return self._base_dir / "opencode" / f"opencode{exe}"
        raise ValueError(f"Unknown tool: {name}")

    def _write_version_file(self, name: str, version: str) -> None:
        """Persist the installed version next to the binary.

        This allows ``detect_tool`` to recover the version when ``--version``
        fails (e.g. GLIBC mismatch).
        """
        vf = self._managed_binary_path(name).with_suffix(".version")
        try:
            vf.write_text(version)
        except OSError:
            logger.debug("Could not write version file for %s", name, exc_info=True)

    def _read_version_file(self, name: str) -> str | None:
        """Read the persisted version file, if it exists."""
        vf = self._managed_binary_path(name).with_suffix(".version")
        try:
            text = vf.read_text().strip()
            return text or None
        except OSError:
            return None
