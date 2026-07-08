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
from src.core.session import SessionStatus
from src.tools.loader import AGENT_EXECUTORS, ToolDefinition
from src.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from src.core.prompt_router import PromptRouter
    from src.core.session import SessionManager

logger = logging.getLogger(__name__)

# Name of the in-process / proxied MCP server RCFlow exposes to nested agents.
# Agents surface its tools as ``mcp__<server>__<tool>``. Single source of truth
# so the SDK server, the stdio proxy, and the Claude Code permission
# short-circuit never drift.
RCFLOW_MCP_SERVER_NAME = "rcflow"
RCFLOW_MCP_TOOL_PREFIX = f"mcp__{RCFLOW_MCP_SERVER_NAME}__"

# Statuses in which a session can no longer run an agent-initiated tool call:
# terminal states plus PAUSED. EXECUTING/ACTIVE/CREATED remain runnable — a
# bridge call normally lands mid-turn while the session is EXECUTING.
_NON_RUNNABLE_STATUSES = frozenset(
    {
        SessionStatus.PAUSED,
        SessionStatus.COMPLETED,
        SessionStatus.FAILED,
        SessionStatus.CANCELLED,
    }
)


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

    @staticmethod
    def _requires_agent_approval(tool_def: ToolDefinition, arguments: dict[str, Any]) -> bool:
        """Whether an agent-initiated call to *tool_def* needs user approval.

        Read-only worktree ``list`` and any tool flagged ``agent_safe`` are
        exempt; everything else (all mutating operations) requires approval.
        """
        if tool_def.agent_safe:
            return False
        if tool_def.executor == "worktree":
            return arguments.get("action") != "list"
        return True

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
        # A token can outlive its agent (orphaned proxy, or an in-flight call
        # landing after cancel/pause). Reject only sessions that are no longer
        # runnable — terminal or paused. A bridge call normally arrives *during*
        # an agent turn, when the session is EXECUTING, so that must stay valid.
        if session.status in _NON_RUNNABLE_STATUSES:
            return ToolCallOutcome(text=f"Session is not active (status: {session.status.value})", is_error=True)

        tool_def = self._tool_registry.get(tool_name)
        if tool_def is None or not self._exposable(tool_def):
            return ToolCallOutcome(text=f"Tool not available over the agent bridge: {tool_name}", is_error=True)

        # The bridge is the single permission gate for agent-initiated tool
        # calls (Claude Code's `can_use_tool` waves `mcp__rcflow__*` through so
        # the prompt is never doubled). Everything that can mutate the host is
        # gated here; only tools explicitly flagged `agent_safe` (read-only
        # demonstrators like system_info) skip it. This mirrors the LLM loop's
        # always-ask rule for worktree ops and closes the gap for any future
        # exposed shell/http tool.
        if self._requires_agent_approval(tool_def, arguments):
            if session.permission_manager is None:
                from src.core.permissions import PermissionManager  # noqa: PLC0415

                session.permission_manager = PermissionManager()
            decision = await self._prompt_router._handle_permission_check(session, tool_def.name, dict(arguments))
            if decision.value == "deny":
                return ToolCallOutcome(text=f"Tool '{tool_def.name}' denied by user.", is_error=True)

        tool_call = ToolCallRequest(
            tool_use_id=f"mcp-{uuid.uuid4().hex[:12]}",
            tool_name=tool_def.name,
            tool_input=dict(arguments),
        )
        logger.info("MCP bridge call: session=%s tool=%s", session_id, tool_def.name)
        text, is_error = await self._prompt_router.execute_one_shot_tool(session, tool_def, tool_call, origin="agent")
        return ToolCallOutcome(text=text, is_error=is_error)
