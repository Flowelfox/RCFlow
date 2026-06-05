"""Windows NSSM/SCM backend for the worker service (staged).

Read-only introspection works via ``sc query`` + the port probe; the mutating
operations are staged behind ``NotImplementedError`` until the NSSM control logic
is wired in (the follow-up that lets the Windows GUI and CLI share this
controller).
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from src.services.worker_service.base import ServiceStatus, port_probe, resolve_port
from src.services.worker_service.config import WORKER_SERVICE_NAME_WINDOWS

if TYPE_CHECKING:
    from collections.abc import Iterator

_NAME = WORKER_SERVICE_NAME_WINDOWS

_STAGED = "Windows worker-service control is not yet wired into this controller; use the Services app or NSSM for now"


def _sc_query() -> str:
    try:
        return subprocess.run(["sc", "query", _NAME], capture_output=True, text=True, check=False).stdout
    except OSError:
        return ""


class WindowsWorkerService:
    """SCM/NSSM-backed controller (introspection live; mutators staged)."""

    def install(self, *, enable: bool, port: int | None = None) -> None:
        """Staged: register the NSSM/SCM service (see ``_STAGED``)."""
        raise NotImplementedError(_STAGED)

    def uninstall(self) -> None:
        """Staged: remove the service."""
        raise NotImplementedError(_STAGED)

    def start(self) -> None:
        """Staged: ``sc start`` / ``nssm start``."""
        raise NotImplementedError(_STAGED)

    def stop(self) -> None:
        """Staged: ``sc stop`` / ``nssm stop``."""
        raise NotImplementedError(_STAGED)

    def restart(self) -> None:
        """Staged: restart the service."""
        raise NotImplementedError(_STAGED)

    def enable(self) -> None:
        """Staged: ``sc config start= auto``."""
        raise NotImplementedError(_STAGED)

    def disable(self) -> None:
        """Staged: ``sc config start= demand``."""
        raise NotImplementedError(_STAGED)

    def status(self) -> ServiceStatus:
        """``sc query`` corroborated by a port probe."""
        port = resolve_port()
        query = _sc_query()
        installed = bool(query) and "FAILED 1060" not in query
        running = "RUNNING" in query or port_probe(port)
        return ServiceStatus(installed=installed, running=running, enabled=installed, pid=None, port=port)

    def detect(self) -> ServiceStatus:
        """Lightweight adopt probe (port + ``sc query``)."""
        port = resolve_port()
        running = port_probe(port)
        return ServiceStatus(installed=bool(_sc_query()), running=running, enabled=False, pid=None, port=port)

    def logs(self, *, follow: bool = False, lines: int = 200) -> Iterator[str]:
        """Staged: tail the NSSM stdout redirect (none yet)."""
        return iter(())
