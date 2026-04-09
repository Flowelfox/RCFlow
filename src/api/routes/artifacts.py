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

_MAX_GLOB_LEN = 200
_BLOCKED_GLOB_PARTS = frozenset({"..", "~"})


def _validate_glob_pattern(pattern: str, field: str) -> None:
    """Raise HTTPException if *pattern* contains path-traversal components."""
    if len(pattern) > _MAX_GLOB_LEN:
        raise HTTPException(status_code=422, detail=f"{field}: pattern too long (max {_MAX_GLOB_LEN} chars)")
    # Reject absolute paths
    if pattern.startswith("/") or (len(pattern) >= 2 and pattern[1] == ":"):
        raise HTTPException(status_code=422, detail=f"{field}: absolute paths are not allowed")
    # Reject traversal sequences and home-dir expansion
    parts = pattern.replace("\\", "/").split("/")
    if any(p in _BLOCKED_GLOB_PARTS for p in parts):
        raise HTTPException(status_code=422, detail=f"{field}: path traversal sequences are not allowed")


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
        "file_exists": artifact.file_exists,
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
    if body.include_pattern is not None:
        _validate_glob_pattern(body.include_pattern, "include_pattern")
    if body.exclude_pattern is not None:
        _validate_glob_pattern(body.exclude_pattern, "exclude_pattern")

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
        settings = Settings()
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


@router.post(
    "/artifacts/recheck",
    summary="Recheck artifact file existence",
    description=(
        "Validates whether each saved artifact file still exists on disk. "
        "Updates the file_exists flag for all artifacts and broadcasts the refreshed list to connected clients."
    ),
    tags=["Artifacts"],
    dependencies=[Depends(verify_http_api_key)],
)
async def recheck_artifacts(request: Request) -> dict[str, Any]:
    """Check disk existence for all artifacts and update the file_exists flag."""
    settings: Settings = request.app.state.settings
    db_session_factory = request.app.state.db_session_factory
    if db_session_factory is None:
        return {"checked": 0, "missing": 0}

    checked = 0
    missing = 0
    async with db_session_factory() as db:
        stmt = select(ArtifactModel).where(ArtifactModel.backend_id == settings.RCFLOW_BACKEND_ID)
        result = await db.execute(stmt)
        artifacts = result.scalars().all()
        for artifact in artifacts:
            exists = Path(artifact.file_path).exists()
            if artifact.file_exists != exists:
                artifact.file_exists = exists
            checked += 1
            if not exists:
                missing += 1
        await db.commit()

    # Broadcast refreshed list so all clients update immediately
    session_manager: SessionManager = request.app.state.session_manager
    async with db_session_factory() as db:
        stmt = (
            select(ArtifactModel)
            .where(ArtifactModel.backend_id == settings.RCFLOW_BACKEND_ID)
            .order_by(ArtifactModel.discovered_at.desc())
        )
        result = await db.execute(stmt)
        projects_dirs = settings.projects_dirs
        from src.core.prompt_router import PromptRouter  # noqa: PLC0415

        artifacts_out = [
            {
                "artifact_id": str(a.id),
                "file_path": a.file_path,
                "file_name": a.file_name,
                "file_extension": a.file_extension,
                "file_size": a.file_size,
                "mime_type": a.mime_type,
                "file_exists": a.file_exists,
                "discovered_at": a.discovered_at.isoformat() if a.discovered_at else "",
                "modified_at": a.modified_at.isoformat() if a.modified_at else "",
                "session_id": str(a.session_id) if a.session_id else None,
                "project_name": PromptRouter._resolve_artifact_project(a.file_path, projects_dirs),
            }
            for a in result.scalars()
        ]
    session_manager.broadcast_artifact_list(artifacts_out)

    logger.info("Artifact recheck: %d checked, %d missing", checked, missing)
    return {"checked": checked, "missing": missing}


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
