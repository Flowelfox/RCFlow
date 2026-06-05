"""Central configuration for the RCFlow worker service across platforms.

Single source of truth for the supervisor labels, file locations, and the
canonical service definition each platform's :class:`WorkerServiceController`
installs.  Previously these literals were duplicated across ``install_macos.sh``,
``get-worker.sh``, ``bundle.py`` and ``src/gui/macos.py`` and drifted apart (most
visibly: the launchd plist used ``KeepAlive=true`` which respawns the worker even
after an explicit stop).  Everything now derives from here so every install path
and both controllers (CLI + GUI) agree.
"""

from __future__ import annotations

import sys
from pathlib import Path

from src.paths import get_data_dir, is_frozen

# ── Supervisor identifiers (one per platform) ───────────────────────────────

# macOS: the *worker service* LaunchAgent (the FastAPI backend, ``rcflow run``).
# Distinct from the GUI-autostart agent ``com.rcflow.worker`` which only launches
# the menu-bar dashboard at login — see ``src/gui/macos.py``.
WORKER_SERVICE_LABEL_MACOS = "com.rcflow.server"
GUI_AUTOSTART_LABEL_MACOS = "com.rcflow.worker"

# Linux: the systemd unit name.
WORKER_SERVICE_UNIT_LINUX = "rcflow.service"

# Windows: the NSSM / Service Control Manager service name.
WORKER_SERVICE_NAME_WINDOWS = "RCFlow"


def worker_log_paths() -> tuple[Path, Path]:
    """Return the ``(stdout, stderr)`` log paths the service writes to.

    Anchored on :func:`src.paths.get_data_dir` so the service-managed worker and
    the GUI agree on where logs land (the GUI streams these when it adopts a
    service-owned worker it did not spawn).
    """
    logs = get_data_dir() / "logs"
    return logs / "service-stdout.log", logs / "service-stderr.log"


def resolve_worker_binary() -> tuple[list[str], str]:
    """Resolve how to invoke ``rcflow run`` for a supervisor's launch command.

    Returns ``(argv_prefix, cwd)`` where ``argv_prefix`` is the command minus the
    ``run`` subcommand:

    - **frozen** (installed ``.app`` / binary) → ``[<rcflow executable>]``; the
      executable is ``sys.executable`` (e.g. ``…/RCFlow Worker.app/Contents/MacOS/
      rcflow`` or ``~/.local/lib/rcflow/rcflow``).
    - **dev** → ``[<python>, "-m", "src"]``.

    ``cwd`` is the install directory so relative data/log paths resolve.
    """
    if is_frozen():
        exe = Path(sys.executable).resolve()
        return [str(exe)], str(exe.parent)
    return [sys.executable, "-m", "src"], str(Path.cwd())


def macos_plist_path() -> Path:
    """Path of the worker-service LaunchAgent plist for the current user."""
    return Path.home() / "Library" / "LaunchAgents" / f"{WORKER_SERVICE_LABEL_MACOS}.plist"


def macos_plist_dict(*, run_at_load: bool, keep_alive_crash_only: bool = True) -> dict[str, object]:
    """Build the canonical launchd plist for the worker service.

    ``keep_alive_crash_only`` is the critical correctness knob: ``KeepAlive`` is
    set to ``{"SuccessfulExit": False}`` (respawn only after an abnormal/crash
    exit) rather than ``True``.  A clean, operator-initiated ``launchctl bootout``
    therefore stays down — "stop always means stop" — while genuine crashes still
    recover.  ``run_at_load`` reflects the *enabled* (autostart at login) axis,
    kept independent from start/stop.
    """
    argv_prefix, cwd = resolve_worker_binary()
    stdout_path, stderr_path = worker_log_paths()
    keep_alive: object = {"SuccessfulExit": False} if keep_alive_crash_only else False
    return {
        "Label": WORKER_SERVICE_LABEL_MACOS,
        "ProgramArguments": [*argv_prefix, "run"],
        "WorkingDirectory": cwd,
        "RunAtLoad": run_at_load,
        "KeepAlive": keep_alive,
        "ProcessType": "Background",
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
    }
