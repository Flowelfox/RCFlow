"""Headless self-update flow behind the ``rcflow update`` CLI verb.

Reuses the pure update logic from :mod:`src.gui.updater` (version resolution
and comparison, GitHub release fetching, asset selection, atomic download)
without the GUI's thread/listener machinery or its 24-hour settings cache —
the CLI always hits the network and ignores the GUI's dismissed-version state.

Install step per platform:

- **Linux** (frozen ``.deb`` install): runs ``dpkg -i`` on the downloaded
  package (prefixed with ``sudo`` when not root). The package's own
  prerm/postinst scripts stop the service, run migrations, and restart it —
  no extra service handling is needed here.
- **Windows / macOS**: launches the downloaded installer and leaves the rest
  to the user, matching the GUI flow.
- **Dev (unfrozen) checkouts**: install is refused (use ``git pull`` +
  ``uv sync``); ``--check`` still works.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from typing import TYPE_CHECKING

from src.gui.updater import (
    HttpUpdateFetcher,
    cleanup_partial_downloads,
    detect_arch,
    detect_platform,
    download_path,
    is_newer,
    launch_installer,
    resolve_current_version,
    stream_download,
)
from src.paths import is_frozen

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from src.gui.updater import UpdateFetcher, UpdateInfo

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_UNSUPPORTED = 2
EXIT_UPDATE_AVAILABLE = 3

_MIB = 1024 * 1024


def run_update(
    *,
    check_only: bool,
    assume_yes: bool,
    fetcher: UpdateFetcher | None = None,
    plat: str | None = None,
) -> int:
    """Check GitHub for the latest release and (optionally) install it.

    Returns a process exit code:

    - ``0`` — up to date, updated successfully, or installer launched
    - ``1`` — check/download/install failure, declined, or non-interactive
      terminal without ``--yes``
    - ``2`` — self-update unsupported here (dev checkout, or no release asset
      for this platform/arch)
    - ``3`` — ``--check`` only: an update is available
    """
    plat = plat or detect_platform()

    current = resolve_current_version()
    if not current:
        print("ERROR: cannot determine the current version.", file=sys.stderr)
        return EXIT_ERROR

    fetcher = fetcher or HttpUpdateFetcher(plat=plat)
    try:
        info = fetcher.fetch_latest()
    except RuntimeError as exc:
        print(f"ERROR: update check failed: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if info is None:
        print("ERROR: update check failed: no release information returned.", file=sys.stderr)
        return EXIT_ERROR

    print(f"Current version: {current}")
    print(f"Latest release:  {info.version}")

    if not is_newer(info.version, current):
        print("rcflow is up to date.")
        return EXIT_OK

    if check_only:
        print("Update available — run 'rcflow update' to install.")
        return EXIT_UPDATE_AVAILABLE

    if not is_frozen():
        print(
            "ERROR: self-update only works for installed builds. In a source checkout use: git pull && uv sync",
            file=sys.stderr,
        )
        return EXIT_UNSUPPORTED

    if not info.download_url:
        print(
            f"ERROR: no release asset for this platform ({plat}/{detect_arch()}). "
            f"Download manually: {info.release_url}",
            file=sys.stderr,
        )
        return EXIT_UNSUPPORTED

    if not _confirm(info.version, assume_yes=assume_yes):
        return EXIT_ERROR

    try:
        dest = _download(info, plat)
    except (RuntimeError, OSError) as exc:
        print(f"ERROR: download failed: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if plat == "linux":
        rc = _install_linux_deb(dest)
        if rc != 0:
            print(
                f"ERROR: dpkg exited with {rc}. Fix with 'sudo dpkg --configure -a' and re-run 'rcflow update'.",
                file=sys.stderr,
            )
            return EXIT_ERROR
        print(f"Updated to v{info.version}. Worker service restarted by the package scripts.")
        return EXIT_OK

    launch_installer(dest, plat)
    print("Installer launched — complete the update there. The worker keeps running until the installer closes it.")
    return EXIT_OK


def _confirm(version: str, *, assume_yes: bool) -> bool:
    """Ask the user to confirm the install; ``--yes`` skips the prompt."""
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(
            "ERROR: non-interactive terminal — re-run with --yes to update unattended.",
            file=sys.stderr,
        )
        return False
    answer = input(f"Install RCFlow v{version}? [y/N] ").strip().lower()
    return answer in ("y", "yes")


def _progress_printer(info: UpdateInfo) -> Callable[[int, int], None] | None:
    """Return a stdout progress callback (tty) or print a one-liner (non-tty)."""
    name = info.asset_name or "update"
    if not sys.stdout.isatty():
        size = f" ({info.asset_size / _MIB:.1f} MiB)" if info.asset_size else ""
        print(f"Downloading {name}{size}…")
        return None

    def _print(received: int, total: int) -> None:
        pct = received * 100 // total if total else 0
        print(
            f"\rDownloading {name}: {received / _MIB:.1f}/{total / _MIB:.1f} MiB ({pct}%)",
            end="",
            flush=True,
        )

    return _print


def _download(info: UpdateInfo, plat: str) -> Path:
    """Download *info*'s installer (reusing a complete cached file) and return its path."""
    cleanup_partial_downloads()
    dest = download_path(info, plat)
    if info.asset_size is not None and dest.exists() and dest.stat().st_size == info.asset_size:
        print(f"Using cached download: {dest}")
        return dest
    on_progress = _progress_printer(info)
    stream_download(info, dest, on_progress)
    if on_progress is not None:
        print()  # terminate the \r progress line
    return dest


def _install_linux_deb(deb: Path) -> int:
    """Install *deb* via dpkg (sudo when not root), streaming its output; return the exit code."""
    cmd = ["dpkg", "-i", str(deb)]
    if os.geteuid() != 0:
        cmd = ["sudo", *cmd]
    print("Running: " + shlex.join(cmd))
    return subprocess.run(cmd, check=False).returncode
