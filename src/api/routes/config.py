from __future__ import annotations

import asyncio
import logging
import platform
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
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
    description=(
        "Returns server metadata including operating system, platform details, and the "
        "current state of both port-forwarding services.  The ``upnp`` object reports "
        "the LAN-router (UPnP-IGD) mapping; the ``natpmp`` object reports the VPN-gateway "
        "(NAT-PMP / RFC 6886) mapping used to escape ISP CGNAT.  Each object's ``status`` "
        "is one of ``disabled``, ``discovering``, ``mapped``, ``failed`` or ``closing``; "
        "clients should surface ``public_ip:external_port`` (NAT-PMP) or "
        "``external_ip:external_port`` (UPnP) when ``status == 'mapped'``."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def server_info(request: Request) -> dict[str, Any]:
    """Return server metadata so clients can display OS and platform info."""
    settings: Settings = request.app.state.settings
    session_manager: SessionManager = request.app.state.session_manager
    llm_client: LLMClient = request.app.state.llm_client

    try:
        worker_version: str | None = _pkg_version("rcflow")
    except PackageNotFoundError:
        worker_version = None

    upnp_service = getattr(request.app.state, "upnp_service", None)
    if upnp_service is not None:
        snap = upnp_service.snapshot()
        upnp_payload: dict[str, Any] = {
            "enabled": True,
            "status": snap.status.value,
            "external_ip": snap.external_ip,
            "external_port": snap.external_port,
            "error": snap.error,
        }
    else:
        upnp_payload = {
            "enabled": settings.UPNP_ENABLED,
            "status": "disabled",
            "external_ip": None,
            "external_port": None,
            "error": None,
        }

    natpmp_service = getattr(request.app.state, "natpmp_service", None)
    if natpmp_service is not None:
        nsnap = natpmp_service.snapshot()
        natpmp_payload: dict[str, Any] = {
            "enabled": True,
            "status": nsnap.status.value,
            "gateway": nsnap.gateway,
            "public_ip": nsnap.public_ip,
            "external_port": nsnap.external_port,
            "internal_port": nsnap.internal_port,
            "error": nsnap.error,
        }
    else:
        natpmp_payload = {
            "enabled": settings.NATPMP_ENABLED,
            "status": "disabled",
            "gateway": None,
            "public_ip": None,
            "external_port": None,
            "internal_port": None,
            "error": None,
        }

    return {
        "os": platform.system(),
        "backend_id": settings.RCFLOW_BACKEND_ID,
        "active_sessions": len(session_manager.list_active_sessions()),
        "version": worker_version,
        # Text files are always accepted, so the attachment button is always
        # available.  Fine-grained per-type support lives in attachment_capabilities.
        "supports_attachments": True,
        "attachment_capabilities": llm_client.attachment_capabilities,
        "upnp": upnp_payload,
        "natpmp": natpmp_payload,
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


_API_KEY_PREFIXES: dict[str, str] = {
    "ANTHROPIC_API_KEY": "sk-ant-",
}


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

    for key, expected_prefix in _API_KEY_PREFIXES.items():
        if key in body.updates:
            value = body.updates[key]
            if value and not str(value).startswith(expected_prefix):
                raise HTTPException(
                    status_code=422,
                    detail=f"{key} does not match the expected format",
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

    new_settings = Settings()
    request.app.state.settings = new_settings

    _reload_components(request, new_settings)
    _invalidate_global_model_cache(request, set(body.updates.keys()))

    logger.info("Config updated: %s", ", ".join(sorted(body.updates.keys())))
    return {"options": get_config_schema(new_settings)}


_GLOBAL_MODEL_CACHE_TRIGGERS: dict[str, tuple[str, ...]] = {
    "ANTHROPIC_API_KEY": ("anthropic", "openrouter"),
    "OPENAI_API_KEY": ("openai",),
    "AWS_ACCESS_KEY_ID": ("bedrock",),
    "AWS_SECRET_ACCESS_KEY": ("bedrock",),
    "AWS_REGION": ("bedrock",),
    "LLM_PROVIDER": ("anthropic", "openai", "bedrock", "openrouter"),
}


def _invalidate_global_model_cache(request: Request, changed_keys: set[str]) -> None:
    """Drop cached model lists for ``scope='global'`` whose creds just changed.

    Each credential update only invalidates the providers it actually
    affects. ``LLM_PROVIDER`` flips invalidate everything global-scoped
    so the UI re-fetches against the newly active provider's key.
    """
    catalog = getattr(request.app.state, "model_catalog", None)
    if catalog is None:
        return
    providers: set[str] = set()
    for key in changed_keys:
        providers.update(_GLOBAL_MODEL_CACHE_TRIGGERS.get(key, ()))
    for provider in providers:
        catalog.invalidate(provider=provider, scope="global")


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
