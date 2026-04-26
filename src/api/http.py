"""HTTP API router — assembles sub-routers from :mod:`src.api.routes`.

Individual endpoint groups (sessions, tools, auth, tasks, artifacts,
config/health) are defined in their own modules under ``src/api/routes/``
and included here under the ``/api`` prefix.
"""

from __future__ import annotations

from fastapi import APIRouter

from src.api.routes import (
    artifacts_router,
    auth_router,
    config_router,
    models_router,
    projects_router,
    rcflow_plugins_router,
    sessions_router,
    slash_commands_router,
    tasks_router,
    telemetry_router,
    tools_router,
    uploads_router,
    worktrees_router,
)
from src.api.routes.artifacts import TEXT_EXTENSIONS as TEXT_EXTENSIONS

# Re-export for backward compatibility (used by src.core.background_tasks)
from src.api.routes.tasks import VALID_TASK_TRANSITIONS as VALID_TASK_TRANSITIONS

router = APIRouter(prefix="/api", tags=["API"])

router.include_router(config_router)
router.include_router(models_router)
router.include_router(sessions_router)
router.include_router(tools_router)
router.include_router(auth_router)
router.include_router(tasks_router)
router.include_router(artifacts_router)
router.include_router(projects_router)
router.include_router(uploads_router)
router.include_router(worktrees_router)
router.include_router(telemetry_router)
router.include_router(slash_commands_router)
router.include_router(rcflow_plugins_router)
