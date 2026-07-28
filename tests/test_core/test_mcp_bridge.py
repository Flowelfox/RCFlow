"""Tests for the MCP agent bridge (src/core/mcp_bridge.py).

Includes the seamlessness-contract test: a tool definition synthesised at test
time (never registered anywhere in code) must flow through listing and
dispatch untouched — proving the pipeline is registry-driven end to end.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.mcp_bridge import McpBridge, McpSessionTokenRegistry
from src.core.permissions import PermissionDecision
from src.core.session import SessionStatus
from src.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from pathlib import Path


def _active_session(session_id: str = "sess-1") -> MagicMock:
    return MagicMock(id=session_id, status=SessionStatus.ACTIVE)


def _write_tool(
    tools_dir: Path, name: str, *, executor: str = "shell", expose: bool = True, agent_safe: bool = False
) -> None:
    executor_config = {
        "shell": {"command_template": "echo {value}", "stream_output": False},
        "http": {"url_template": "http://example.com/{value}"},
        "worktree": {"default_base_branch": "main"},
        "claude_code": {},
        "codex": {},
        "opencode": {},
    }
    tools_dir.joinpath(f"{name}.json").write_text(
        json.dumps(
            {
                "name": name,
                "description": f"{name} description",
                "session_type": "one-shot",
                "llm_context": "stateless",
                "executor": executor,
                "expose_to_agents": expose,
                "agent_safe": agent_safe,
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
                "executor_config": {executor: executor_config[executor]},
            }
        )
    )


@pytest.fixture
def registry(tmp_path: Path) -> ToolRegistry:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    _write_tool(tools_dir, "synthetic_exposed", agent_safe=True)
    _write_tool(tools_dir, "synthetic_unsafe")  # exposed, needs approval (not agent_safe)
    _write_tool(tools_dir, "synthetic_hidden", expose=False)
    _write_tool(tools_dir, "synthetic_agent", executor="claude_code")
    _write_tool(tools_dir, "synthetic_worktree", executor="worktree")
    registry = ToolRegistry()
    registry.load_from_directory(tools_dir)
    return registry


def _make_bridge(registry: ToolRegistry) -> tuple[McpBridge, MagicMock, MagicMock]:
    session_manager = MagicMock()
    router = MagicMock()
    router.execute_one_shot_tool = AsyncMock(return_value=("tool output", False))
    return McpBridge(registry, session_manager, router), session_manager, router


class TestSeamlessnessContract:
    """Drop a JSON file with expose_to_agents → tool lists + dispatches. No code registration."""

    def test_synthetic_tool_listed_with_verbatim_schema(self, registry: ToolRegistry) -> None:
        bridge, _, _ = _make_bridge(registry)
        specs = {s.name: s for s in bridge.list_agent_tools()}
        assert "synthetic_exposed" in specs
        spec = specs["synthetic_exposed"]
        assert spec.description == "synthetic_exposed description"
        # Schema passed through verbatim — exact object from the JSON file.
        assert spec.input_schema == registry.get("synthetic_exposed").parameters

    @pytest.mark.asyncio
    async def test_synthetic_tool_dispatches(self, registry: ToolRegistry) -> None:
        bridge, session_manager, router = _make_bridge(registry)
        session_manager.get_session.return_value = _active_session()

        outcome = await bridge.call_tool("sess-1", "synthetic_exposed", {"value": "x"})

        assert not outcome.is_error
        assert outcome.text == "tool output"
        (_session, tool_def, tool_call), kwargs = router.execute_one_shot_tool.call_args
        assert tool_def.name == "synthetic_exposed"
        assert tool_call.tool_input == {"value": "x"}
        assert kwargs == {"origin": "agent"}


class TestFiltering:
    def test_unexposed_tool_not_listed(self, registry: ToolRegistry) -> None:
        bridge, _, _ = _make_bridge(registry)
        names = {s.name for s in bridge.list_agent_tools()}
        assert "synthetic_hidden" not in names

    def test_agent_executor_never_listed(self, registry: ToolRegistry) -> None:
        """Recursion guard: expose_to_agents=true on an agent executor is ignored."""
        bridge, _, _ = _make_bridge(registry)
        names = {s.name for s in bridge.list_agent_tools()}
        assert "synthetic_agent" not in names

    @pytest.mark.asyncio
    async def test_call_unexposed_tool_errors(self, registry: ToolRegistry) -> None:
        bridge, session_manager, router = _make_bridge(registry)
        session_manager.get_session.return_value = _active_session()

        for name in ("synthetic_hidden", "synthetic_agent", "no_such_tool"):
            outcome = await bridge.call_tool("sess-1", name, {})
            assert outcome.is_error
            assert name in outcome.text
        router.execute_one_shot_tool.assert_not_called()


class TestCallTool:
    @pytest.mark.asyncio
    async def test_unknown_session_errors(self, registry: ToolRegistry) -> None:
        bridge, session_manager, router = _make_bridge(registry)
        session_manager.get_session.return_value = None

        outcome = await bridge.call_tool("gone", "synthetic_exposed", {})

        assert outcome.is_error
        assert "gone" in outcome.text
        router.execute_one_shot_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_executor_error_propagates(self, registry: ToolRegistry) -> None:
        bridge, session_manager, router = _make_bridge(registry)
        session_manager.get_session.return_value = _active_session()
        router.execute_one_shot_tool = AsyncMock(return_value=("boom", True))

        outcome = await bridge.call_tool("sess-1", "synthetic_exposed", {"value": "x"})

        assert outcome.is_error
        assert outcome.text == "boom"


class TestWorktreeGate:
    """Mutating worktree ops from agents always require user approval; list is exempt."""

    def _setup(self, registry: ToolRegistry, decision: PermissionDecision) -> tuple[McpBridge, MagicMock]:
        bridge, session_manager, router = _make_bridge(registry)
        session_manager.get_session.return_value = _active_session()
        router._handle_permission_check = AsyncMock(return_value=decision)
        return bridge, router

    @pytest.mark.asyncio
    async def test_mutating_action_denied(self, registry: ToolRegistry) -> None:
        bridge, router = self._setup(registry, PermissionDecision.DENY)

        outcome = await bridge.call_tool("sess-1", "synthetic_worktree", {"action": "new", "branch": "feature/x"})

        assert outcome.is_error
        assert "denied" in outcome.text
        router.execute_one_shot_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_mutating_action_approved(self, registry: ToolRegistry) -> None:
        bridge, router = self._setup(registry, PermissionDecision.ALLOW)

        outcome = await bridge.call_tool("sess-1", "synthetic_worktree", {"action": "merge", "name": "x"})

        assert not outcome.is_error
        router._handle_permission_check.assert_awaited_once()
        router.execute_one_shot_tool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_list_action_exempt(self, registry: ToolRegistry) -> None:
        bridge, router = self._setup(registry, PermissionDecision.DENY)

        outcome = await bridge.call_tool("sess-1", "synthetic_worktree", {"action": "list"})

        assert not outcome.is_error
        router._handle_permission_check.assert_not_called()
        router.execute_one_shot_tool.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agent_safe_tool_not_gated(self, registry: ToolRegistry) -> None:
        bridge, router = self._setup(registry, PermissionDecision.DENY)

        outcome = await bridge.call_tool("sess-1", "synthetic_exposed", {"value": "x"})

        assert not outcome.is_error
        router._handle_permission_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsafe_tool_requires_approval(self, registry: ToolRegistry) -> None:
        # Exposed shell tool without agent_safe must be gated (closes the hole
        # where any future non-worktree exposed tool would run promptless).
        bridge, router = self._setup(registry, PermissionDecision.DENY)

        outcome = await bridge.call_tool("sess-1", "synthetic_unsafe", {"value": "x"})

        assert outcome.is_error
        assert "denied" in outcome.text
        router._handle_permission_check.assert_awaited_once()
        router.execute_one_shot_tool.assert_not_called()

    @pytest.mark.asyncio
    async def test_unsafe_tool_approved_dispatches(self, registry: ToolRegistry) -> None:
        bridge, router = self._setup(registry, PermissionDecision.ALLOW)

        outcome = await bridge.call_tool("sess-1", "synthetic_unsafe", {"value": "x"})

        assert not outcome.is_error
        router._handle_permission_check.assert_awaited_once()
        router.execute_one_shot_tool.assert_awaited_once()


class TestSessionStatusGuard:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "status", [SessionStatus.PAUSED, SessionStatus.CANCELLED, SessionStatus.COMPLETED, SessionStatus.FAILED]
    )
    async def test_non_runnable_session_rejected(self, registry: ToolRegistry, status: SessionStatus) -> None:
        bridge, session_manager, router = _make_bridge(registry)
        session_manager.get_session.return_value = MagicMock(id="sess-1", status=status)

        outcome = await bridge.call_tool("sess-1", "synthetic_exposed", {"value": "x"})

        assert outcome.is_error
        assert "not active" in outcome.text
        router.execute_one_shot_tool.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [SessionStatus.ACTIVE, SessionStatus.EXECUTING])
    async def test_runnable_session_allowed(self, registry: ToolRegistry, status: SessionStatus) -> None:
        # A bridge call normally lands mid-turn (EXECUTING) — must not be blocked.
        bridge, session_manager, router = _make_bridge(registry)
        session_manager.get_session.return_value = MagicMock(id="sess-1", status=status)

        outcome = await bridge.call_tool("sess-1", "synthetic_exposed", {"value": "x"})

        assert not outcome.is_error
        router.execute_one_shot_tool.assert_awaited_once()


class TestTokenRegistry:
    def test_issue_resolve_revoke(self) -> None:
        reg = McpSessionTokenRegistry()
        token = reg.issue("sess-1")
        assert reg.resolve(token) == "sess-1"
        reg.revoke_session("sess-1")
        assert reg.resolve(token) is None

    def test_unknown_token(self) -> None:
        assert McpSessionTokenRegistry().resolve("nope") is None

    def test_reissue_replaces_prior_token(self) -> None:
        reg = McpSessionTokenRegistry()
        first = reg.issue("sess-1")
        second = reg.issue("sess-1")
        assert reg.resolve(first) is None
        assert reg.resolve(second) == "sess-1"

    def test_revoke_only_target_session(self) -> None:
        reg = McpSessionTokenRegistry()
        t1 = reg.issue("sess-1")
        t2 = reg.issue("sess-2")
        reg.revoke_session("sess-1")
        assert reg.resolve(t1) is None
        assert reg.resolve(t2) == "sess-2"
