"""API route sub-modules.

Each module defines its own ``router`` (an :class:`~fastapi.APIRouter`)
which is collected and included into the main API router by
:mod:`src.api.http`.
"""

from src.api.routes.artifacts import router as artifacts_router
from src.api.routes.auth import router as auth_router
from src.api.routes.config import router as config_router
from src.api.routes.sessions import router as sessions_router
from src.api.routes.tasks import router as tasks_router
from src.api.routes.tools import router as tools_router
from src.api.routes.uploads import router as uploads_router
from src.api.routes.worktrees import router as worktrees_router

__all__ = [
    "artifacts_router",
    "auth_router",
    "config_router",
    "sessions_router",
    "tasks_router",
    "tools_router",
    "uploads_router",
    "worktrees_router",
]
