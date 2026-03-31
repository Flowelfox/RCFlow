"""Project-scoped API endpoints.

Provides endpoints for querying data filtered to a specific project directory,
such as artifacts discovered within that project.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select

from src.api.deps import verify_http_api_key
from src.models.db import Artifact as ArtifactModel

if TYPE_CHECKING:
    from src.config import Settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Projects"])


@router.get(
    "/projects/{project_name}/artifacts",
    summary="List artifacts for a project",
    description=(
        "Returns all artifacts whose file path is under the given project directory. "
        "The project name is resolved against the configured PROJECTS_DIR paths. "
        "Returns 404 if the project name does not resolve to a known directory."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def list_project_artifacts(
    project_name: str,
    request: Request,
    limit: int = Query(200, description="Maximum number of results"),
    offset: int = Query(0, description="Offset for pagination"),
) -> dict[str, Any]:
    """List artifacts that belong to a project directory."""
    settings: Settings = request.app.state.settings
    db_session_factory = request.app.state.db_session_factory

    # Resolve project name to an absolute path
    project_path: str | None = None
    for projects_dir in settings.projects_dirs:
        candidate = projects_dir / project_name
        if candidate.is_dir():
            project_path = str(candidate)
            break

    if project_path is None:
        raise HTTPException(
            status_code=404,
            detail=f"Project not found: {project_name}",
        )

    if db_session_factory is None:
        return {"project_name": project_name, "project_path": project_path, "artifacts": []}

    # Match artifacts whose file_path starts with the project directory.
    # Append "/" to avoid false matches on projects with a common prefix
    # (e.g. "RCFlow" vs "RCFlowExtra").
    path_prefix = project_path.rstrip("/") + "/"

    async with db_session_factory() as db:
        stmt = (
            select(ArtifactModel)
            .where(ArtifactModel.backend_id == settings.RCFLOW_BACKEND_ID)
            .where(ArtifactModel.file_path.like(f"{path_prefix}%"))
            .order_by(ArtifactModel.discovered_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await db.execute(stmt)
        artifacts = result.scalars().all()

    return {
        "project_name": project_name,
        "project_path": project_path,
        "artifacts": [
            {
                "artifact_id": str(a.id),
                "file_path": a.file_path,
                "file_name": a.file_name,
                "file_extension": a.file_extension,
                "file_size": a.file_size,
                "mime_type": a.mime_type,
                "discovered_at": a.discovered_at.isoformat() if a.discovered_at else None,
                "modified_at": a.modified_at.isoformat() if a.modified_at else None,
                "session_id": str(a.session_id) if a.session_id else None,
            }
            for a in artifacts
        ],
    }
