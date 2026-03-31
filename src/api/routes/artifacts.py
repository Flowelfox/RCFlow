from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select

from src.api.deps import verify_http_api_key
from src.config import Settings, update_settings_file
from src.models.db import Artifact as ArtifactModel

if TYPE_CHECKING:
    from src.core.session import SessionManager

# Text file extensions that support content viewing/inclusion
TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".md",
        ".txt",
        ".log",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".cfg",
        ".ini",
        ".csv",
        ".xml",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".py",
        ".sh",
        ".bash",
        ".sql",
        ".rs",
        ".go",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".jsx",
        ".tsx",
        ".vue",
        ".dart",
        ".swift",
        ".kt",
        ".r",
        ".m",
        ".mm",
    }
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Artifacts"])


def _artifact_to_dict(artifact: ArtifactModel) -> dict[str, Any]:
    """Serialize an Artifact ORM instance to a JSON-friendly dict."""
    return {
        "artifact_id": str(artifact.id),
        "backend_id": artifact.backend_id,
        "file_path": artifact.file_path,
        "file_name": artifact.file_name,
        "file_extension": artifact.file_extension,
        "file_size": artifact.file_size,
        "mime_type": artifact.mime_type,
        "discovered_at": artifact.discovered_at.isoformat() if artifact.discovered_at else None,
        "modified_at": artifact.modified_at.isoformat() if artifact.modified_at else None,
        "session_id": str(artifact.session_id) if artifact.session_id else None,
    }


@router.get(
    "/artifacts/settings",
    summary="Get artifact settings",
    description="Returns current artifact extraction configuration.",
    tags=["Artifacts"],
    dependencies=[Depends(verify_http_api_key)],
)
async def get_artifact_settings(request: Request) -> dict[str, Any]:
    """Get current artifact extraction settings."""
    settings: Settings = request.app.state.settings
    return {
        "include_pattern": settings.ARTIFACT_INCLUDE_PATTERN,
        "exclude_pattern": settings.ARTIFACT_EXCLUDE_PATTERN,
        "auto_scan": settings.ARTIFACT_AUTO_SCAN,
        "max_file_size": settings.ARTIFACT_MAX_FILE_SIZE,
    }


class UpdateArtifactSettingsRequest(BaseModel):
    include_pattern: str | None = None
    exclude_pattern: str | None = None
    auto_scan: bool | None = None
    max_file_size: int | None = None


@router.patch(
    "/artifacts/settings",
    summary="Update artifact settings",
    description="Update artifact extraction configuration.",
    tags=["Artifacts"],
    dependencies=[Depends(verify_http_api_key)],
)
async def update_artifact_settings(
    body: UpdateArtifactSettingsRequest,
    request: Request,
) -> dict[str, Any]:
    """Update artifact extraction settings."""
    updates: dict[str, str] = {}

    if body.include_pattern is not None:
        updates["ARTIFACT_INCLUDE_PATTERN"] = body.include_pattern
    if body.exclude_pattern is not None:
        updates["ARTIFACT_EXCLUDE_PATTERN"] = body.exclude_pattern
    if body.auto_scan is not None:
        updates["ARTIFACT_AUTO_SCAN"] = str(body.auto_scan).lower()
    if body.max_file_size is not None:
        updates["ARTIFACT_MAX_FILE_SIZE"] = str(body.max_file_size)

    if updates:
        update_settings_file(updates)

        # Recreate artifact scanner with new settings
        settings = Settings()  # type: ignore[call-arg]
        from src.services.artifact_scanner import ArtifactScanner  # noqa: PLC0415

        request.app.state.artifact_scanner = ArtifactScanner(
            settings,
            request.app.state.db_session_factory,
        )

    return await get_artifact_settings(request)


@router.get(
    "/artifacts",
    summary="List artifacts",
    description=(
        "Returns all artifacts for the current backend. "
        "Supports optional ?search= filter for file name/path. Sorted by discovered_at descending."
    ),
    tags=["Artifacts"],
    dependencies=[Depends(verify_http_api_key)],
)
async def list_artifacts(
    request: Request,
    search: str | None = Query(None, description="Search in file names and paths"),
    limit: int = Query(100, description="Maximum number of results"),
    offset: int = Query(0, description="Offset for pagination"),
) -> dict[str, Any]:
    """List all artifacts, optionally filtered by search query."""
    settings: Settings = request.app.state.settings
    db_session_factory = request.app.state.db_session_factory
    if db_session_factory is None:
        return {"artifacts": []}

    async with db_session_factory() as db:
        stmt = (
            select(ArtifactModel)
            .where(ArtifactModel.backend_id == settings.RCFLOW_BACKEND_ID)
            .order_by(ArtifactModel.discovered_at.desc())
        )
        if search:
            search_pattern = f"%{search}%"
            stmt = stmt.where(
                (ArtifactModel.file_name.ilike(search_pattern)) | (ArtifactModel.file_path.ilike(search_pattern))
            )
        stmt = stmt.limit(limit).offset(offset)
        result = await db.execute(stmt)
        artifacts = result.scalars().all()
        return {"artifacts": [_artifact_to_dict(a) for a in artifacts]}


@router.get(
    "/artifacts/search",
    summary="Search artifacts for autocomplete",
    description=(
        "Returns artifact file names and paths matching a query. "
        "Optimized for the $ mention autocomplete dropdown. "
        "Filters by the current backend_id."
    ),
    tags=["Artifacts"],
    dependencies=[Depends(verify_http_api_key)],
)
async def search_artifacts(
    request: Request,
    q: str | None = Query(None, description="Case-insensitive substring filter for file name/path"),
) -> dict[str, list[dict[str, Any]]]:
    """Search artifacts for autocomplete suggestions."""
    settings: Settings = request.app.state.settings
    db_session_factory = request.app.state.db_session_factory
    if db_session_factory is None:
        return {"artifacts": []}

    async with db_session_factory() as db:
        stmt = (
            select(ArtifactModel)
            .where(ArtifactModel.backend_id == settings.RCFLOW_BACKEND_ID)
            .order_by(ArtifactModel.modified_at.desc())
        )
        if q:
            search_pattern = f"%{q}%"
            stmt = stmt.where(
                (ArtifactModel.file_name.ilike(search_pattern)) | (ArtifactModel.file_path.ilike(search_pattern))
            )
        stmt = stmt.limit(10)

        result = await db.execute(stmt)
        artifacts = result.scalars().all()
        return {
            "artifacts": [
                {
                    "artifact_id": str(a.id),
                    "file_name": a.file_name,
                    "file_path": a.file_path,
                    "file_extension": a.file_extension,
                    "file_size": a.file_size,
                    "mime_type": a.mime_type or "",
                    "is_text": a.file_extension.lower() in TEXT_EXTENSIONS,
                }
                for a in artifacts
            ],
        }


@router.get(
    "/artifacts/{artifact_id}",
    summary="Get a single artifact",
    description="Returns a single artifact by ID.",
    tags=["Artifacts"],
    dependencies=[Depends(verify_http_api_key)],
)
async def get_artifact(artifact_id: str, request: Request) -> dict[str, Any]:
    """Get a single artifact by ID."""
    db_session_factory = request.app.state.db_session_factory
    if db_session_factory is None:
        raise HTTPException(status_code=404, detail="Database not configured")

    try:
        artifact_uuid = uuid.UUID(artifact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid artifact ID: {artifact_id}") from None

    async with db_session_factory() as db:
        artifact = await db.get(ArtifactModel, artifact_uuid)
        if artifact is None:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")
        return _artifact_to_dict(artifact)


@router.get(
    "/artifacts/{artifact_id}/content",
    summary="Get artifact file content",
    description=(
        "Reads and returns the raw text content of an artifact file. "
        "Supports text files up to 5MB. Returns 415 for unsupported file types."
    ),
    tags=["Artifacts"],
    dependencies=[Depends(verify_http_api_key)],
    response_class=PlainTextResponse,
)
async def get_artifact_content(artifact_id: str, request: Request) -> PlainTextResponse:
    """Read and return the raw file content of an artifact."""
    settings: Settings = request.app.state.settings
    db_session_factory = request.app.state.db_session_factory
    if db_session_factory is None:
        raise HTTPException(status_code=404, detail="Database not configured")

    try:
        artifact_uuid = uuid.UUID(artifact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid artifact ID: {artifact_id}") from None

    async with db_session_factory() as db:
        artifact = await db.get(ArtifactModel, artifact_uuid)
        if artifact is None:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")

        # Check if file exists
        file_path = Path(artifact.file_path)
        if not file_path.exists():
            raise HTTPException(status_code=404, detail="File not found on disk")

        # Check file size limit
        if artifact.file_size > settings.ARTIFACT_MAX_FILE_SIZE:
            raise HTTPException(status_code=413, detail="File too large to view")

        if artifact.file_extension.lower() not in TEXT_EXTENSIONS:
            raise HTTPException(
                status_code=415, detail=f"File type not supported for viewing: {artifact.file_extension}"
            )

        # Read file content
        try:
            content = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Try with latin-1 encoding as fallback
            try:
                content = file_path.read_text(encoding="latin-1")
            except Exception:
                raise HTTPException(status_code=500, detail="Could not decode file content") from None
        except Exception as e:
            logger.error("Error reading artifact file %s: %s", artifact.file_path, e)
            raise HTTPException(status_code=500, detail="Error reading file") from None

        return PlainTextResponse(content=content, media_type=artifact.mime_type or "text/plain")


@router.delete(
    "/artifacts/{artifact_id}",
    summary="Delete an artifact",
    description="Removes artifact entry from database (does NOT delete the actual file).",
    tags=["Artifacts"],
    dependencies=[Depends(verify_http_api_key)],
)
async def delete_artifact(artifact_id: str, request: Request) -> dict[str, str]:
    """Delete an artifact entry from the database."""
    db_session_factory = request.app.state.db_session_factory
    if db_session_factory is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    try:
        artifact_uuid = uuid.UUID(artifact_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid artifact ID: {artifact_id}") from None

    async with db_session_factory() as db:
        artifact = await db.get(ArtifactModel, artifact_uuid)
        if artifact is None:
            raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")

        await db.delete(artifact)
        await db.commit()

    # Broadcast deletion
    session_manager: SessionManager = request.app.state.session_manager
    session_manager.broadcast_artifact_deleted(artifact_id)

    return {"message": "Artifact deleted successfully"}
