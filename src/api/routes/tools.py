from __future__ import annotations

import json as json_mod
import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.deps import verify_http_api_key
from src.core.agent_auth import agent_configuration_issue

if TYPE_CHECKING:
    from src.config import Settings
    from src.services.tool_manager import ManagedTool, ToolManager
    from src.services.tool_settings import ToolSettingsManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Tools"])


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@router.get(
    "/tools",
    summary="List available tools",
    description=(
        "Returns registered tool definitions that the LLM can use. "
        "Optionally filters by a case-insensitive substring match on the tool name."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def list_tools(
    request: Request,
    q: str | None = Query(None, description="Case-insensitive substring filter for tool names"),
) -> dict[str, Any]:
    tool_registry = request.app.state.tool_registry
    all_tools = tool_registry.list_tools()

    if q:
        q_lower = q.lower()
        all_tools = [
            t
            for t in all_tools
            if q_lower in t.name.lower()
            or q_lower in t.mention_name.lower()
            or q_lower in (t.display_name or "").lower()
        ]

    tools = [
        {
            "name": t.name,
            "mention_name": t.mention_name,
            "display_name": t.display_name or t.name,
            "description": t.description,
            "version": t.version,
            "session_type": t.session_type,
            "executor": t.executor,
        }
        for t in sorted(all_tools, key=lambda t: t.name)
    ]
    return {"tools": tools}


@router.get(
    "/tools/status",
    summary="Get managed tool status",
    description=(
        "Returns installation status, versions, and update availability for managed CLI tools (Claude Code, Codex)."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def get_tool_status(request: Request) -> dict[str, Any]:
    """Return installation status, current/latest versions, and update availability."""
    tool_manager: ToolManager = request.app.state.tool_manager
    tools = await tool_manager.check_updates()
    return {"tools": {name: _tool_dict(tool) for name, tool in tools.items()}}


def _tool_dict(tool: ManagedTool) -> dict[str, Any]:
    """Serialise a single ManagedTool to a JSON-friendly dict."""
    return {
        "installed": tool.binary_path is not None,
        "managed": tool.managed,
        "binary_path": tool.binary_path,
        "current_version": tool.current_version,
        "latest_version": tool.latest_version,
        "update_available": (
            tool.current_version is not None
            and tool.latest_version is not None
            and tool.current_version != tool.latest_version
        ),
        "error": tool.error,
        "managed_path": tool.managed_path,
        "external_path": tool.external_path,
    }


@router.get(
    "/tools/auth/preflight",
    summary="Coding-agent auth-readiness preflight",
    description=(
        "Returns per-agent auth readiness so the client can warn the user before they "
        "send a prompt that would otherwise hang on a CLI login prompt. Each agent "
        "(``claude_code``, ``codex``, ``opencode``) reports ``ready: bool`` and an "
        "``issue`` string when not ready. OAuth flows (Anthropic Login, ChatGPT) and "
        "OpenCode global mode are reported as ``ready: true`` because their state "
        "lives in the CLI's own credential store and cannot be inspected synchronously."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def coding_agent_auth_preflight(request: Request) -> dict[str, Any]:
    """Report per-agent auth readiness for the client to surface as a warning."""
    settings: Settings = request.app.state.settings
    tool_settings: ToolSettingsManager = request.app.state.tool_settings
    tool_manager: ToolManager = request.app.state.tool_manager
    out: dict[str, Any] = {}
    for agent in ("claude_code", "codex", "opencode"):
        issue = agent_configuration_issue(agent, settings, tool_settings, tool_manager)
        out[agent] = {"ready": issue is None, "issue": issue}
    return {"agents": out}


@router.post(
    "/tools/update",
    summary="Trigger tool updates",
    description=(
        "Checks for updates to managed CLI tools and installs them if available. "
        "Only updates tools that RCFlow manages (not user-installed ones)."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def trigger_tool_update(request: Request) -> dict[str, Any]:
    """Trigger an update check and install for all RCFlow-managed tools."""
    tool_manager: ToolManager = request.app.state.tool_manager
    results = await tool_manager.update_all()
    return {"tools": {name: _tool_dict(tool) for name, tool in results.items()}}


@router.post(
    "/tools/update/{tool_name}",
    summary="Update a single tool",
    description=(
        "Updates a specific managed CLI tool by name (e.g. claude_code, codex). "
        "Streams NDJSON progress events. Final event has step='complete' with updated tool data."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def trigger_single_tool_update(request: Request, tool_name: str) -> StreamingResponse:
    """Update a single tool with streaming progress."""
    tool_manager: ToolManager = request.app.state.tool_manager
    if tool_name not in tool_manager.tool_names:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")

    async def _stream():
        try:
            async for event in tool_manager.update_tool_streaming(tool_name):
                yield json_mod.dumps(event) + "\n"
            # Re-detect to refresh paths
            tool = await tool_manager.detect_tool(tool_name)
            tool_manager._tools[tool_name] = tool
            yield json_mod.dumps({"step": "complete", "tool": _tool_dict(tool)}) + "\n"
        except Exception:
            logger.exception("Update failed for '%s'", tool_name)
            yield json_mod.dumps({"step": "error", "message": "Update failed — see server logs"}) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@router.post(
    "/tools/{tool_name}/install",
    summary="Install managed version of a tool",
    description=(
        "Downloads and installs the RCFlow-managed version of a CLI tool. "
        "Streams NDJSON progress events. Final event has step='done'."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def install_managed_tool(request: Request, tool_name: str) -> StreamingResponse:
    """Install the managed version of a tool with streaming progress."""
    tool_manager: ToolManager = request.app.state.tool_manager
    if tool_name not in tool_manager.tool_names:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")

    async def _stream():
        try:
            async for event in tool_manager.install_tool_streaming(tool_name):
                yield json_mod.dumps(event) + "\n"
            # Re-detect after install so managed_path/external_path are populated
            tool = await tool_manager.detect_tool(tool_name)
            tool_manager._tools[tool_name] = tool
            yield json_mod.dumps({"step": "complete", "tool": _tool_dict(tool)}) + "\n"
        except Exception:
            logger.exception("Install failed for '%s'", tool_name)
            yield json_mod.dumps({"step": "error", "message": "Install failed — see server logs"}) + "\n"

    return StreamingResponse(_stream(), media_type="application/x-ndjson")


@router.delete(
    "/tools/{tool_name}/install",
    summary="Uninstall managed version of a tool",
    description=(
        "Removes the managed binary and version file but preserves tool settings. "
        "If an external binary exists on PATH the tool falls back to it."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def uninstall_managed_tool(request: Request, tool_name: str) -> dict[str, Any]:
    """Uninstall the managed version of a tool, preserving settings."""
    tool_manager: ToolManager = request.app.state.tool_manager
    if tool_name not in tool_manager.tool_names:
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
    try:
        tool = await tool_manager.uninstall_tool(tool_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None
    return {"tool": _tool_dict(tool)}


def _is_tool_installed(tool_manager: ToolManager, tool_name: str) -> bool:
    """Whether the managed binary for *tool_name* exists on disk."""
    tool = tool_manager._tools.get(tool_name)
    return bool(tool and tool.binary_path)


# Per-tool keys that should invalidate the model-catalog cache when changed.
# When any of these are part of a settings PATCH, the matching scope is
# fully evicted so the UI re-fetches against the new credential next time.
_TOOL_MODEL_CACHE_TRIGGERS: dict[str, frozenset[str]] = {
    "claude_code": frozenset(
        {"provider", "anthropic_api_key", "aws_region", "aws_access_key_id", "aws_secret_access_key"}
    ),
    "codex": frozenset({"provider", "codex_api_key"}),
    "opencode": frozenset({"provider", "opencode_api_key", "openai_api_key"}),
}


def _invalidate_tool_model_cache(request: Request, tool_name: str, changed_keys: set[str]) -> None:
    """Drop cached model lists scoped to *tool_name* when creds change."""
    triggers = _TOOL_MODEL_CACHE_TRIGGERS.get(tool_name)
    if not triggers or not changed_keys & triggers:
        return
    catalog = getattr(request.app.state, "model_catalog", None)
    if catalog is None:
        return
    catalog.invalidate(scope=tool_name)


class UpdateToolSettingsRequest(BaseModel):
    """Body for the PATCH /api/tools/{tool_name}/settings endpoint."""

    updates: dict[str, Any]


@router.get(
    "/tools/{tool_name}/settings",
    summary="Get per-tool settings",
    description=(
        "Returns the settings schema and current values for a managed CLI tool. "
        "Each field includes key, label, type, current value, default, and description. "
        "When the tool is not installed yet, ``fields`` is empty and ``installed`` is "
        "false so the client can prompt for installation instead of rendering a form."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def get_tool_settings(request: Request, tool_name: str) -> dict[str, Any]:
    """Return settings schema with current values for a tool."""
    tool_settings: ToolSettingsManager = request.app.state.tool_settings
    tool_manager: ToolManager = request.app.state.tool_manager
    installed = _is_tool_installed(tool_manager, tool_name)
    if not installed:
        # Hide all configurable fields when the binary is not on disk — there is
        # nothing useful to set until the user installs the tool.  The schema
        # itself is still validated so a 404 surfaces an unknown tool name.
        if tool_name not in {"claude_code", "codex", "opencode"}:
            raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
        return {"tool": tool_name, "fields": [], "installed": False}
    try:
        result = tool_settings.get_settings_with_schema(tool_name, managed=True)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from None
    result["installed"] = True
    return result


@router.patch(
    "/tools/{tool_name}/settings",
    summary="Update per-tool settings",
    description=(
        "Apply partial updates to a managed CLI tool's settings. "
        'Body: {"updates": {"key": value, ...}}. Returns the updated schema+values. '
        "Rejected with 409 when the tool is not installed."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def update_tool_settings(request: Request, tool_name: str, body: UpdateToolSettingsRequest) -> dict[str, Any]:
    """Apply partial settings updates for a tool."""
    tool_settings: ToolSettingsManager = request.app.state.tool_settings
    tool_manager: ToolManager = request.app.state.tool_manager
    if not _is_tool_installed(tool_manager, tool_name):
        raise HTTPException(
            status_code=409,
            detail=f"{tool_name} is not installed. Install it before configuring.",
        )
    try:
        result = tool_settings.update_settings(tool_name, body.updates, managed=True)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    result["installed"] = True
    _invalidate_tool_model_cache(request, tool_name, set(body.updates.keys()))
    logger.info("Tool settings updated for '%s': %s", tool_name, list(body.updates.keys()))
    return result
