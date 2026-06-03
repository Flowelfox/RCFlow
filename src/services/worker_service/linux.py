"""Linux systemd backend for the worker service (staged).

Read-only introspection (``status``/``detect``) is implemented so ``rcflow
status`` and GUI adoption work today.  The mutating operations are intentionally
staged: the GUI already drives systemd via D-Bus in ``src/gui/linux_app.py``;
folding that logic in here (and the ``pkexec`` fallback) is the follow-up that
makes the Linux GUI and CLI share this controller.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from src.services.worker_service.base import (
    ServiceStatus,
    find_listening_pid,
    port_probe,
    resolve_port,
)
from src.services.worker_service.config import WORKER_SERVICE_UNIT_LINUX

if TYPE_CHECKING:
    from collections.abc import Iterator

_UNIT = WORKER_SERVICE_UNIT_LINUX

_STAGED = (
    "systemd worker-service control is not yet wired into this controller; "
    "use `systemctl --user`/`sudo systemctl` or the Linux GUI for now"
)


def _systemctl_ok(*args: str) -> bool:
    try:
        return subprocess.run(["systemctl", *args], capture_output=True, text=True, check=False).returncode == 0
    except OSError:
        return False


class SystemdWorkerService:
    """systemd-backed controller (introspection live; mutators staged)."""

    def install(self, *, enable: bool, port: int | None = None) -> None:
        """Staged: install the systemd unit (see ``_STAGED``)."""
        raise NotImplementedError(_STAGED)

    def uninstall(self) -> None:
        """Staged: remove the systemd unit."""
        raise NotImplementedError(_STAGED)

    def start(self) -> None:
        """Staged: ``StartUnit`` via systemd."""
        raise NotImplementedError(_STAGED)

    def stop(self) -> None:
        """Staged: ``StopUnit`` via systemd."""
        raise NotImplementedError(_STAGED)

    def restart(self) -> None:
        """Staged: ``RestartUnit`` via systemd."""
        raise NotImplementedError(_STAGED)

    def enable(self) -> None:
        """Staged: ``EnableUnitFiles`` via systemd."""
        raise NotImplementedError(_STAGED)

    def disable(self) -> None:
        """Staged: ``DisableUnitFiles`` via systemd."""
        raise NotImplementedError(_STAGED)

    def status(self) -> ServiceStatus:
        """Report state from ``is-active``/``is-enabled``, corroborated by a port probe."""
        port = resolve_port()
        running = _systemctl_ok("is-active", "--quiet", _UNIT) or port_probe(port)
        enabled = _systemctl_ok("is-enabled", "--quiet", _UNIT)
        installed = enabled or _systemctl_ok("status", _UNIT) or running
        return ServiceStatus(
            installed=installed,
            running=running,
            enabled=enabled,
            pid=find_listening_pid(port) if running else None,
            port=port,
        )

    def detect(self) -> ServiceStatus:
        """Lightweight adopt probe (port + ``is-enabled``)."""
        port = resolve_port()
        running = port_probe(port)
        return ServiceStatus(
            installed=_systemctl_ok("is-enabled", "--quiet", _UNIT),
            running=running,
            enabled=_systemctl_ok("is-enabled", "--quiet", _UNIT),
            pid=find_listening_pid(port) if running else None,
            port=port,
        )

    def logs(self, *, follow: bool = False, lines: int = 200) -> Iterator[str]:
        """Stream the unit journal (``journalctl -u``)."""
        args = ["journalctl", "-u", _UNIT, "-n", str(lines), "--no-pager", "-o", "cat"]
        if follow:
            args.append("-f")
        try:
            proc = subprocess.Popen(args, stdout=subprocess.PIPE, text=True)
        except OSError:
            return
        assert proc.stdout is not None  # noqa: S101
        try:
            for line in proc.stdout:
                yield line.rstrip("\n")
        finally:
            proc.terminate()
