"""Platform dispatch for the worker-service controller."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.services.worker_service.base import WorkerServiceController


def get_controller(platform: str | None = None) -> WorkerServiceController:
    """Return the worker-service controller for *platform* (default: current OS).

    Both the CLI (``src/__main__.py``) and the GUI (``src/gui/*``) obtain their
    controller here so they command and query the same service instance.
    """
    plat = platform or sys.platform
    if plat == "darwin":
        from src.services.worker_service.macos import MacOSWorkerService  # noqa: PLC0415

        return MacOSWorkerService()
    if plat.startswith("linux"):
        from src.services.worker_service.linux import SystemdWorkerService  # noqa: PLC0415

        return SystemdWorkerService()
    if plat == "win32":
        from src.services.worker_service.windows import WindowsWorkerService  # noqa: PLC0415

        return WindowsWorkerService()
    raise RuntimeError(f"No worker-service controller for platform {plat!r}")
