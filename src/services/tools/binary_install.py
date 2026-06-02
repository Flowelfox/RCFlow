"""Binary placement, verification, and archive-extraction helpers.

Pure(ish) functions used by :class:`~src.services.tools.manager.ToolManager`
to swap binaries atomically, verify they run, locate the right file inside a
release archive, and check release checksums.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import sys
from typing import TYPE_CHECKING

import httpx

from src.services.tools.constants import _CHECK_TIMEOUT, CODEX_RELEASE_BASE

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


def _atomic_install_binary(tmp_path: Path, binary_path: Path) -> None:
    """Atomically swap *binary_path* with the freshly-downloaded *tmp_path*.

    POSIX:  ``Path.replace`` is atomic and works even when the target is
    executing — overwriting an in-use binary just unlinks the old inode while
    the running process keeps its file descriptor open.

    Windows:  ``os.replace`` (and Win32 ``MoveFileExW(MOVEFILE_REPLACE_EXISTING)``)
    refuses to overwrite a target that any process holds open, including the
    *kernel-loaded image* of a running ``.exe``.  Updating Claude Code
    immediately after using it therefore raises
    ``PermissionError: [WinError 5] Access is denied``.

    Workaround that actually works on Windows: a directory-entry rename of
    the live ``.exe`` to a sibling name **is** allowed by NTFS even while the
    image is loaded.  We move the old binary aside to ``<name>.<pid>.old``,
    drop the new file into place, and best-effort-delete the parked file.
    Anything we can't delete now (still mapped) is swept up by
    :meth:`ToolManager._cleanup_parked_binaries` on the next operation.
    """
    if sys.platform != "win32":
        tmp_path.replace(binary_path)
        return

    # Fast path: no existing binary, nothing to evict.
    if not binary_path.exists():
        tmp_path.replace(binary_path)
        return

    # Try the simple replace first — if no other process holds the binary,
    # this is the cheapest path and leaves no parked file behind.
    try:
        tmp_path.replace(binary_path)
        return
    except PermissionError:
        pass

    # The binary is in use.  Move it aside, drop the new one in, sweep later.
    parked = binary_path.with_name(f"{binary_path.name}.{os.getpid()}.old")
    # Another concurrent install might have already created this name; pick
    # something definitely unique.
    counter = 0
    while parked.exists():
        counter += 1
        parked = binary_path.with_name(f"{binary_path.name}.{os.getpid()}.{counter}.old")

    binary_path.rename(parked)
    try:
        tmp_path.replace(binary_path)
    except Exception:
        # Roll back the rename so the user is left with a working binary.
        with contextlib.suppress(OSError):
            parked.rename(binary_path)
        raise

    # Best-effort cleanup of the parked copy.  The file is still memory-mapped
    # by the running process; deletion will only succeed once that handle is
    # closed.  We retry on next install via ``_cleanup_parked_binaries``.
    with contextlib.suppress(OSError, PermissionError):
        parked.unlink()


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
