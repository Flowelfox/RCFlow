"""Platform / architecture detection for managed-tool downloads.

These free functions read ``sys.platform`` and ``platform.machine()`` and
intentionally look one another up via module-global names (``_is_musl``,
``_glibc_too_old``) so tests can patch them in this module's namespace.
"""

from __future__ import annotations

import platform
import re
import sys
from pathlib import Path

from src.services.tools.constants import _CODEX_MIN_GLIBC


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
