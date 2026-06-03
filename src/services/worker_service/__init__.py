"""Unified worker-service control shared by the CLI and the GUI.

One OS-managed service (launchd / systemd / NSSM) is the single source of truth;
:func:`get_controller` returns the platform controller both front-ends use to
start/stop/enable/disable/query that one instance.  See ``base.ServiceStatus`` and
``base.WorkerServiceController`` for the surface, and ``docs/design/deployment.md``
for the model.
"""

from __future__ import annotations

from src.services.worker_service.base import ServiceStatus, WorkerServiceController
from src.services.worker_service.factory import get_controller

__all__ = ["ServiceStatus", "WorkerServiceController", "get_controller"]
