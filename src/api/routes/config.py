from __future__ import annotations

import asyncio
import logging
import platform
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from src.api.deps import verify_http_api_key
from src.config import CONFIGURABLE_KEYS, Settings, get_config_schema, update_settings_file
from src.core.llm import LLMClient

if TYPE_CHECKING:
    from src.core.session import SessionManager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/health",
    summary="Health check",
    description="Returns server status. Does not require authentication.",
)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/info",
    summary="Server information",
    description="Returns server metadata including operating system and platform details.",
    dependencies=[Depends(verify_http_api_key)],
)
async def server_info(request: Request) -> dict[str, Any]:
    """Return server metadata so clients can display OS and platform info."""
    settings: Settings = request.app.state.settings
    session_manager: SessionManager = request.app.state.session_manager
    llm_client: LLMClient = request.app.state.llm_client

    return {
        "os": platform.system(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "hostname": platform.node(),
        "backend_id": settings.RCFLOW_BACKEND_ID,
        "active_sessions": len(session_manager.list_active_sessions()),
        # Text files are always accepted, so the attachment button is always
        # available.  Fine-grained per-type support lives in attachment_capabilities.
        "supports_attachments": True,
        "attachment_capabilities": llm_client.attachment_capabilities,
    }


@router.get(
    "/projects",
    summary="List project directories",
    description=(
        "Returns project entries from all configured project directories. "
        "Each entry includes the directory ``name`` and its absolute ``path``. "
        "Optionally filters by a case-insensitive substring match on the name."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def list_projects(
    request: Request,
    q: str | None = Query(None, description="Case-insensitive substring filter for project names"),
) -> dict[str, list[dict[str, str]]]:
    settings: Settings = request.app.state.settings
    # Use a dict keyed by name so duplicate names across multiple projects_dirs
    # only appear once (first match wins, consistent with path resolution order).
    entries: dict[str, str] = {}

    for projects_dir in settings.projects_dirs:
        if not projects_dir.is_dir():
            continue
        for entry in projects_dir.iterdir():
            if entry.is_dir() and not entry.name.startswith(".") and entry.name not in entries:
                entries[entry.name] = str(entry)

    projects = sorted(entries.items(), key=lambda x: x[0])

    if q:
        q_lower = q.lower()
        projects = [(n, p) for n, p in projects if q_lower in n.lower()]

    return {"projects": [{"name": n, "path": p} for n, p in projects]}


@router.get(
    "/config",
    summary="Get server configuration",
    description=(
        "Returns all configurable server options with their current values, types, "
        "and available choices. Secret values (API keys) are masked. Options are "
        "grouped into logical sections (LLM, Executors, Paths, Logging)."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def get_config(request: Request) -> dict[str, Any]:
    """Return the server configuration schema with current values."""
    settings: Settings = request.app.state.settings
    return {"options": get_config_schema(settings)}


class UpdateConfigRequest(BaseModel):
    """Body for the PATCH /api/config endpoint.

    Keys are setting names (e.g. ``LLM_PROVIDER``), values are the new
    string values to apply.
    """

    updates: dict[str, Any]


@router.patch(
    "/config",
    summary="Update server configuration",
    description=(
        "Accepts partial config updates. Validates keys against the configurable "
        "set, persists changes to settings.json, reloads settings, and hot-reloads "
        "affected components (LLM client). Returns the updated "
        "config schema."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def update_config(body: UpdateConfigRequest, request: Request) -> dict[str, Any]:
    """Apply partial config updates and return the refreshed config schema."""
    invalid_keys = set(body.updates.keys()) - CONFIGURABLE_KEYS
    if invalid_keys:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown or non-configurable keys: {', '.join(sorted(invalid_keys))}",
        )

    env_updates: dict[str, str] = {}
    for key, value in body.updates.items():
        if isinstance(value, list):
            env_updates[key] = ", ".join(str(v) for v in value)
        elif isinstance(value, bool):
            env_updates[key] = "true" if value else "false"
        else:
            env_updates[key] = str(value)

    update_settings_file(env_updates)

    new_settings = Settings()  # type: ignore[call-arg]
    request.app.state.settings = new_settings

    _reload_components(request, new_settings)

    logger.info("Config updated: %s", ", ".join(sorted(body.updates.keys())))
    return {"options": get_config_schema(new_settings)}


def _reload_components(request: Request, settings: Settings) -> None:
    """Hot-reload server components that depend on settings.

    Recreates the LLM client from the new settings and patches the prompt
    router to use it.
    """
    tool_registry = request.app.state.tool_registry

    old_llm: LLMClient = request.app.state.llm_client
    new_llm = LLMClient(settings, tool_registry)
    request.app.state.llm_client = new_llm

    prompt_router = request.app.state.prompt_router
    prompt_router._llm = new_llm
    prompt_router._settings = settings

    if old_llm is not None:
        asyncio.get_event_loop().create_task(old_llm.close())
