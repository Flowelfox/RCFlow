"""``GET /api/models`` — dynamic LLM model catalog endpoint.

Returns the live (or cached, or fallback) list of models for a given
provider/scope so the client's ``model_select`` widgets can populate
themselves at runtime instead of relying on a hardcoded list.

Credentials are looked up server-side based on the requested *scope*:

* ``global`` — uses the worker's :class:`~src.config.Settings` (top-level
  ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` / ``AWS_*``).
* ``claude_code`` / ``codex`` / ``opencode`` — uses the per-tool settings
  managed by :class:`~src.services.tool_settings.ToolSettingsManager`,
  so each managed CLI can use a different key than the global LLM.

Fetch failures *do not* return 5xx — they return ``200`` with
``source='fallback'`` and ``error`` populated so the UI can render a
status badge ("offline (fallback)") without hiding the dropdown.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from src.api.deps import verify_http_api_key
from src.services.model_catalog import Credentials, ModelCatalog

if TYPE_CHECKING:
    from src.config import Settings
    from src.services.tool_settings import ToolSettingsManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Models"])


_VALID_PROVIDERS: set[str] = {"anthropic", "openai", "bedrock", "openrouter"}
_VALID_SCOPES: set[str] = {"global", "claude_code", "codex", "opencode"}


def _global_credentials(settings: Settings, provider: str) -> Credentials:
    """Build :class:`Credentials` from worker-level settings for *provider*."""
    if provider == "anthropic" or provider == "openrouter":
        return Credentials(api_key=settings.ANTHROPIC_API_KEY or None)
    if provider == "openai":
        return Credentials(api_key=settings.OPENAI_API_KEY or None)
    if provider == "bedrock":
        return Credentials(
            aws_region=settings.AWS_REGION or None,
            aws_access_key=settings.AWS_ACCESS_KEY_ID or None,
            aws_secret_key=settings.AWS_SECRET_ACCESS_KEY or None,
        )
    raise ValueError(f"Unsupported provider: {provider!r}")


def _tool_credentials(tool_settings: ToolSettingsManager, scope: str, provider: str) -> Credentials:
    """Build :class:`Credentials` from per-tool settings.

    The *scope* picks the tool, *provider* selects which key field to
    pull. Each managed CLI stores its own keys (see
    :data:`~src.services.tool_settings.CLAUDE_CODE_SETTINGS_SCHEMA`,
    ``CODEX_SETTINGS_SCHEMA``, ``OPENCODE_SETTINGS_SCHEMA``).
    """
    settings = tool_settings.get_settings(scope)
    if scope == "claude_code":
        if provider == "anthropic":
            return Credentials(api_key=settings.get("anthropic_api_key") or None)
        if provider == "bedrock":
            return Credentials(
                aws_region=settings.get("aws_region") or None,
                aws_access_key=settings.get("aws_access_key_id") or None,
                aws_secret_key=settings.get("aws_secret_access_key") or None,
            )
    if scope == "codex" and provider == "openai":
        return Credentials(api_key=settings.get("codex_api_key") or None)
    if scope == "opencode":
        if provider == "openrouter":
            # OpenRouter listing is unauthenticated — but pass the saved key
            # if one exists so the cache fingerprints differ per user.
            return Credentials(api_key=settings.get("opencode_api_key") or None)
        if provider == "anthropic":
            return Credentials(api_key=settings.get("opencode_api_key") or None)
        if provider == "openai":
            return Credentials(api_key=settings.get("openai_api_key") or None)
    raise HTTPException(
        status_code=422,
        detail=f"Provider {provider!r} not supported for scope {scope!r}",
    )


@router.get(
    "/models",
    summary="List models for a provider",
    description=(
        "Returns the dynamic list of LLM models the client should show in "
        "``model_select`` dropdowns for the given *provider* and *scope*. "
        "Results are TTL-cached on the worker; pass ``refresh=true`` to "
        "bypass the cache. On upstream failure the response is still 200 "
        "with ``source='fallback'`` and ``error`` populated."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def list_models(
    request: Request,
    provider: Annotated[
        str,
        Query(description="Upstream provider: anthropic, openai, bedrock, openrouter"),
    ],
    scope: Annotated[
        str,
        Query(description="Credential scope: global (server settings) or a tool name"),
    ] = "global",
    refresh: Annotated[
        bool,
        Query(description="Bypass cache and force a re-fetch"),
    ] = False,
) -> dict[str, Any]:
    if provider not in _VALID_PROVIDERS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown provider {provider!r}. Allowed: {sorted(_VALID_PROVIDERS)}",
        )
    if scope not in _VALID_SCOPES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown scope {scope!r}. Allowed: {sorted(_VALID_SCOPES)}",
        )

    catalog: ModelCatalog = request.app.state.model_catalog

    if scope == "global":
        settings: Settings = request.app.state.settings
        credentials = _global_credentials(settings, provider)
    else:
        tool_settings: ToolSettingsManager = request.app.state.tool_settings
        credentials = _tool_credentials(tool_settings, scope, provider)

    result = await catalog.get(provider, scope, credentials, force_refresh=refresh)
    payload = result.to_dict()
    payload["provider"] = provider
    payload["scope"] = scope
    payload["allow_custom"] = True
    return payload
