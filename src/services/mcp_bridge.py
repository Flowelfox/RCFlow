"""MCP agent bridge — expose registry tools to nested coding agents.

Zero-touch contract: everything the bridge serves is derived from the
:class:`~src.tools.registry.ToolRegistry` at call time. Adding a new
agent-exposed tool means dropping a ``tools/*.json`` file with
``"expose_to_agents": true`` — no code changes here, in the executors, in the
HTTP endpoints, or in the stdio proxy. Do not enumerate tool names in this
module.

Two consumers:

- ``ClaudeCodeSdkExecutor`` builds an in-process SDK MCP server from
  :meth:`McpBridge.list_agent_tools` and dispatches through
  :meth:`McpBridge.call_tool`.
- The ``/api/mcp/*`` HTTP endpoints (used by the ``rcflow-mcp`` stdio proxy
  that Codex spawns) authenticate with a per-session token from
  :class:`McpSessionTokenRegistry` and hit the same two methods.
"""

import logging
import secrets
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.core.llm import ToolCallRequest
from src.tools.loader import AGENT_EXECUTORS, ToolDefinition
from src.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from src.core.prompt_router import PromptRouter
    from src.core.session import SessionManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class McpToolSpec:
    """MCP-shaped tool descriptor served to nested agents."""

    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCallOutcome:
    """Result of a bridge-dispatched tool call."""

    text: str
    is_error: bool


class McpSessionTokenRegistry:
    """In-memory per-session MCP tokens.

    Issued when an agent executor spawns, revoked when its session ends.
    Never persisted — a worker restart kills the subprocesses that hold the
    tokens, so they die with the process. Tokens are the sole credential for
    the ``/api/mcp/*`` endpoints; the worker-wide ``RCFLOW_API_KEY`` must
    never be handed to an agent subprocess.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, str] = {}

    def issue(self, session_id: str) -> str:
        """Create and register a token for *session_id*, replacing any prior one."""
        self.revoke_session(session_id)
        token = secrets.token_urlsafe(32)
        self._tokens[token] = session_id
        return token

    def resolve(self, token: str) -> str | None:
        """Return the session id for *token*, or None if unknown/revoked."""
        return self._tokens.get(token)

    def revoke_session(self, session_id: str) -> None:
        """Drop all tokens issued for *session_id*."""
        stale = [t for t, sid in self._tokens.items() if sid == session_id]
        for token in stale:
            del self._tokens[token]


class McpBridge:
    """Registry-driven MCP tool list + dispatch for nested coding agents."""

    def __init__(
        self,
        tool_registry: ToolRegistry,
        session_manager: "SessionManager",
        prompt_router: "PromptRouter",
    ) -> None:
        self._tool_registry = tool_registry
        self._session_manager = session_manager
        self._prompt_router = prompt_router
        self.tokens = McpSessionTokenRegistry()

    def _exposable(self, tool: ToolDefinition) -> bool:
        # Recursion guard is enforced both at load time (loader normalises the
        # flag off) and here, so a hand-constructed definition can't slip through.
        return tool.expose_to_agents and tool.executor not in AGENT_EXECUTORS

    def list_agent_tools(self) -> list[McpToolSpec]:
        """MCP tool list derived live from the registry — never hardcode names."""
        return [
            McpToolSpec(name=tool.name, description=tool.description, input_schema=tool.parameters)
            for tool in self._tool_registry.list_tools()
            if self._exposable(tool)
        ]

    async def call_tool(self, session_id: str, tool_name: str, arguments: dict[str, Any]) -> ToolCallOutcome:
        """Dispatch an agent-originated tool call through the shared one-shot path.

        Errors (unknown session, unexposed tool, executor failure) come back as
        ``is_error=True`` outcomes rather than exceptions so both consumers can
        relay them to the agent as MCP tool errors.
        """
        session = self._session_manager.get_session(session_id)
        if session is None:
            return ToolCallOutcome(text=f"Unknown or ended session: {session_id}", is_error=True)

        tool_def = self._tool_registry.get(tool_name)
        if tool_def is None or not self._exposable(tool_def):
            return ToolCallOutcome(text=f"Tool not available over the agent bridge: {tool_name}", is_error=True)

        # Mutating worktree operations always require explicit user approval,
        # same rule as the LLM tool loop (`_execute_tool`). The bridge is the
        # single gate for both agents — Claude Code's `can_use_tool` waves
        # `mcp__rcflow__*` through so this prompt is never doubled.
        if tool_def.executor == "worktree" and arguments.get("action") != "list":
            if session.permission_manager is None:
                from src.core.permissions import PermissionManager  # noqa: PLC0415

                session.permission_manager = PermissionManager()
            decision = await self._prompt_router._handle_permission_check(session, tool_def.name, dict(arguments))
            if decision.value == "deny":
                return ToolCallOutcome(
                    text=f"Worktree operation '{arguments.get('action')}' denied by user.",
                    is_error=True,
                )

        tool_call = ToolCallRequest(
            tool_use_id=f"mcp-{uuid.uuid4().hex[:12]}",
            tool_name=tool_def.name,
            tool_input=dict(arguments),
        )
        logger.info("MCP bridge call: session=%s tool=%s", session_id, tool_def.name)
        text, is_error = await self._prompt_router.execute_one_shot_tool(session, tool_def, tool_call, origin="agent")
        return ToolCallOutcome(text=text, is_error=is_error)
