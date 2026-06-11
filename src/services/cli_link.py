"""Install / detect a user-PATH ``rcflow`` CLI launcher for GUI installs.

GUI / standalone installs (notably the macOS ``.dmg``, which is drag-to-
Applications and runs no installer) ship the ``rcflow`` executable *inside* the
app bundle and never put it on ``PATH``. This module lets the GUI offer a
one-click "install the ``rcflow`` command" action that points ``~/.local/bin/
rcflow`` at the running executable, plus a status check so the GUI can show
whether the command is wired up.

- **macOS / Linux** — a symlink ``~/.local/bin/rcflow`` → the running executable.
- **Windows** — a ``~/.local/bin/rcflow.cmd`` shim that forwards to the
  executable (symlinks need Developer Mode / admin; a ``.cmd`` does not).

Only meaningful for frozen builds: from source the ``rcflow`` console-script is
already on ``PATH`` via the project venv. ``~/.local/bin`` is the same directory
the install scripts use, and may still need adding to ``PATH`` — see
:func:`bin_dir_on_path`.
"""

from __future__ import annotations

import os
import sys
from enum import Enum
from pathlib import Path

from src.paths import is_frozen


class CliLinkStatus(Enum):
    """State of the user-PATH ``rcflow`` launcher."""

    INSTALLED = "installed"  # launcher exists and points at this executable
    MISSING = "missing"  # no launcher present
    MISMATCH = "mismatch"  # launcher exists but points elsewhere (old build)


def bin_dir() -> Path:
    """User bin directory the launcher is installed into (``~/.local/bin``)."""
    return Path.home() / ".local" / "bin"


def link_path() -> Path:
    """Full path of the launcher (``rcflow`` POSIX, ``rcflow.cmd`` Windows)."""
    name = "rcflow.cmd" if sys.platform == "win32" else "rcflow"
    return bin_dir() / name


def cli_target() -> Path:
    """Return the executable the launcher points at (the running rcflow binary)."""
    return Path(sys.executable).resolve()


def is_supported() -> bool:
    """Whether offering CLI-link install makes sense for this build.

    Only frozen builds need it; from source ``rcflow`` is already on ``PATH``.
    """
    return is_frozen()


def _windows_shim_target(text: str) -> str | None:
    """Extract the executable path a generated ``.cmd`` shim forwards to."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('"') and stripped.endswith('" %*'):
            return stripped[1 : -len('" %*')]
    return None


def status() -> CliLinkStatus:
    """Return whether the launcher exists and matches the running executable."""
    path = link_path()
    target = str(cli_target())
    if sys.platform == "win32":
        if not path.exists():
            return CliLinkStatus.MISSING
        try:
            current = _windows_shim_target(path.read_text(encoding="utf-8"))
        except OSError:
            return CliLinkStatus.MISMATCH
        return CliLinkStatus.INSTALLED if current == target else CliLinkStatus.MISMATCH

    # POSIX: a symlink (or any file) at the launcher path.
    if not path.is_symlink() and not path.exists():
        return CliLinkStatus.MISSING
    try:
        resolved = str(path.resolve())
    except OSError:
        return CliLinkStatus.MISMATCH
    return CliLinkStatus.INSTALLED if resolved == target else CliLinkStatus.MISMATCH


def is_installed() -> bool:
    """Return True when the launcher exists and points at this executable."""
    return status() is CliLinkStatus.INSTALLED


def install() -> Path:
    """Create / refresh the launcher so ``rcflow`` resolves to this executable.

    Returns the launcher path. Raises ``OSError`` on failure (e.g. permission).
    """
    path = link_path()
    target = cli_target()
    path.parent.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        # A .cmd shim avoids the Developer-Mode/admin requirement of symlinks.
        path.write_text(f'@echo off\r\n"{target}" %*\r\n', encoding="utf-8")
        return path

    # Replace any existing launcher (broken symlink, old build) atomically-ish.
    if path.is_symlink() or path.exists():
        path.unlink()
    path.symlink_to(target)
    return path


def bin_dir_on_path() -> bool:
    """Whether :func:`bin_dir` is on the current process ``PATH``.

    When false the GUI should tell the user to add it to their shell profile —
    the launcher exists but the shell won't find it yet.
    """
    raw = os.environ.get("PATH", "")
    target = bin_dir()
    for entry in raw.split(os.pathsep):
        if not entry:
            continue
        try:
            if Path(entry).expanduser() == target:
                return True
        except (OSError, ValueError):
            continue
    return False
