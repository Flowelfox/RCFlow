"""Interactive permission approval for Claude Code / Codex tool sessions.

When a session is configured with ``interactive`` permission mode, the
:class:`PermissionManager` intercepts tool-use events from the subprocess,
asks the user for approval via the WebSocket output channel, and caches
decisions so repeated questions are suppressed.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PermissionDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"


class PermissionScope(StrEnum):
    ONCE = "once"  # Single request only
    TOOL_SESSION = "tool_session"  # All uses of this tool in this session
    TOOL_PATH = "tool_path"  # This tool + files under a path prefix
    ALL_SESSION = "all_session"  # All tools in this session


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PermissionRule:
    """A persisted permission decision."""

    tool_name: str
    decision: PermissionDecision
    scope: PermissionScope
    path_prefix: str | None = None
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class PendingPermission:
    """A permission request waiting for user response."""

    request_id: str
    tool_name: str
    tool_input: dict[str, Any]
    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: PermissionDecision | None = None
    scope: PermissionScope = PermissionScope.ONCE
    timed_out: bool = False


# ---------------------------------------------------------------------------
# Risk classification
# ---------------------------------------------------------------------------

TOOL_RISK_LEVELS: dict[str, str] = {
    # Read-only tools — low risk
    "Read": "low",
    "Glob": "low",
    "Grep": "low",
    "WebFetch": "low",
    "ListMcpResourcesTool": "low",
    "ReadMcpResourceTool": "low",
    # Write tools — medium risk
    "Write": "medium",
    "Edit": "medium",
    "NotebookEdit": "medium",
    # Execution tools — high risk
    "Bash": "high",
    "Agent": "medium",
    # Default for unknown tools
    "_default": "medium",
}

_DESTRUCTIVE_PATTERNS: tuple[str, ...] = (
    "rm ",
    "rm\t",
    "rmdir",
    "dd ",
    "mkfs",
    "> /dev/",
    "chmod",
    "chown",
    "kill ",
    "pkill",
    "shutdown",
    "reboot",
    "git push --force",
    "git push -f",
    "git reset --hard",
    "git clean -f",
)

_SENSITIVE_PREFIXES: tuple[str, ...] = (
    "/etc/",
    "/usr/",
    "/bin/",
    "/sbin/",
    "/boot/",
)


def classify_risk(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Classify the risk level of a tool invocation.

    Returns one of ``"low"``, ``"medium"``, ``"high"``, or ``"critical"``.
    """
    base_risk = TOOL_RISK_LEVELS.get(tool_name, TOOL_RISK_LEVELS["_default"])

    # Elevate risk for destructive Bash commands
    if tool_name == "Bash" and base_risk == "high":
        command = tool_input.get("command", "")
        if any(p in command for p in _DESTRUCTIVE_PATTERNS):
            return "critical"

    # Elevate for writes outside project directory
    if tool_name in ("Write", "Edit") and base_risk == "medium":
        path = tool_input.get("file_path", "")
        if any(path.startswith(p) for p in _SENSITIVE_PREFIXES):
            return "high"

    return base_risk


def describe_tool_action(tool_name: str, tool_input: dict[str, Any]) -> str:
    """Generate a human-readable description of what a tool wants to do."""
    match tool_name:
        case "Bash":
            cmd = tool_input.get("command", "")
            return f"Execute command: {cmd[:200]}"
        case "Read":
            return f"Read file: {tool_input.get('file_path', '')}"
        case "Write":
            return f"Write file: {tool_input.get('file_path', '')}"
        case "Edit":
            return f"Edit file: {tool_input.get('file_path', '')}"
        case "Glob":
            return f"Search for files matching: {tool_input.get('pattern', '')}"
        case "Grep":
            return f"Search file contents for: {tool_input.get('pattern', '')}"
        case "Agent":
            return f"Launch sub-agent: {tool_input.get('description', '')}"
        case "WebFetch":
            return f"Fetch URL: {tool_input.get('url', '')}"
        case "NotebookEdit":
            return f"Edit notebook: {tool_input.get('notebook_path', '')}"
        case _:
            return f"Use tool: {tool_name}"


def get_scope_options(tool_name: str) -> list[str]:
    """Return available permission scopes for a tool type."""
    base = ["once", "tool_session", "all_session"]
    if tool_name in ("Read", "Write", "Edit", "Glob", "Grep"):
        base.insert(2, "tool_path")  # Path-scoped option for file tools
    return base


# ---------------------------------------------------------------------------
# PermissionManager
# ---------------------------------------------------------------------------


class PermissionManager:
    """Manages permission rules and pending requests for a single session.

    Each :class:`~src.core.session.ActiveSession` that uses interactive
    permissions gets its own ``PermissionManager`` instance.
    """

    DEFAULT_TIMEOUT: float = 120.0  # seconds

    def __init__(self) -> None:
        self._rules: list[PermissionRule] = []
        self._pending: dict[str, PendingPermission] = {}
        self._blanket_allow: bool = False
        self._blanket_deny: bool = False

    @property
    def has_pending(self) -> bool:
        """True if any permission requests are waiting for a response."""
        return bool(self._pending)

    def check_cached(self, tool_name: str, tool_input: dict[str, Any]) -> PermissionDecision | None:
        """Check if a cached rule covers this request.

        Returns ``None`` if no matching rule is found.
        """
        if self._blanket_allow:
            return PermissionDecision.ALLOW
        if self._blanket_deny:
            return PermissionDecision.DENY

        for rule in reversed(self._rules):  # Most recent first
            if rule.scope == PermissionScope.TOOL_SESSION and rule.tool_name == tool_name:
                return rule.decision
            if rule.scope == PermissionScope.TOOL_PATH and rule.tool_name == tool_name:
                path = _extract_path(tool_name, tool_input)
                if path and rule.path_prefix and path.startswith(rule.path_prefix):
                    return rule.decision

        return None

    def create_request(self, tool_name: str, tool_input: dict[str, Any]) -> PendingPermission:
        """Create a new pending permission request."""
        request_id = str(uuid.uuid4())
        pending = PendingPermission(
            request_id=request_id,
            tool_name=tool_name,
            tool_input=tool_input,
        )
        self._pending[request_id] = pending
        return pending

    def resolve_request(
        self,
        request_id: str,
        decision: PermissionDecision,
        scope: PermissionScope,
        path_prefix: str | None = None,
    ) -> bool:
        """Resolve a pending permission request and optionally store a rule.

        Returns True if the request was found and resolved, False otherwise.
        """
        pending = self._pending.get(request_id)
        if pending is None:
            return False

        pending.decision = decision
        pending.scope = scope

        # Store the rule if scope is broader than ONCE
        if scope == PermissionScope.ALL_SESSION:
            if decision == PermissionDecision.ALLOW:
                self._blanket_allow = True
            else:
                self._blanket_deny = True
        elif scope != PermissionScope.ONCE:
            self._rules.append(
                PermissionRule(
                    tool_name=pending.tool_name,
                    decision=decision,
                    scope=scope,
                    path_prefix=path_prefix,
                )
            )

        # Signal the waiting coroutine
        pending.event.set()
        return True

    async def wait_for_response(self, request_id: str, timeout: float | None = None) -> PendingPermission:
        """Wait for a permission response.

        Returns the resolved :class:`PendingPermission`.  If the timeout
        expires before the user responds, the request is auto-denied.
        """
        pending = self._pending.get(request_id)
        if pending is None:
            raise ValueError(f"Unknown request: {request_id}")

        effective_timeout = timeout or self.DEFAULT_TIMEOUT
        try:
            await asyncio.wait_for(pending.event.wait(), timeout=effective_timeout)
        except TimeoutError:
            pending.timed_out = True
            pending.decision = PermissionDecision.DENY

        # Clean up
        self._pending.pop(request_id, None)
        return pending

    def cancel_all_pending(self) -> None:
        """Auto-deny and signal all pending permission requests.

        Called when the session is paused, cancelled, or the subprocess dies.
        """
        for pending in self._pending.values():
            if pending.decision is None:
                pending.decision = PermissionDecision.DENY
                pending.timed_out = True
            pending.event.set()
        self._pending.clear()

    def get_rules_snapshot(self) -> list[dict[str, Any]]:
        """Return current rules for serialization (e.g., archiving to DB)."""
        result: list[dict[str, Any]] = [
            {
                "tool_name": r.tool_name,
                "decision": r.decision.value,
                "scope": r.scope.value,
                "path_prefix": r.path_prefix,
            }
            for r in self._rules
        ]
        if self._blanket_allow:
            result.append({"tool_name": "*", "decision": "allow", "scope": "all_session", "path_prefix": None})
        elif self._blanket_deny:
            result.append({"tool_name": "*", "decision": "deny", "scope": "all_session", "path_prefix": None})
        return result

    def restore_rules(self, rules: list[dict[str, Any]]) -> None:
        """Restore rules from a serialized snapshot (e.g., on session restore)."""
        for rule_data in rules:
            tool_name = rule_data.get("tool_name", "")
            decision = PermissionDecision(rule_data["decision"])
            scope = PermissionScope(rule_data["scope"])

            if tool_name == "*" and scope == PermissionScope.ALL_SESSION:
                if decision == PermissionDecision.ALLOW:
                    self._blanket_allow = True
                else:
                    self._blanket_deny = True
            else:
                self._rules.append(
                    PermissionRule(
                        tool_name=tool_name,
                        decision=decision,
                        scope=scope,
                        path_prefix=rule_data.get("path_prefix"),
                    )
                )


def _extract_path(tool_name: str, tool_input: dict[str, Any]) -> str | None:
    """Extract a file/directory path from tool input for path-scoped rules."""
    if tool_name in ("Read", "Glob", "Grep"):
        return tool_input.get("path") or tool_input.get("file_path")
    if tool_name in ("Write", "Edit"):
        return tool_input.get("file_path")
    if tool_name == "NotebookEdit":
        return tool_input.get("notebook_path")
    return None
