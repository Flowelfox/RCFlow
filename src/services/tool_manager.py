"""Manages installation and updates of external CLI tools (Claude Code, Codex, OpenCode)."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import platform
import re
import shutil
import sys
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from src.paths import get_managed_cc_plugins_dir, get_managed_tools_dir

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from src.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CLAUDE_GCS_BUCKET = (
    "https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/claude-code-releases"
)

CODEX_GITHUB_RELEASES_API = "https://api.github.com/repos/openai/codex/releases/latest"
CODEX_RELEASE_BASE = "https://github.com/openai/codex/releases/download"

OPENCODE_GITHUB_RELEASES_API = "https://api.github.com/repos/sst/opencode/releases/latest"
OPENCODE_RELEASE_BASE = "https://github.com/sst/opencode/releases/download"

# Timeout for binary downloads (large files)
_DOWNLOAD_TIMEOUT = 300
# Timeout for version/metadata checks
_CHECK_TIMEOUT = 15

# Minimum glibc version known to work with recent Codex releases.
# When the system glibc is older, we proactively use the musl (static) variant
# to avoid a failed install + retry.  The post-install verification still acts
# as a safety net in case this threshold becomes stale.
_CODEX_MIN_GLIBC = (2, 38)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ManagedTool:
    """Represents a single managed CLI tool."""

    name: str  # "claude_code" or "codex"
    binary_name: str  # "claude" or "codex"
    current_version: str | None = None
    latest_version: str | None = None
    binary_path: str | None = None  # Resolved absolute path to binary
    managed: bool = False  # Whether RCFlow manages this install
    error: str | None = None  # Last error message, if any
    managed_path: str | None = None  # Path to managed install (if exists)
    external_path: str | None = None  # Path found on PATH (if exists)


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------


def _is_musl() -> bool:
    """Detect if the system uses musl libc (Linux only)."""
    if sys.platform == "win32":
        return False
    return Path("/lib/libc.musl-x86_64.so.1").exists() or Path("/lib/libc.musl-aarch64.so.1").exists()


def _detect_claude_platform() -> str:
    """Return Claude Code platform string for GCS downloads (e.g. ``linux-x64``, ``darwin-arm64``, ``win32-x64``)."""
    machine = platform.machine()

    if sys.platform == "win32":
        if machine in ("AMD64", "x86_64"):
            return "win32-x64"
        if machine in ("ARM64", "aarch64"):
            return "win32-arm64"
        raise RuntimeError(f"Unsupported Windows architecture: {machine}")

    if machine in ("x86_64", "amd64"):
        arch = "x64"
    elif machine in ("arm64", "aarch64"):
        arch = "arm64"
    else:
        raise RuntimeError(f"Unsupported architecture: {machine}")

    if sys.platform == "darwin":
        return f"darwin-{arch}"

    # Linux
    suffix = "-musl" if _is_musl() else ""
    return f"linux-{arch}{suffix}"


def _detect_codex_target() -> str:
    """Return Codex release target triple for GitHub downloads.

    On Linux, prefers the ``gnu`` variant but falls back to ``musl``
    (statically linked) when the system glibc is too old or absent.
    """
    machine = platform.machine()

    if sys.platform == "win32":
        if machine in ("AMD64", "x86_64"):
            return "x86_64-pc-windows-msvc"
        if machine in ("ARM64", "aarch64"):
            return "aarch64-pc-windows-msvc"
        raise RuntimeError(f"Unsupported Windows architecture: {machine}")

    if machine in ("x86_64", "amd64"):
        arch = "x86_64"
    elif machine in ("arm64", "aarch64"):
        arch = "aarch64"
    else:
        raise RuntimeError(f"Unsupported architecture: {machine}")

    if sys.platform == "darwin":
        return f"{arch}-apple-darwin"

    # Linux
    libc = "musl" if _is_musl() or _glibc_too_old() else "gnu"
    return f"{arch}-unknown-linux-{libc}"


def _glibc_too_old() -> bool:
    """Return True if system glibc is older than what Codex requires."""
    try:
        _, version_str = platform.libc_ver()
        if not version_str:
            return True
        parts = tuple(int(x) for x in version_str.split("."))
        return parts < _CODEX_MIN_GLIBC
    except (ValueError, TypeError):
        return True


def _parse_version(name: str, raw: str) -> str | None:
    """Extract a semver-ish version string from ``--version`` output."""
    if name == "claude_code":
        # "2.1.63 (Claude Code)" → "2.1.63"
        match = re.match(r"([\d.]+)", raw)
        return match.group(1) if match else None
    if name == "codex":
        # "codex-cli 0.91.0" → "0.91.0"
        match = re.search(r"([\d.]+)", raw)
        return match.group(1) if match else None
    if name == "opencode":
        # "1.3.7" → "1.3.7"
        match = re.search(r"([\d]+\.[\d]+\.[\d]+)", raw)
        return match.group(1) if match else None
    return None


# ---------------------------------------------------------------------------
# ToolManager
# ---------------------------------------------------------------------------


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

    async def switch_source(self, name: str, use_managed: bool) -> ManagedTool:
        """Switch a tool between managed and external (PATH) source."""
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Unknown tool: {name}")

        if use_managed:
            if not tool.managed_path:
                raise ValueError(f"No managed installation found for {name}")
            version = await self._get_installed_version(tool.managed_path, name)
            tool.binary_path = tool.managed_path
            tool.current_version = version
            tool.managed = True
        else:
            if not tool.external_path:
                raise ValueError(f"No external installation found for {name}")
            version = await self._get_installed_version(tool.external_path, name)
            tool.binary_path = tool.external_path
            tool.current_version = version
            tool.managed = False

        self._tools[name] = tool
        return tool

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

        Priority: RCFlow managed directory first, then PATH lookup.
        """
        binary_names = {"claude_code": "claude", "codex": "codex", "opencode": "opencode"}
        binary_name = binary_names.get(name, name)

        # Probe both sources so the UI can offer a switch
        mp = self._managed_binary_path(name)
        managed_str = str(mp) if mp.is_file() and _is_executable(mp) else None
        which_str = shutil.which(binary_name)
        # Don't report PATH hit if it points at the managed binary
        if which_str and managed_str and Path(which_str).resolve() == mp.resolve():
            which_str = None

        # 1. RCFlow managed directory (preferred)
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
                external_path=which_str,
            )

        # 2. PATH lookup (external)
        if which_str:
            version = await self._get_installed_version(which_str, name)
            return ManagedTool(
                name=name,
                binary_name=binary_name,
                binary_path=which_str,
                current_version=version,
                managed=False,
                managed_path=managed_str,
                external_path=which_str,
            )

        # 3. Not found
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
                tmp_path.rename(binary_path)
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
                    tmp_path.rename(binary_path)
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
                        zf.extractall(tmp_dir)
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
                tmp_path.rename(binary_path)
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
                tmp_path.rename(binary_path)
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
                    zf.extractall(tmp_dir)
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


async def _verify_binary(binary_path: str) -> tuple[bool, str]:
    """Check if a binary can execute on this system.

    Returns ``(True, "")`` on success, or ``(False, stderr_output)`` on failure.
    Used to detect glibc mismatches so the installer can fall back to musl.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            binary_path,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            return True, ""
        return False, stderr.decode("utf-8", errors="replace").strip()
    except FileNotFoundError:
        return False, "binary not found"
    except Exception as exc:
        return False, str(exc)


def _detect_opencode_asset() -> tuple[str, str]:
    """Return ``(asset_base, ext)`` for the current platform.

    For example ``("opencode-linux-x64", ".tar.gz")`` or
    ``("opencode-darwin-arm64", ".zip")``.
    """
    machine = platform.machine().lower()

    if sys.platform == "win32":
        arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
        return f"opencode-windows-{arch}", ".zip"

    if sys.platform == "darwin":
        arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
        return f"opencode-darwin-{arch}", ".zip"

    # Linux
    if machine in ("aarch64", "arm64"):
        base = "opencode-linux-arm64"
        if _is_musl():
            base += "-musl"
        return base, ".tar.gz"
    if machine in ("x86_64", "amd64"):
        base = "opencode-linux-x64"
        if _is_musl():
            base += "-musl"
        return base, ".tar.gz"
    raise RuntimeError(f"Unsupported architecture for OpenCode: {machine}")


def _find_opencode_binary(extract_dir: Path, members: list[str]) -> Path | None:
    """Find the opencode CLI binary among extracted archive members.

    Skips desktop/electron variants; returns the first plain ``opencode``
    (or ``opencode.exe`` on Windows) executable found.
    """
    exe = ".exe" if sys.platform == "win32" else ""
    target_name = f"opencode{exe}"
    for member in members:
        p = extract_dir / member
        if not p.is_file():
            continue
        if p.name == target_name and "desktop" not in member.lower() and "electron" not in member.lower():
            return p
    return None


async def _fetch_codex_checksums(client: httpx.AsyncClient, tag: str) -> dict[str, str]:
    """Download and parse ``checksums.txt`` for a Codex GitHub release.

    Returns ``{filename: sha256_hex}``.  If the file is absent (older release)
    logs a warning and returns an empty dict so the install can still proceed.
    """
    url = f"{CODEX_RELEASE_BASE}/{tag}/checksums.txt"
    try:
        resp = await client.get(url, timeout=_CHECK_TIMEOUT)
        resp.raise_for_status()
        return _parse_codex_checksums(resp.text)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            logger.warning("Codex checksums.txt not found for tag %s — skipping integrity check", tag)
            return {}
        raise


def _parse_codex_checksums(text: str) -> dict[str, str]:
    """Parse a checksums.txt file into ``{filename: sha256_hex}``."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2:
            sha256_hex, filename = parts[0], parts[-1].lstrip("*")
            result[filename] = sha256_hex
    return result


def _verify_codex_asset_checksum(content: bytes, asset_name: str, checksums: dict[str, str]) -> None:
    """Verify *content* matches the SHA-256 in *checksums* for *asset_name*.

    Raises ValueError on a mismatch.  If the asset is not listed in *checksums*
    (e.g. the release predates the checksums file) a warning is logged and the
    function returns without error — callers should treat an empty *checksums*
    dict as a signal that verification was skipped.
    """
    if not checksums:
        return
    expected = checksums.get(asset_name)
    if expected is None:
        logger.warning("No checksum entry for %r in checksums.txt — skipping verification", asset_name)
        return
    actual = hashlib.sha256(content).hexdigest()
    if actual != expected:
        raise ValueError(f"Codex checksum mismatch for {asset_name!r}: expected {expected!r}, got {actual!r}")
    logger.debug("Codex asset checksum verified: %s", asset_name)


def _find_codex_binary(extract_dir: Path, members: list[str]) -> Path | None:
    """Find the main codex binary among extracted tarball members.

    The release tarball contains a single file named ``codex-<target>``
    (e.g. ``codex-x86_64-unknown-linux-gnu``).  This helper locates that
    file regardless of the exact target suffix or directory nesting.
    """
    for member in members:
        p = extract_dir / member
        if not p.is_file():
            continue
        name = p.name
        if name.startswith("codex") and "proxy" not in name and "runner" not in name and "sandbox" not in name:
            return p
    return None


def _is_executable(path: Path) -> bool:
    """Check if a path points to an executable file."""
    if sys.platform == "win32":
        return path.is_file() and path.suffix.lower() in (".exe", ".cmd", ".bat", ".com")
    return os.access(path, os.X_OK)
