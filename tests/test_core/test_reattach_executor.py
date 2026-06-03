"""Tests for lazy crash-resume executor reconstruction (`reattach_executor`)."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.core.agent_claude_code import ClaudeCodeAgent


def _make_agent(tool_def):
    agent = ClaudeCodeAgent.__new__(ClaudeCodeAgent)
    agent._r = MagicMock()
    agent._r._tool_registry.get.return_value = tool_def
    return agent


def _cc_tool_def():
    td = MagicMock()
    td.executor = "claude_code"
    return td


def _session(metadata, executor=None):
    s = MagicMock()
    s.claude_code_executor = executor
    s.metadata = metadata
    return s


class TestReattachExecutor:
    def test_reconstructs_from_metadata(self):
        td = _cc_tool_def()
        agent = _make_agent(td)
        sentinel = MagicMock()
        agent.build_session_executor = MagicMock(return_value=sentinel)
        session = _session({"claude_code_session_id": "cc1", "claude_code_tool_name": "claude_code"})

        assert agent.reattach_executor(session) is True
        assert session.claude_code_executor is sentinel
        agent.build_session_executor.assert_called_once_with(td, session, "cc1")

    def test_noop_when_executor_already_present(self):
        agent = _make_agent(_cc_tool_def())
        agent.build_session_executor = MagicMock()
        existing = MagicMock()
        session = _session({"claude_code_session_id": "cc1", "claude_code_tool_name": "claude_code"}, executor=existing)

        assert agent.reattach_executor(session) is True
        assert session.claude_code_executor is existing
        agent.build_session_executor.assert_not_called()

    def test_false_without_cc_metadata(self):
        agent = _make_agent(_cc_tool_def())
        agent.build_session_executor = MagicMock()
        session = _session({})  # no claude_code_session_id / tool_name

        assert agent.reattach_executor(session) is False
        agent.build_session_executor.assert_not_called()

    def test_false_when_tool_def_missing_or_not_cc(self):
        agent = _make_agent(None)  # registry returns no tool def
        agent.build_session_executor = MagicMock()
        session = _session({"claude_code_session_id": "cc1", "claude_code_tool_name": "gone"})

        assert agent.reattach_executor(session) is False
        agent.build_session_executor.assert_not_called()

    def test_restores_saved_permission_rules(self):
        td = _cc_tool_def()
        agent = _make_agent(td)
        agent.build_session_executor = MagicMock(return_value=MagicMock())
        session = _session(
            {
                "claude_code_session_id": "cc1",
                "claude_code_tool_name": "claude_code",
                "permission_rules": [
                    {"tool_name": "Bash", "decision": "allow", "scope": "tool_session", "path_prefix": None}
                ],
            }
        )

        assert agent.reattach_executor(session) is True
        # A PermissionManager was attached from the saved rules.
        assert session.permission_manager is not None
