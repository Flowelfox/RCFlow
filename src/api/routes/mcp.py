"""HTTP endpoints for the MCP agent bridge.

Consumed by the ``rcflow-mcp`` stdio proxy that Codex spawns as its MCP
server. Authentication uses a per-session token (``X-RCFlow-MCP-Token``)
issued when the agent subprocess starts — deliberately NOT the worker-wide
``X-API-Key``, which must never be handed to an agent subprocess. The token
only unlocks these two endpoints, scoped to the session it was issued for,
and is revoked when that session ends.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from src.services.mcp_bridge import McpBridge

logger = logging.getLogger(__name__)

router = APIRouter(tags=["MCP Bridge"])

_mcp_token_header = APIKeyHeader(
    name="X-RCFlow-MCP-Token",
    auto_error=False,
    description="Per-session MCP bridge token issued at agent spawn.",
)


def _get_bridge(request: Request) -> McpBridge:
    bridge = getattr(request.app.state, "mcp_bridge", None)
    if bridge is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="MCP bridge not initialised")
    return bridge


async def verify_mcp_token(request: Request, token: str | None = Depends(_mcp_token_header)) -> str:
    """Resolve the per-session MCP token to its session id.

    Raises 401 for a missing, unknown, or revoked token.
    """
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing MCP bridge token")
    session_id = _get_bridge(request).tokens.resolve(token)
    if session_id is None:
        logger.warning("Invalid MCP bridge token attempt")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid MCP bridge token")
    return session_id


class McpToolInfo(BaseModel):
    """One MCP-shaped tool descriptor."""

    name: str = Field(description="Tool name as exposed to the agent")
    description: str = Field(description="Tool description")
    inputSchema: dict[str, Any] = Field(description="JSON Schema for the tool arguments")  # noqa: N815 — MCP wire name


class McpToolListResponse(BaseModel):
    """Response for the MCP tool list."""

    tools: list[McpToolInfo]


class McpCallRequest(BaseModel):
    """Request body for an MCP bridge tool call."""

    tool: str = Field(description="Name of the agent-exposed tool to call")
    arguments: dict[str, Any] = Field(default_factory=dict, description="Tool arguments matching its input schema")


class McpCallResponse(BaseModel):
    """Result of an MCP bridge tool call."""

    content: str = Field(description="Aggregated tool output text")
    is_error: bool = Field(description="True when the call failed (unknown tool, executor error, ended session)")


@router.get(
    "/mcp/tools",
    summary="List agent-exposed tools",
    description=(
        "Returns the registry tools exposed to nested coding agents over the MCP bridge, "
        "in MCP tool shape. Authenticated by the per-session `X-RCFlow-MCP-Token` header "
        "(issued at agent spawn), not the worker API key."
    ),
    response_model=McpToolListResponse,
)
async def list_mcp_tools(
    request: Request,
    session_id: str = Depends(verify_mcp_token),
) -> McpToolListResponse:
    """List the tools available to the calling agent session."""
    bridge = _get_bridge(request)
    return McpToolListResponse(
        tools=[
            McpToolInfo(name=spec.name, description=spec.description, inputSchema=spec.input_schema)
            for spec in bridge.list_agent_tools()
        ]
    )


@router.post(
    "/mcp/call",
    summary="Call an agent-exposed tool",
    description=(
        "Dispatches a tool call from a nested coding agent through RCFlow's executor layer. "
        "Failures (unknown tool, ended session, executor error) are returned as `is_error: true` "
        "results rather than HTTP errors so the proxy can relay them as MCP tool errors. "
        "Authenticated by the per-session `X-RCFlow-MCP-Token` header."
    ),
    response_model=McpCallResponse,
)
async def call_mcp_tool(
    request: Request,
    body: McpCallRequest,
    session_id: str = Depends(verify_mcp_token),
) -> McpCallResponse:
    """Execute one agent-originated tool call for the token's session."""
    bridge = _get_bridge(request)
    outcome = await bridge.call_tool(session_id, body.tool, body.arguments)
    return McpCallResponse(content=outcome.text, is_error=outcome.is_error)
