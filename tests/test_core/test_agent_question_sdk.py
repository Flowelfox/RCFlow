"""Tests for the SDK ``can_use_tool`` callback that resolves AskUserQuestion
and permission prompts in-process.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny

from src.core.agent_claude_code import ClaudeCodeAgent
from src.core.buffer import MessageType
from src.core.permissions import PermissionDecision
from src.core.session_lifecycle import _parse_answer_text


def _make_agent() -> ClaudeCodeAgent:
    agent = ClaudeCodeAgent.__new__(ClaudeCodeAgent)
    agent._r = MagicMock()
    return agent


def _make_session() -> MagicMock:
    session = MagicMock()
    session.id = "s1"
    session.permission_manager = None
    session._question_event = None
    session._question_answers = None
    session._question_tool_use_id = None
    session._plan_mode_event = None
    session._plan_mode_approved = False
    session._plan_review_event = None
    session._plan_review_approved = False
    session._plan_review_feedback = None
    session.buffer = MagicMock()
    session.buffer.push_text = MagicMock()
    # One unresolved AskUserQuestion TOOL_START for the annotation loop.
    session.buffer.text_history = [
        SimpleNamespace(
            message_type=MessageType.TOOL_START,
            data={"tool_name": "AskUserQuestion"},
        )
    ]
    return session


class TestCanUseTool:
    @pytest.mark.asyncio
    async def test_ask_user_question_returns_answers(self):
        agent = _make_agent()
        session = _make_session()
        cb = agent._make_can_use_tool(session)

        async def answer():
            for _ in range(10_000):
                if session._question_event is not None:
                    session._question_answers = {"Pick?": "A"}
                    session._question_event.set()
                    return
                await asyncio.sleep(0)

        result, _ = await asyncio.gather(
            cb("AskUserQuestion", {"questions": [{"question": "Pick?"}]}, object()),
            answer(),
        )

        assert isinstance(result, PermissionResultAllow)
        assert result.updated_input == {
            "questions": [{"question": "Pick?"}],
            "answers": {"Pick?": "A"},
        }
        # Widget pushed once; buffered TOOL_START annotated for replay.
        starts = [c for c in session.buffer.push_text.call_args_list if c[0][0] == MessageType.TOOL_START]
        assert len(starts) == 1
        assert session.buffer.text_history[0].data["answered"] is True
        assert session.buffer.text_history[0].data["answer"] == "Pick?: A"

    @pytest.mark.asyncio
    async def test_other_tool_auto_allows_without_permission_manager(self):
        agent = _make_agent()
        session = _make_session()
        cb = agent._make_can_use_tool(session)
        result = await cb("Bash", {"command": "ls"}, object())
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_other_tool_denied_by_permission_manager(self):
        agent = _make_agent()
        session = _make_session()

        async def deny(_session, _name, _input):
            return PermissionDecision.DENY

        agent._handle_permission_check = deny  # type: ignore[method-assign]
        cb = agent._make_can_use_tool(session)
        result = await cb("Bash", {"command": "rm -rf /"}, object())
        assert isinstance(result, PermissionResultDeny)


class TestPlanModeCallback:
    @pytest.mark.asyncio
    async def test_enter_plan_mode_approved_allows(self):
        agent = _make_agent()
        session = _make_session()

        async def approve():
            for _ in range(10_000):
                if session._plan_mode_event is not None:
                    session._plan_mode_approved = True
                    session._plan_mode_event.set()
                    return
                await asyncio.sleep(0)

        result, _ = await asyncio.gather(agent._handle_enter_plan_mode(session), approve())
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_enter_plan_mode_denied_interrupts(self):
        agent = _make_agent()
        session = _make_session()

        async def deny():
            for _ in range(10_000):
                if session._plan_mode_event is not None:
                    session._plan_mode_approved = False
                    session._plan_mode_event.set()
                    return
                await asyncio.sleep(0)

        result, _ = await asyncio.gather(agent._handle_enter_plan_mode(session), deny())
        assert isinstance(result, PermissionResultDeny)
        assert result.interrupt is True

    @pytest.mark.asyncio
    async def test_exit_plan_mode_approved_allows(self):
        agent = _make_agent()
        session = _make_session()

        async def approve():
            for _ in range(10_000):
                if session._plan_review_event is not None:
                    session._plan_review_approved = True
                    session._plan_review_event.set()
                    return
                await asyncio.sleep(0)

        result, _ = await asyncio.gather(agent._handle_exit_plan_mode(session, {}), approve())
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_exit_plan_mode_feedback_denies_with_message(self):
        agent = _make_agent()
        session = _make_session()

        async def feedback():
            for _ in range(10_000):
                if session._plan_review_event is not None:
                    session._plan_review_approved = False
                    session._plan_review_feedback = "use async instead"
                    session._plan_review_event.set()
                    return
                await asyncio.sleep(0)

        result, _ = await asyncio.gather(agent._handle_exit_plan_mode(session, {}), feedback())
        assert isinstance(result, PermissionResultDeny)
        assert result.message == "use async instead"


class TestParseAnswerText:
    def test_parses_pairs(self):
        assert _parse_answer_text("Q1: A\nQ2: B") == {"Q1": "A", "Q2": "B"}

    def test_blank_lines_skipped(self):
        assert _parse_answer_text("Q: A\n\n") == {"Q": "A"}

    def test_line_without_separator(self):
        assert _parse_answer_text("just text") == {"just text": "just text"}
