"""Tests for session lifecycle methods on PromptRouter / SessionLifecycleMixin.

Covers:
- ``ensure_session`` — create/reuse/replace sessions
- ``end_session`` — normal, already-completed idempotent, terminal-state error
- ``pause_session`` — state transition, buffer message, executor teardown
- ``resume_session`` — state transition, buffer message, not-paused error
- ``interrupt_subprocess`` — kills executor, session stays ACTIVE
- ``_reap_inactive_sessions`` — auto-ends stale sessions, skips paused/terminal
- ``_check_token_limit_exceeded`` — limit enforcement per token type
- ``_contains_session_end_ask`` — tag detection in content variants
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.buffer import MessageType
from src.core.prompt_router import PromptRouter
from src.core.session import ActiveSession, SessionManager, SessionStatus, SessionType
from src.tools.registry import ToolRegistry

_TOOLS_DIR = Path(__file__).parent.parent.parent / "tools"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_router(session_manager: SessionManager) -> PromptRouter:
    llm = MagicMock()
    registry = ToolRegistry()
    registry.load_from_directory(_TOOLS_DIR)
    settings = MagicMock()
    settings.SESSION_INPUT_TOKEN_LIMIT = 0
    settings.SESSION_OUTPUT_TOKEN_LIMIT = 0
    return PromptRouter(
        llm,
        session_manager,
        registry,
        settings=settings,
    )


# ---------------------------------------------------------------------------
# ensure_session
# ---------------------------------------------------------------------------


class TestEnsureSession:
    def test_none_creates_new_session(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session_id = router.ensure_session(None)
        assert session_manager.get_session(session_id) is not None

    def test_returns_existing_active_session(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session_id = router.ensure_session(session.id)
        assert session_id == session.id

    def test_unknown_id_creates_new_session(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session_id = router.ensure_session("00000000-0000-0000-0000-000000000000")
        # The returned ID is a fresh session, not the unknown one
        assert session_manager.get_session(session_id) is not None

    def test_completed_session_creates_new(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.complete()
        new_id = router.ensure_session(session.id)
        assert new_id != session.id
        assert session_manager.get_session(new_id) is not None

    def test_cancelled_session_creates_new(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.ONE_SHOT)
        session.set_active()
        session.cancel()
        new_id = router.ensure_session(session.id)
        assert new_id != session.id

    def test_failed_session_creates_new(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.ONE_SHOT)
        session.fail()
        new_id = router.ensure_session(session.id)
        assert new_id != session.id


# ---------------------------------------------------------------------------
# end_session
# ---------------------------------------------------------------------------


class TestEndSession:
    async def test_ends_active_session(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        result = await router.end_session(session.id)
        assert result.status == SessionStatus.COMPLETED

    async def test_pushes_session_end_message(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        await router.end_session(session.id)
        end_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_END]
        assert len(end_msgs) == 1
        assert end_msgs[0].data["reason"] == "user_ended"

    async def test_already_completed_is_idempotent(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.complete()
        # Should not raise — returns session as-is
        result = await router.end_session(session.id)
        assert result.status == SessionStatus.COMPLETED

    async def test_cancelled_session_raises(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.ONE_SHOT)
        session.set_active()
        session.cancel()
        with pytest.raises(RuntimeError, match="terminal state"):
            await router.end_session(session.id)

    async def test_nonexistent_session_raises(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        with pytest.raises(ValueError, match="not found"):
            await router.end_session("nonexistent-id")

    async def test_ends_paused_session(self, session_manager: SessionManager) -> None:
        """Paused sessions can still be explicitly ended by the user."""
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()
        result = await router.end_session(session.id)
        assert result.status == SessionStatus.COMPLETED


# ---------------------------------------------------------------------------
# pause_session
# ---------------------------------------------------------------------------


class TestPauseSession:
    async def test_pauses_active_session(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        result = await router.pause_session(session.id)
        assert result.status == SessionStatus.PAUSED

    async def test_pushes_session_paused_message(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        await router.pause_session(session.id)
        paused_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_PAUSED]
        assert len(paused_msgs) == 1

    async def test_cancels_pending_permissions_on_pause(self, session_manager: SessionManager) -> None:
        from src.core.permissions import PermissionManager
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        pm = PermissionManager()
        session.permission_manager = pm
        pending = pm.create_request("Bash", {})
        await router.pause_session(session.id)
        # All pending requests should be auto-denied
        assert pending.event.is_set()

    async def test_nonexistent_session_raises(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        with pytest.raises(ValueError, match="not found"):
            await router.pause_session("nonexistent-id")

    async def test_pause_with_running_executor(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.LONG_RUNNING)
        session.set_active()
        mock_executor = AsyncMock()
        mock_executor.cancel = AsyncMock()
        session.claude_code_executor = mock_executor
        await router.pause_session(session.id)
        mock_executor.cancel.assert_awaited_once()
        assert session.claude_code_executor is None


# ---------------------------------------------------------------------------
# resume_session
# ---------------------------------------------------------------------------


class TestResumeSession:
    async def test_resumes_paused_session(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()
        result = await router.resume_session(session.id)
        assert result.status == SessionStatus.ACTIVE

    async def test_pushes_session_resumed_message(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()
        await router.resume_session(session.id)
        msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_RESUMED]
        assert len(msgs) == 1

    async def test_resume_non_paused_raises(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        with pytest.raises(RuntimeError):
            await router.resume_session(session.id)

    async def test_nonexistent_session_raises(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        with pytest.raises(ValueError, match="not found"):
            await router.resume_session("nonexistent-id")

    async def test_resume_reconstructs_claude_code_executor(
        self, session_manager: SessionManager
    ) -> None:
        """Resuming a paused Claude Code session must restore the executor.

        pause_session() kills the subprocess and sets claude_code_executor=None,
        but preserves claude_code_session_id / claude_code_tool_name in metadata.
        resume_session() must reconstruct the executor so that the next
        handle_prompt() routes back to Claude Code rather than the outer LLM.
        """
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.LONG_RUNNING)
        session.set_active()

        # Simulate state left by pause_session() after a Claude Code session:
        # executor is None but metadata records the prior CC session identifiers.
        session.metadata["claude_code_session_id"] = "cc-session-abc123"
        session.metadata["claude_code_tool_name"] = "claude_code"
        session.metadata["claude_code_parameters"] = {"working_directory": "/tmp"}
        session.claude_code_executor = None
        session.pause()

        result = await router.resume_session(session.id)

        assert result.status == SessionStatus.ACTIVE
        assert result.claude_code_executor is not None, (
            "claude_code_executor must be restored on resume so routing goes to Claude Code"
        )

    async def test_resume_without_cc_metadata_leaves_executor_none(
        self, session_manager: SessionManager
    ) -> None:
        """Resuming a plain (non-agent) session must not create a spurious executor."""
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()

        result = await router.resume_session(session.id)

        assert result.status == SessionStatus.ACTIVE
        assert result.claude_code_executor is None
        assert result.codex_executor is None

    async def test_resume_routes_to_claude_code_not_llm(
        self, session_manager: SessionManager
    ) -> None:
        """After pause+resume a Claude Code session, handle_prompt must forward to CC.

        This is the end-to-end regression test for the bug where resumed messages
        were routed to the RCFlow LLM instead of the Claude Code subprocess.
        """
        from unittest.mock import patch

        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.LONG_RUNNING)
        session.set_active()

        # Simulate what pause_session() does: kill executor, preserve metadata.
        session.metadata["claude_code_session_id"] = "cc-session-xyz"
        session.metadata["claude_code_tool_name"] = "claude_code"
        session.metadata["claude_code_parameters"] = {"working_directory": "/tmp"}
        session.claude_code_executor = None
        session.pause()

        # Resume the session (rehydrates the executor).
        await router.resume_session(session.id)
        assert session.claude_code_executor is not None

        # Now send a message — it must be forwarded to Claude Code, not the LLM.
        with patch.object(router, "_forward_to_claude_code", new_callable=AsyncMock) as mock_fwd:
            # _ensure_session_row_in_db is a background DB helper; stub it out.
            with patch.object(router, "_ensure_session_row_in_db", new_callable=AsyncMock):
                await router.handle_prompt("continue the task", session.id)

        mock_fwd.assert_awaited_once()
        called_session, called_text = mock_fwd.call_args.args
        assert called_session.id == session.id
        assert called_text == "continue the task"


# ---------------------------------------------------------------------------
# interrupt_subprocess
# ---------------------------------------------------------------------------


class TestInterruptSubprocess:
    async def test_kills_claude_code_executor(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.LONG_RUNNING)
        session.set_active()
        mock_executor = AsyncMock()
        mock_executor.cancel = AsyncMock()
        session.claude_code_executor = mock_executor
        await router.interrupt_subprocess(session.id)
        mock_executor.cancel.assert_awaited_once()
        assert session.claude_code_executor is None

    async def test_session_remains_active_after_interrupt(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        result = await router.interrupt_subprocess(session.id)
        assert result.status == SessionStatus.ACTIVE

    async def test_interrupt_clears_subprocess_tracking(self, session_manager: SessionManager) -> None:
        """Interrupting a subprocess must clear ALL tracking fields and push null status."""
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.LONG_RUNNING)
        session.set_active()

        # Simulate active subprocess tracking
        session.subprocess_started_at = datetime.now(UTC)
        session.subprocess_current_tool = "Edit"
        session.subprocess_type = "claude_code"
        session.subprocess_display_name = "Claude Code"
        session.subprocess_working_directory = "/tmp/project"

        mock_executor = AsyncMock()
        mock_executor.cancel = AsyncMock()
        session.claude_code_executor = mock_executor

        await router.interrupt_subprocess(session.id)

        assert session.subprocess_started_at is None
        assert session.subprocess_current_tool is None
        assert session.subprocess_type is None
        assert session.subprocess_display_name is None
        assert session.subprocess_working_directory is None

    async def test_terminal_session_raises(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.ONE_SHOT)
        session.complete()
        with pytest.raises(RuntimeError, match="terminal state"):
            await router.interrupt_subprocess(session.id)

    async def test_paused_session_raises(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()
        with pytest.raises(RuntimeError, match="paused"):
            await router.interrupt_subprocess(session.id)

    async def test_nonexistent_session_raises(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        with pytest.raises(ValueError, match="not found"):
            await router.interrupt_subprocess("nonexistent-id")


# ---------------------------------------------------------------------------
# _reap_inactive_sessions
# ---------------------------------------------------------------------------


class TestReapInactiveSessions:
    async def test_ends_session_past_inactivity_threshold(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        # Backdate last_activity_at beyond the 6-hour threshold
        session.last_activity_at = datetime.now(UTC) - timedelta(hours=7)

        await router._reap_inactive_sessions()

        assert session.status == SessionStatus.COMPLETED

    async def test_does_not_end_recent_session(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        # Recent activity — should NOT be reaped
        session.last_activity_at = datetime.now(UTC) - timedelta(minutes=30)

        await router._reap_inactive_sessions()

        assert session.status != SessionStatus.COMPLETED

    async def test_skips_paused_sessions(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session.pause()
        session.last_activity_at = datetime.now(UTC) - timedelta(hours=10)

        await router._reap_inactive_sessions()

        assert session.status == SessionStatus.PAUSED

    async def test_skips_completed_sessions(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.complete()
        # completed_at won't matter — already terminal

        await router._reap_inactive_sessions()

        assert session.status == SessionStatus.COMPLETED

    async def test_reaps_multiple_stale_sessions(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        sessions = []
        for _ in range(3):
            s = session_manager.create_session(SessionType.CONVERSATIONAL)
            s.set_active()
            s.last_activity_at = datetime.now(UTC) - timedelta(hours=8)
            sessions.append(s)

        await router._reap_inactive_sessions()

        assert all(s.status == SessionStatus.COMPLETED for s in sessions)


# ---------------------------------------------------------------------------
# _check_token_limit_exceeded
# ---------------------------------------------------------------------------


class TestCheckTokenLimitExceeded:
    def _router_with_limits(
        self,
        session_manager: SessionManager,
        input_limit: int = 0,
        output_limit: int = 0,
    ) -> PromptRouter:
        llm = MagicMock()
        registry = ToolRegistry()
        settings = MagicMock()
        settings.SESSION_INPUT_TOKEN_LIMIT = input_limit
        settings.SESSION_OUTPUT_TOKEN_LIMIT = output_limit
        return PromptRouter(llm, session_manager, registry, settings=settings)

    def test_no_limits_returns_false(self, session_manager: SessionManager) -> None:
        router = self._router_with_limits(session_manager, 0, 0)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.input_tokens = 999_999
        assert router._check_token_limit_exceeded(session) is False

    def test_input_limit_exceeded_returns_true(self, session_manager: SessionManager) -> None:
        router = self._router_with_limits(session_manager, input_limit=1000)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.input_tokens = 1000
        session.tool_input_tokens = 0
        assert router._check_token_limit_exceeded(session) is True

    def test_output_limit_exceeded_returns_true(self, session_manager: SessionManager) -> None:
        router = self._router_with_limits(session_manager, output_limit=500)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.output_tokens = 400
        session.tool_output_tokens = 200
        assert router._check_token_limit_exceeded(session) is True

    def test_input_limit_not_exceeded_returns_false(self, session_manager: SessionManager) -> None:
        router = self._router_with_limits(session_manager, input_limit=1000)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.input_tokens = 500
        session.tool_input_tokens = 0
        assert router._check_token_limit_exceeded(session) is False

    def test_exceeded_pushes_error_to_buffer(self, session_manager: SessionManager) -> None:
        router = self._router_with_limits(session_manager, input_limit=100)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.input_tokens = 100
        session.tool_input_tokens = 0
        router._check_token_limit_exceeded(session)
        error_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.ERROR]
        assert len(error_msgs) == 1
        assert error_msgs[0].data.get("code") == "TOKEN_LIMIT_REACHED"


# ---------------------------------------------------------------------------
# _contains_session_end_ask
# ---------------------------------------------------------------------------


class TestContainsSessionEndAsk:
    def test_detects_tag_in_string_content(self) -> None:
        msg = {"content": "Please confirm. [SessionEndAsk]"}
        assert PromptRouter._contains_session_end_ask(msg) is True

    def test_returns_false_when_tag_absent(self) -> None:
        msg = {"content": "Normal response without the tag."}
        assert PromptRouter._contains_session_end_ask(msg) is False

    def test_detects_tag_in_list_content(self) -> None:
        msg = {
            "content": [
                {"type": "text", "text": "Some intro."},
                {"type": "text", "text": "[SessionEndAsk] Please confirm."},
            ]
        }
        assert PromptRouter._contains_session_end_ask(msg) is True

    def test_returns_false_when_no_tag_in_list(self) -> None:
        msg = {
            "content": [
                {"type": "text", "text": "No tag here."},
            ]
        }
        assert PromptRouter._contains_session_end_ask(msg) is False


# ---------------------------------------------------------------------------
# agent_type property
# ---------------------------------------------------------------------------


class TestAgentType:
    def test_none_for_plain_session(self) -> None:
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        assert session.agent_type is None

    def test_claude_code_when_executor_set(self) -> None:
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.claude_code_executor = object()  # type: ignore[assignment]
        assert session.agent_type == "claude_code"

    def test_codex_when_executor_set(self) -> None:
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.codex_executor = object()  # type: ignore[assignment]
        assert session.agent_type == "codex"

    def test_claude_code_takes_priority_when_both_set(self) -> None:
        # In practice only one executor is ever set at a time, but the property
        # should be deterministic if both are somehow present.
        session = ActiveSession("test-id", SessionType.CONVERSATIONAL)
        session.claude_code_executor = object()  # type: ignore[assignment]
        session.codex_executor = object()  # type: ignore[assignment]
        assert session.agent_type == "claude_code"

    def test_broadcast_includes_agent_type(self, session_manager: SessionManager) -> None:
        """broadcast_session_update should include agent_type in the update dict."""
        updates: list[dict] = []
        queue = session_manager.subscribe_updates("test-sub")

        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        # Drain initial broadcast
        while not queue.empty():
            queue.get_nowait()

        session.claude_code_executor = object()  # type: ignore[assignment]
        session_manager.broadcast_session_update(session)

        msg = queue.get_nowait()
        assert msg is not None
        assert msg.get("agent_type") == "claude_code"

        session_manager.unsubscribe_updates("test-sub")

    def test_handles_missing_content_key(self) -> None:
        msg: dict = {}
        assert PromptRouter._contains_session_end_ask(msg) is False
