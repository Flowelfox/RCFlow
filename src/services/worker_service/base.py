"""Platform-neutral worker-service controller interface + shared helpers.

A :class:`WorkerServiceController` is the single object both the CLI
(``src/__main__.py``) and the GUI (``src/gui/*``) use to command and query the
one OS-managed worker service.  Whichever of CLI/GUI acts first starts the
service; the other *adopts* and controls the same instance.  State is read from
the OS service manager and corroborated by a loopback port probe, so a worker the
caller did not spawn is still reported and controlled correctly.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True)
class ServiceStatus:
    """Snapshot of the worker service state.

    - ``installed`` — a supervisor registration exists (plist / unit / service).
    - ``running`` — a worker is currently serving (manager state or port probe).
    - ``enabled`` — configured to autostart at login/boot (independent of running).
    - ``pid`` / ``port`` — best-effort identifiers, ``None`` when unknown.
    - ``detail`` — human-readable diagnostics (e.g. a ``launchctl print`` excerpt).
    """

    installed: bool
    running: bool
    enabled: bool
    pid: int | None = None
    port: int | None = None
    detail: str = ""


@runtime_checkable
class WorkerServiceController(Protocol):
    """Command + query surface for the worker service on one platform.

    ``enable``/``disable`` toggle autostart; ``start``/``stop``/``restart``
    control the running instance.  The two axes are independent (systemd/launchd
    semantics): disabling does not stop a running worker, and stopping does not
    clear the autostart setting.  ``stop`` MUST be final — it removes the
    supervisor's respawn so an explicit stop is never undone.
    """

    def install(self, *, enable: bool, port: int | None = None) -> None:
        """Register the service with the OS supervisor (idempotent)."""
        ...

    def uninstall(self) -> None:
        """Stop and remove the service registration entirely."""
        ...

    def start(self) -> None:
        """Start the worker now (no-op if already running)."""
        ...

    def stop(self) -> None:
        """Stop the worker now, finally (no respawn)."""
        ...

    def restart(self) -> None:
        """Restart the worker."""
        ...

    def enable(self) -> None:
        """Enable autostart at login/boot."""
        ...

    def disable(self) -> None:
        """Disable autostart (leaves a running worker running)."""
        ...

    def status(self) -> ServiceStatus:
        """Query installed/running/enabled state from the supervisor."""
        ...

    def detect(self) -> ServiceStatus:
        """Probe whether *some* worker is already serving on the port (adopt path).

        Independent of the supervisor (uses the port + listening pid), so the
        GUI can adopt a CLI-started or boot-started worker it did not spawn.
        """
        ...

    def logs(self, *, follow: bool = False, lines: int = 200) -> Iterator[str]:
        """Yield recent service log lines (``follow`` tails indefinitely)."""
        ...


# ── Shared, OS-agnostic detection helpers ───────────────────────────────────


def resolve_port() -> int:
    """Return the worker's configured TCP port (from settings)."""
    from src.config import get_settings  # noqa: PLC0415

    return int(get_settings().RCFLOW_PORT)


def port_probe(port: int, *, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    """Return True if something accepts a TCP connection on ``host:port``.

    The universal "is a worker serving?" signal — works regardless of which
    process (GUI child, CLI-started service, boot service) owns the listener.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_listening_pid(port: int) -> int | None:
    """Best-effort pid of the process listening on ``port`` (or ``None``).

    Uses ``lsof`` on macOS, ``/proc/net/tcp`` walking on Linux; returns ``None``
    on Windows / when the lookup is unavailable.  Reporting only — control still
    goes through the supervisor.
    """
    if sys.platform == "darwin":
        return _find_listening_pid_lsof(port)
    if sys.platform.startswith("linux"):
        return _find_listening_pid_proc(port)
    return None


def _find_listening_pid_lsof(port: int) -> int | None:
    try:
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.stdout.splitlines():
        line = line.strip()
        if line.isdigit():
            return int(line)
    return None


def _find_listening_pid_proc(port: int) -> int | None:
    """Resolve the listening pid via ``/proc`` on Linux (cross-uid safe)."""
    import os  # noqa: PLC0415

    hex_port = f"{port:04X}"
    inodes: set[str] = set()
    for tcp_file in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(tcp_file, encoding="utf-8") as fh:
                next(fh, None)  # header
                for row in fh:
                    cols = row.split()
                    if len(cols) < 10:
                        continue
                    local, state, inode = cols[1], cols[3], cols[9]
                    # 0A == TCP_LISTEN
                    if state == "0A" and local.rsplit(":", 1)[-1].upper() == hex_port:
                        inodes.add(inode)
        except OSError:
            continue
    if not inodes:
        return None
    for pid_dir in filter(str.isdigit, os.listdir("/proc")):
        fd_dir = f"/proc/{pid_dir}/fd"
        try:
            for fd in os.listdir(fd_dir):
                try:
                    target = os.readlink(f"{fd_dir}/{fd}")
                except OSError:
                    continue
                if target.startswith("socket:[") and target[8:-1] in inodes:
                    return int(pid_dir)
        except OSError:
            continue
    return None
