import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.buffer import MessageType
from src.core.llm import ToolCallRequest
from src.core.permissions import PermissionManager
from src.core.prompt_router import PromptRouter
from src.core.session import SessionManager, SessionStatus, SessionType
from src.executors.base import ExecutionChunk
from src.tools.registry import ToolRegistry

_TOOLS_DIR = Path(__file__).parent.parent.parent / "tools"


def _make_router(session_manager: SessionManager) -> PromptRouter:
    """Create a PromptRouter with mocked LLM client and tool registry."""
    llm_client = MagicMock()
    tool_registry = MagicMock()
    return PromptRouter(llm_client, session_manager, tool_registry)


def _make_router_with_real_registry(session_manager: SessionManager) -> PromptRouter:
    """Create a PromptRouter with a real tool registry loaded from the tools directory."""
    llm_client = MagicMock()
    registry = ToolRegistry()
    registry.load_from_directory(_TOOLS_DIR)
    return PromptRouter(llm_client, session_manager, registry)


@pytest.mark.asyncio
async def test_cancel_active_session(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.ONE_SHOT)
    session.set_active()

    result = await router.cancel_session(session.id)

    assert result.status == SessionStatus.CANCELLED
    assert result.ended_at is not None

    # Verify SESSION_END message was pushed to buffer
    end_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_END]
    assert len(end_msgs) == 1
    assert end_msgs[0].data["reason"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_session_with_executor(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    # Mock Claude Code executor
    mock_executor = AsyncMock()
    mock_executor.cancel = AsyncMock()
    session.claude_code_executor = mock_executor

    # Mock a running background task
    task_future: asyncio.Future[None] = asyncio.get_event_loop().create_future()
    task = asyncio.ensure_future(task_future)
    session._claude_code_stream_task = task  # type: ignore[assignment]

    result = await router.cancel_session(session.id)

    assert result.status == SessionStatus.CANCELLED
    mock_executor.cancel.assert_awaited_once()
    assert session.claude_code_executor is None
    assert session._claude_code_stream_task is None


@pytest.mark.asyncio
async def test_cancel_nonexistent_session(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    with pytest.raises(ValueError, match="Session not found"):
        await router.cancel_session("nonexistent-id")


@pytest.mark.asyncio
async def test_cancel_already_completed_session(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.ONE_SHOT)
    session.complete()

    with pytest.raises(RuntimeError, match="terminal state"):
        await router.cancel_session(session.id)


@pytest.mark.asyncio
async def test_cancel_already_cancelled_session(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.ONE_SHOT)
    session.set_active()
    session.cancel()

    with pytest.raises(RuntimeError, match="terminal state"):
        await router.cancel_session(session.id)


# --- Summary generation tests ---


@pytest.mark.asyncio
async def test_summarize_and_push_pushes_summary_message(session_manager: SessionManager) -> None:
    """_summarize_and_push should push a SUMMARY message with content from the LLM."""
    router = _make_router(session_manager)
    router._llm.summarize = AsyncMock(return_value="One sentence summary.")  # type: ignore[method-assign]

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    await router._summarize_and_push(session, "Long result text.")

    summary_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SUMMARY]
    assert len(summary_msgs) == 1
    assert summary_msgs[0].data["content"] == "One sentence summary."
    assert summary_msgs[0].data["session_id"] == session.id


@pytest.mark.asyncio
async def test_summarize_and_push_failure_does_not_raise(session_manager: SessionManager) -> None:
    """A LLM exception in _summarize_and_push must not propagate — session stays alive."""
    router = _make_router(session_manager)
    router._llm.summarize = AsyncMock(side_effect=RuntimeError("LLM unavailable"))  # type: ignore[method-assign]

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    # Must not raise
    await router._summarize_and_push(session, "Some text.")

    summary_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SUMMARY]
    assert len(summary_msgs) == 0


# --- Title generation tests ---


@pytest.mark.asyncio
async def test_generate_and_set_title(session_manager: SessionManager) -> None:
    """_generate_and_set_title should call llm.generate_title and set session.title."""
    router = _make_router(session_manager)
    router._llm.generate_title = AsyncMock(return_value="List project files")  # type: ignore[method-assign]

    session = session_manager.create_session(SessionType.CONVERSATIONAL)
    session.set_active()
    assert session.title is None

    await router._generate_and_set_title(session, "list files", "Here are the files...")

    router._llm.generate_title.assert_awaited_once_with("list files", "Here are the files...")  # type: ignore[attr-defined]
    assert session.title == "List project files"


@pytest.mark.asyncio
async def test_generate_and_set_title_failure_does_not_raise(session_manager: SessionManager) -> None:
    """_generate_and_set_title should swallow exceptions without breaking the session."""
    router = _make_router(session_manager)
    router._llm.generate_title = AsyncMock(side_effect=RuntimeError("LLM unavailable"))  # type: ignore[method-assign]

    session = session_manager.create_session(SessionType.CONVERSATIONAL)
    session.set_active()

    # Should not raise
    await router._generate_and_set_title(session, "hello", "world")

    # On LLM failure the finally-block sets a fallback title derived from user_text
    assert session.title == "hello"


@pytest.mark.asyncio
async def test_fire_title_task(session_manager: SessionManager) -> None:
    """_fire_title_task should schedule a background task that completes."""
    router = _make_router(session_manager)
    router._llm.generate_title = AsyncMock(return_value="Setup dev environment")  # type: ignore[method-assign]

    session = session_manager.create_session(SessionType.CONVERSATIONAL)
    session.set_active()

    router._fire_title_task(session, "setup dev", "I'll help you set up...")

    # Let the background task complete
    if router._pending_title_tasks:
        await asyncio.gather(*router._pending_title_tasks)

    assert session.title == "Setup dev environment"


# --- Claude Code stream tests ---


async def _async_stream_chunks(chunks: list[ExecutionChunk]):
    """Helper to create an async generator from a list of ExecutionChunks."""
    for chunk in chunks:
        yield chunk


@pytest.mark.asyncio
async def test_relay_claude_code_stream_max_turns_subtype(session_manager: SessionManager) -> None:
    """When result has subtype=max_turns, the session is paused with reason='max_turns'."""
    router = _make_router(session_manager)

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    result_event = json.dumps({"type": "result", "result": "", "subtype": "max_turns"})
    stream = _async_stream_chunks([ExecutionChunk(content=result_event, stream="stdout")])

    await router._relay_claude_code_stream(session, stream)

    # Session should be paused with reason "max_turns"
    assert session.status == SessionStatus.PAUSED
    assert session.paused_reason == "max_turns"

    # SESSION_PAUSED with reason and AGENT_GROUP_END must be in the buffer
    paused_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_PAUSED]
    assert len(paused_msgs) == 1
    assert paused_msgs[0].data["reason"] == "max_turns"
    assert paused_msgs[0].data["claude_code_interrupted"] is False

    agent_end_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.AGENT_GROUP_END]
    assert len(agent_end_msgs) == 1
    # AGENT_GROUP_END must precede SESSION_PAUSED
    assert agent_end_msgs[0].sequence < paused_msgs[0].sequence


# --- Interrupt subprocess tests ---


@pytest.mark.asyncio
async def test_interrupt_subprocess_active_session_no_executor(session_manager: SessionManager) -> None:
    """Interrupting an active session without a running executor clears fields and stays ACTIVE."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    result = await router.interrupt_subprocess(session.id)

    # Session must remain ACTIVE — not paused, not cancelled
    assert result.status == SessionStatus.ACTIVE

    # No AGENT_GROUP_END when there was no executor
    group_end_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.AGENT_GROUP_END]
    assert len(group_end_msgs) == 0

    # subprocess_status is ephemeral — must NOT be in text_history
    status_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SUBPROCESS_STATUS]
    assert len(status_msgs) == 0


@pytest.mark.asyncio
async def test_interrupt_subprocess_with_claude_code_executor(session_manager: SessionManager) -> None:
    """Interrupting a session with a running Claude Code executor cancels it and pushes AGENT_GROUP_END."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    mock_executor = AsyncMock()
    mock_executor.cancel = AsyncMock()
    session.claude_code_executor = mock_executor

    task_future: asyncio.Future[None] = asyncio.get_event_loop().create_future()
    task = asyncio.ensure_future(task_future)
    session._claude_code_stream_task = task  # type: ignore[assignment]

    result = await router.interrupt_subprocess(session.id)

    # Session stays ACTIVE (not paused)
    assert result.status == SessionStatus.ACTIVE

    # Executor was cancelled and cleared
    mock_executor.cancel.assert_awaited_once()
    assert session.claude_code_executor is None
    assert session._claude_code_stream_task is None

    # AGENT_GROUP_END + interrupt TEXT_CHUNK pushed to buffer
    group_end_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.AGENT_GROUP_END]
    assert len(group_end_msgs) == 1

    text_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.TEXT_CHUNK]
    assert any("[Subprocess interrupted by user]" in m.data.get("content", "") for m in text_msgs)

    # No SESSION_PAUSED pushed
    paused_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_PAUSED]
    assert len(paused_msgs) == 0

    # Ephemeral subprocess_status must NOT be in archived history
    status_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SUBPROCESS_STATUS]
    assert len(status_msgs) == 0


@pytest.mark.asyncio
async def test_interrupt_subprocess_nonexistent_raises(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    with pytest.raises(ValueError, match="Session not found"):
        await router.interrupt_subprocess("nonexistent-id")


@pytest.mark.asyncio
async def test_interrupt_subprocess_terminal_raises(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.ONE_SHOT)
    session.complete()

    with pytest.raises(RuntimeError, match="terminal state"):
        await router.interrupt_subprocess(session.id)


@pytest.mark.asyncio
async def test_interrupt_subprocess_paused_raises(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.CONVERSATIONAL)
    session.set_active()
    session.pause()

    with pytest.raises(RuntimeError, match="paused"):
        await router.interrupt_subprocess(session.id)


# --- Pause / Resume tests ---


@pytest.mark.asyncio
async def test_pause_active_session(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.CONVERSATIONAL)
    session.set_active()

    result = await router.pause_session(session.id)

    assert result.status == SessionStatus.PAUSED
    assert result.paused_at is not None

    paused_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_PAUSED]
    assert len(paused_msgs) == 1
    assert paused_msgs[0].data["session_id"] == session.id


@pytest.mark.asyncio
async def test_pause_session_with_executor(session_manager: SessionManager) -> None:
    """Pausing a session with a running Claude Code executor should kill it."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    # Mock Claude Code executor
    mock_executor = AsyncMock()
    mock_executor.cancel = AsyncMock()
    session.claude_code_executor = mock_executor

    # Mock a running background task
    task_future: asyncio.Future[None] = asyncio.get_event_loop().create_future()
    task = asyncio.ensure_future(task_future)
    session._claude_code_stream_task = task  # type: ignore[assignment]

    result = await router.pause_session(session.id)

    assert result.status == SessionStatus.PAUSED
    mock_executor.cancel.assert_awaited_once()
    assert session.claude_code_executor is None
    assert session._claude_code_stream_task is None

    # Verify AGENT_GROUP_END was pushed before SESSION_PAUSED
    group_end_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.AGENT_GROUP_END]
    assert len(group_end_msgs) == 1

    paused_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_PAUSED]
    assert len(paused_msgs) == 1
    assert paused_msgs[0].data["claude_code_interrupted"] is True
    assert group_end_msgs[0].sequence < paused_msgs[0].sequence


@pytest.mark.asyncio
async def test_pause_session_without_executor(session_manager: SessionManager) -> None:
    """Pausing a session without a Claude Code executor should not push AGENT_GROUP_END."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.CONVERSATIONAL)
    session.set_active()

    result = await router.pause_session(session.id)

    assert result.status == SessionStatus.PAUSED
    group_end_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.AGENT_GROUP_END]
    assert len(group_end_msgs) == 0

    paused_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_PAUSED]
    assert len(paused_msgs) == 1
    assert paused_msgs[0].data["claude_code_interrupted"] is False


@pytest.mark.asyncio
async def test_pause_and_resume_session_with_executor(session_manager: SessionManager) -> None:
    """After pausing a session with a running executor, resuming should yield an active session with no executor."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    # Mock Claude Code executor
    mock_executor = AsyncMock()
    mock_executor.cancel = AsyncMock()
    session.claude_code_executor = mock_executor

    # Mock a running background task
    task_future: asyncio.Future[None] = asyncio.get_event_loop().create_future()
    task = asyncio.ensure_future(task_future)
    session._claude_code_stream_task = task  # type: ignore[assignment]

    await router.pause_session(session.id)
    assert session.status == SessionStatus.PAUSED
    assert session.claude_code_executor is None

    result = await router.resume_session(session.id)

    assert result.status == SessionStatus.ACTIVE
    assert result.claude_code_executor is None
    assert result._claude_code_stream_task is None


@pytest.mark.asyncio
async def test_pause_nonexistent_session_raises(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    with pytest.raises(ValueError, match="Session not found"):
        await router.pause_session("nonexistent-id")


@pytest.mark.asyncio
async def test_pause_terminal_session_raises(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.ONE_SHOT)
    session.complete()

    with pytest.raises(RuntimeError, match="terminal state"):
        await router.pause_session(session.id)


@pytest.mark.asyncio
async def test_resume_paused_session(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.CONVERSATIONAL)
    session.set_active()
    await router.pause_session(session.id)

    result = await router.resume_session(session.id)

    assert result.status == SessionStatus.ACTIVE
    assert result.paused_at is None

    resumed_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_RESUMED]
    assert len(resumed_msgs) == 1


@pytest.mark.asyncio
async def test_resume_nonexistent_session_raises(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    with pytest.raises(ValueError, match="Session not found"):
        await router.resume_session("nonexistent-id")


@pytest.mark.asyncio
async def test_resume_non_paused_raises(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.CONVERSATIONAL)
    session.set_active()

    with pytest.raises(RuntimeError, match="Cannot resume"):
        await router.resume_session(session.id)


@pytest.mark.asyncio
async def test_resume_session_completed_while_paused(session_manager: SessionManager) -> None:
    """When CC completes while paused, resume should trigger the deferred completion."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    # Simulate: session paused, then CC finishes (calls complete() which sets flag)
    session.pause()
    session.complete()  # sets completed_while_paused flag
    assert session.status == SessionStatus.PAUSED
    assert session.metadata.get("completed_while_paused") is True

    result = await router.resume_session(session.id)

    # The deferred completion should now have fired
    assert result.status == SessionStatus.COMPLETED
    assert result.ended_at is not None
    # Flag should be consumed
    assert "completed_while_paused" not in session.metadata

    end_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_END]
    assert len(end_msgs) == 1
    assert end_msgs[0].data["reason"] == "claude_code_finished"


@pytest.mark.asyncio
async def test_cancel_paused_session(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.CONVERSATIONAL)
    session.set_active()
    await router.pause_session(session.id)

    result = await router.cancel_session(session.id)

    assert result.status == SessionStatus.CANCELLED
    assert result.ended_at is not None


@pytest.mark.asyncio
async def test_end_paused_session(session_manager: SessionManager) -> None:
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.CONVERSATIONAL)
    session.set_active()
    await router.pause_session(session.id)

    result = await router.end_session(session.id)

    assert result.status == SessionStatus.COMPLETED
    assert result.ended_at is not None


# --- _build_tool_context tests ---


def test_build_tool_context_agent_only(session_manager: SessionManager) -> None:
    """A single agent mention produces a MUST directive."""
    router = _make_router_with_real_registry(session_manager)
    ctx = router._build_tool_context(["ClaudeCode"])
    assert ctx is not None
    assert "MUST" in ctx
    assert "claude_code" in ctx
    assert "worktree" not in ctx.lower() or "Step 1" not in ctx


def test_build_tool_context_worktree_only(session_manager: SessionManager) -> None:
    """A worktree-only mention produces a preference block (no orchestration)."""
    router = _make_router_with_real_registry(session_manager)
    ctx = router._build_tool_context(["Worktree"])
    assert ctx is not None
    assert "Tool preference" in ctx
    assert "Step 1" not in ctx


def test_build_tool_context_worktree_and_agent_orchestration(session_manager: SessionManager) -> None:
    """Worktree + agent mention produces the two-step orchestration directive."""
    router = _make_router_with_real_registry(session_manager)
    ctx = router._build_tool_context(["Worktree", "ClaudeCode"])
    assert ctx is not None
    assert "Step 1" in ctx
    assert "Step 2" in ctx
    assert "worktree" in ctx
    assert "working_directory" in ctx
    # Must not fall back to the split MUST + preference blocks
    assert "Mandatory tool selection" not in ctx
    assert "Tool preference" not in ctx


def test_build_tool_context_worktree_and_agent_case_insensitive(session_manager: SessionManager) -> None:
    """Case-insensitive mention matching still triggers orchestration."""
    router = _make_router_with_real_registry(session_manager)
    ctx = router._build_tool_context(["worktree", "claude_code"])
    assert ctx is not None
    assert "Step 1" in ctx
    assert "Step 2" in ctx


def test_build_tool_context_unknown_mention_ignored(session_manager: SessionManager) -> None:
    """Unknown tool mentions are silently ignored; valid ones still produce output."""
    router = _make_router_with_real_registry(session_manager)
    ctx = router._build_tool_context(["NonExistentTool", "ClaudeCode"])
    assert ctx is not None
    assert "MUST" in ctx


def test_build_tool_context_all_unknown_returns_none(session_manager: SessionManager) -> None:
    """All-unknown mentions return None."""
    router = _make_router_with_real_registry(session_manager)
    ctx = router._build_tool_context(["Foo", "Bar"])
    assert ctx is None


# ---------------------------------------------------------------------------
# _build_active_worktree_context
# ---------------------------------------------------------------------------


def test_build_active_worktree_context_no_worktree(session_manager: SessionManager) -> None:
    """Returns None when no worktree is selected for the session."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.ONE_SHOT)
    assert router._build_active_worktree_context(session) is None


def test_build_active_worktree_context_with_worktree(session_manager: SessionManager) -> None:
    """Returns a directive containing the selected worktree path and branch."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.ONE_SHOT)
    session.metadata["selected_worktree_path"] = "/repos/myproject/.worktrees/feat-xyz"
    session.metadata["worktree"] = {
        "repo_path": "/repos/myproject",
        "last_action": "new",
        "branch": "feature/feat-xyz",
        "base": "main",
    }

    ctx = router._build_active_worktree_context(session)

    assert ctx is not None
    assert "/repos/myproject/.worktrees/feat-xyz" in ctx
    assert "feature/feat-xyz" in ctx
    assert "working_directory" in ctx


def test_build_active_worktree_context_path_only(session_manager: SessionManager) -> None:
    """Works when worktree metadata dict is absent; only path is needed."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.ONE_SHOT)
    session.metadata["selected_worktree_path"] = "/repos/myproject/.worktrees/feat-xyz"

    ctx = router._build_active_worktree_context(session)

    assert ctx is not None
    assert "/repos/myproject/.worktrees/feat-xyz" in ctx
    assert "working_directory" in ctx


def test_build_active_worktree_context_merge_direction_disambiguation(
    session_manager: SessionManager,
) -> None:
    """The directive must warn the LLM that worktree merge = worktree→main (not main→worktree).

    This guards against the bug where 'pull main into worktree' was interpreted
    as action='merge' (which merges the worktree INTO main), rather than starting
    a claude_code session that runs git commands inside the worktree.
    """
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.ONE_SHOT)
    session.metadata["selected_worktree_path"] = "/repos/myproject/.worktrees/feat-xyz"
    session.metadata["worktree"] = {
        "repo_path": "/repos/myproject",
        "last_action": "new",
        "branch": "feature/feat-xyz",
        "base": "main",
    }

    ctx = router._build_active_worktree_context(session)

    assert ctx is not None
    # Disambiguation block must be present
    assert "merge direction" in ctx.lower() or "disambiguation" in ctx.lower()
    # Must clarify that the worktree tool merges the worktree INTO main
    assert "INTO main" in ctx or "into main" in ctx
    # Must warn against using the worktree tool for main→worktree direction
    assert "pull main into worktree" in ctx.lower() or "main → worktree" in ctx or "main -> worktree" in ctx.lower()
    # Must instruct use of the agent tool (claude_code) with the worktree path
    assert "claude_code" in ctx
    assert "/repos/myproject/.worktrees/feat-xyz" in ctx


# ---------------------------------------------------------------------------
# _update_session_worktree_meta — auto-select after creation
# ---------------------------------------------------------------------------


def test_update_worktree_meta_new_auto_selects_path(session_manager: SessionManager) -> None:
    """After action=new, selected_worktree_path is auto-set from the result JSON."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.ONE_SHOT)

    tool_call = ToolCallRequest(
        tool_use_id="test-id",
        tool_name="worktree",
        tool_input={
            "action": "new",
            "repo_path": "/repos/myproject",
            "branch": "feature/new-thing",
            "base": "main",
        },
    )
    result_json = json.dumps(
        {
            "created": {
                "name": "new-thing",
                "branch": "feature/new-thing",
                "base": "main",
                "path": "/repos/myproject/.worktrees/new-thing",
                "created_at": "2026-03-18T10:00:00",
            }
        }
    )

    router._update_session_worktree_meta(session, tool_call, result_json)

    assert session.metadata.get("selected_worktree_path") == "/repos/myproject/.worktrees/new-thing"
    assert session.metadata["worktree"]["branch"] == "feature/new-thing"


def test_update_worktree_meta_new_handles_missing_path(session_manager: SessionManager) -> None:
    """Gracefully handles malformed result JSON without raising."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.ONE_SHOT)

    tool_call = ToolCallRequest(
        tool_use_id="test-id",
        tool_name="worktree",
        tool_input={"action": "new", "repo_path": "/repos/x", "branch": "feature/x"},
    )

    # Malformed JSON should not raise
    router._update_session_worktree_meta(session, tool_call, "not json")
    assert "selected_worktree_path" not in session.metadata


def test_update_worktree_meta_merge_clears_selected_path(session_manager: SessionManager) -> None:
    """action=merge clears a previously selected worktree path."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.ONE_SHOT)
    session.metadata["selected_worktree_path"] = "/repos/myproject/.worktrees/old"

    tool_call = ToolCallRequest(
        tool_use_id="test-id",
        tool_name="worktree",
        tool_input={"action": "merge", "repo_path": "/repos/myproject", "name": "old"},
    )

    router._update_session_worktree_meta(session, tool_call, '{"merged": true, "name": "old"}')

    assert "selected_worktree_path" not in session.metadata


def test_update_worktree_meta_rm_clears_selected_path(session_manager: SessionManager) -> None:
    """action=rm clears a previously selected worktree path."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.ONE_SHOT)
    session.metadata["selected_worktree_path"] = "/repos/myproject/.worktrees/old"

    tool_call = ToolCallRequest(
        tool_use_id="test-id",
        tool_name="worktree",
        tool_input={"action": "rm", "repo_path": "/repos/myproject", "name": "old"},
    )

    router._update_session_worktree_meta(session, tool_call, '{"removed": true, "name": "old"}')

    assert "selected_worktree_path" not in session.metadata


# ---------------------------------------------------------------------------
# Permission resolution — buffer persistence
# ---------------------------------------------------------------------------


def test_resolve_permission_sets_accepted_in_buffer(session_manager: SessionManager) -> None:
    """Resolving a permission request persists 'accepted' in the buffer message.

    This ensures that when a session is re-opened in a new pane the replayed
    PERMISSION_REQUEST message carries the decision and the widget renders in
    its resolved (non-pending) state.
    """
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    pm = PermissionManager()
    session.permission_manager = pm

    # Simulate what _handle_permission_check does: create a pending request and
    # push the corresponding buffer message.
    pending = pm.create_request("Bash", {"command": "ls"})
    session.buffer.push_text(
        MessageType.PERMISSION_REQUEST,
        {
            "session_id": session.id,
            "request_id": pending.request_id,
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "description": "Execute command: ls",
            "risk_level": "high",
            "scope_options": ["once", "tool_session", "all_session"],
        },
    )

    # Before resolution the buffer message has no 'accepted' key.
    perm_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.PERMISSION_REQUEST]
    assert len(perm_msgs) == 1
    assert "accepted" not in perm_msgs[0].data

    # Resolve the request via the router (the path taken by the WS input handler).
    router.resolve_permission(session.id, pending.request_id, "allow", "once")

    # After resolution the buffer message must carry accepted=True.
    assert perm_msgs[0].data.get("accepted") is True


def test_resolve_permission_deny_sets_accepted_false_in_buffer(session_manager: SessionManager) -> None:
    """Denying a permission request persists accepted=False in the buffer."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    pm = PermissionManager()
    session.permission_manager = pm

    pending = pm.create_request("Write", {"file_path": "/tmp/x"})
    session.buffer.push_text(
        MessageType.PERMISSION_REQUEST,
        {
            "session_id": session.id,
            "request_id": pending.request_id,
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/x"},
            "description": "Write file: /tmp/x",
            "risk_level": "medium",
            "scope_options": ["once", "tool_session", "tool_path", "all_session"],
        },
    )

    router.resolve_permission(session.id, pending.request_id, "deny", "once")

    perm_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.PERMISSION_REQUEST]
    assert perm_msgs[0].data.get("accepted") is False


# ---------------------------------------------------------------------------
# Plan mode approval gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_interactive_response_resolves_plan_mode_event(
    session_manager: SessionManager,
) -> None:
    """send_interactive_response with 'yes' resolves a pending plan mode event as approved."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    # Simulate a pending plan mode gate (as set by _relay_claude_code_stream)
    session._plan_mode_event = asyncio.Event()
    session._plan_mode_approved = False
    session.buffer.push_text(
        MessageType.PLAN_MODE_ASK,
        {"session_id": session.id},
    )

    await router.send_interactive_response(session.id, "yes")

    assert session._plan_mode_approved is True
    assert session._plan_mode_event.is_set()

    # Buffer should have accepted=True persisted on the PLAN_MODE_ASK message
    plan_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.PLAN_MODE_ASK]
    assert len(plan_msgs) == 1
    assert plan_msgs[0].data.get("accepted") is True


@pytest.mark.asyncio
async def test_send_interactive_response_deny_plan_mode(
    session_manager: SessionManager,
) -> None:
    """send_interactive_response with 'no' resolves a pending plan mode event as denied."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    session._plan_mode_event = asyncio.Event()
    session._plan_mode_approved = False
    session.buffer.push_text(
        MessageType.PLAN_MODE_ASK,
        {"session_id": session.id},
    )

    await router.send_interactive_response(session.id, "no")

    assert session._plan_mode_approved is False
    assert session._plan_mode_event.is_set()

    plan_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.PLAN_MODE_ASK]
    assert plan_msgs[0].data.get("accepted") is False


@pytest.mark.asyncio
async def test_cancel_session_resolves_pending_plan_mode_event(
    session_manager: SessionManager,
) -> None:
    """cancel_session auto-denies any pending plan mode approval gate."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    session._plan_mode_event = asyncio.Event()
    session._plan_mode_approved = False

    await router.cancel_session(session.id)

    assert session._plan_mode_approved is False
    assert session._plan_mode_event.is_set()


@pytest.mark.asyncio
async def test_pause_session_resolves_pending_plan_mode_event(
    session_manager: SessionManager,
) -> None:
    """pause_session auto-denies any pending plan mode approval gate."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    session._plan_mode_event = asyncio.Event()
    session._plan_mode_approved = False

    await router.pause_session(session.id)

    assert session._plan_mode_approved is False
    assert session._plan_mode_event.is_set()


@pytest.mark.asyncio
async def test_relay_enter_plan_mode_blocks_and_resumes_on_approve(
    session_manager: SessionManager,
) -> None:
    """EnterPlanMode in the stream blocks until the user approves, then continues."""
    router = _make_router(session_manager)

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    enter_plan_event = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "EnterPlanMode", "input": {}}],
            },
        }
    )
    result_event = json.dumps({"type": "result", "result": "Plan complete."})
    stream = _async_stream_chunks(
        [
            ExecutionChunk(content=enter_plan_event, stream="stdout"),
            ExecutionChunk(content=result_event, stream="stdout"),
        ]
    )

    # Run relay concurrently with an approval sent after a short delay
    async def _approve_after_yield() -> None:
        # Yield control so the relay can start and hit the gate
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await router.send_interactive_response(session.id, "yes")

    await asyncio.gather(
        router._relay_claude_code_stream(session, stream),
        _approve_after_yield(),
    )

    # Session should still be active (not ended by denial)
    assert True  # relay ended normally

    # PLAN_MODE_ASK should be in the buffer, marked accepted
    plan_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.PLAN_MODE_ASK]
    assert len(plan_msgs) == 1
    assert plan_msgs[0].data.get("accepted") is True


@pytest.mark.asyncio
async def test_relay_enter_plan_mode_denied_ends_session(
    session_manager: SessionManager,
) -> None:
    """Denying plan mode ends the session with PLAN_MODE_DENIED error."""
    router = _make_router(session_manager)
    router._end_claude_code_session = AsyncMock()  # type: ignore[method-assign]

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    # Executor mock so _end_claude_code_session doesn't fail
    mock_executor = AsyncMock()
    mock_executor.stop_process = AsyncMock()
    session.claude_code_executor = mock_executor

    enter_plan_event = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "EnterPlanMode", "input": {}}],
            },
        }
    )
    # The result event should NOT be reached after a denial
    result_event = json.dumps({"type": "result", "result": "Should not reach."})
    stream = _async_stream_chunks(
        [
            ExecutionChunk(content=enter_plan_event, stream="stdout"),
            ExecutionChunk(content=result_event, stream="stdout"),
        ]
    )

    async def _deny_after_yield() -> None:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await router.send_interactive_response(session.id, "no")

    await asyncio.gather(
        router._relay_claude_code_stream(session, stream),
        _deny_after_yield(),
    )

    # PLAN_MODE_ASK must be in the buffer, marked denied
    plan_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.PLAN_MODE_ASK]
    assert len(plan_msgs) == 1
    assert plan_msgs[0].data.get("accepted") is False

    # An ERROR message with PLAN_MODE_DENIED code should be present
    error_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.ERROR]
    assert any(m.data.get("code") == "PLAN_MODE_DENIED" for m in error_msgs)

    # _end_claude_code_session should have been called
    router._end_claude_code_session.assert_awaited_once_with(session)  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_relay_exit_plan_mode_blocks_until_approval(
    session_manager: SessionManager,
) -> None:
    """ExitPlanMode relay blocks the stream until the user provides a response."""
    router = _make_router(session_manager)

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    # Mock executor so send_input doesn't fail
    mock_executor = AsyncMock()
    mock_executor.is_running = True
    mock_executor.send_input = AsyncMock()
    session.claude_code_executor = mock_executor

    exit_plan_event = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "ExitPlanMode",
                        "input": {"plan": "1. Do X\n2. Do Y"},
                    }
                ],
            },
        }
    )
    result_event = json.dumps({"type": "result", "result": "Plan ready."})
    stream = _async_stream_chunks(
        [
            ExecutionChunk(content=exit_plan_event, stream="stdout"),
            ExecutionChunk(content=result_event, stream="stdout"),
        ]
    )

    relay_done = False

    async def _track_relay() -> None:
        nonlocal relay_done
        await router._relay_claude_code_stream(session, stream)
        relay_done = True

    async def _approve_after_yield() -> None:
        # Yield control so the relay can hit the gate
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        # At this point the relay should be blocked — confirm it hasn't finished
        assert not relay_done, "Relay must not complete before approval is given"
        await router.send_interactive_response(session.id, "Looks good, proceed.", accepted=True)

    await asyncio.gather(_track_relay(), _approve_after_yield())

    # PLAN_REVIEW_ASK should be in the buffer, marked accepted
    plan_review_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.PLAN_REVIEW_ASK]
    assert len(plan_review_msgs) == 1
    assert plan_review_msgs[0].data["plan_input"] == {"plan": "1. Do X\n2. Do Y"}
    assert plan_review_msgs[0].data["session_id"] == session.id
    assert plan_review_msgs[0].data.get("accepted") is True


@pytest.mark.asyncio
async def test_relay_exit_plan_mode_approve_sends_to_stdin(
    session_manager: SessionManager,
) -> None:
    """Approving the plan review sends the approval text to Claude Code stdin."""
    router = _make_router(session_manager)

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    mock_executor = AsyncMock()
    mock_executor.is_running = True
    mock_executor.send_input = AsyncMock()
    session.claude_code_executor = mock_executor

    exit_plan_event = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "ExitPlanMode", "input": {"plan": "Step 1"}}],
            },
        }
    )
    result_event = json.dumps({"type": "result", "result": "Done."})
    stream = _async_stream_chunks(
        [
            ExecutionChunk(content=exit_plan_event, stream="stdout"),
            ExecutionChunk(content=result_event, stream="stdout"),
        ]
    )

    approval_text = "Looks good, proceed with the plan."

    async def _approve() -> None:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await router.send_interactive_response(session.id, approval_text, accepted=True)

    await asyncio.gather(router._relay_claude_code_stream(session, stream), _approve())

    # The approval text must have been forwarded to Claude Code's stdin
    mock_executor.send_input.assert_awaited_once_with(approval_text)

    # Buffer message must be marked accepted
    plan_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.PLAN_REVIEW_ASK]
    assert plan_msgs[0].data.get("accepted") is True


@pytest.mark.asyncio
async def test_relay_exit_plan_mode_reject_sends_feedback_to_stdin(
    session_manager: SessionManager,
) -> None:
    """Rejecting the plan review sends feedback text to Claude Code stdin for revision."""
    router = _make_router(session_manager)

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    mock_executor = AsyncMock()
    mock_executor.is_running = True
    mock_executor.send_input = AsyncMock()
    session.claude_code_executor = mock_executor

    exit_plan_event = json.dumps(
        {
            "type": "assistant",
            "message": {
                "content": [{"type": "tool_use", "name": "ExitPlanMode", "input": {"plan": "Step 1"}}],
            },
        }
    )
    result_event = json.dumps({"type": "result", "result": "Done."})
    stream = _async_stream_chunks(
        [
            ExecutionChunk(content=exit_plan_event, stream="stdout"),
            ExecutionChunk(content=result_event, stream="stdout"),
        ]
    )

    feedback = "Please also add error handling in step 1."

    async def _reject() -> None:
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await router.send_interactive_response(session.id, feedback, accepted=False)

    await asyncio.gather(router._relay_claude_code_stream(session, stream), _reject())

    # Feedback must have been forwarded to Claude Code's stdin
    mock_executor.send_input.assert_awaited_once_with(feedback)

    # Buffer message must be marked rejected
    plan_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.PLAN_REVIEW_ASK]
    assert plan_msgs[0].data.get("accepted") is False


@pytest.mark.asyncio
async def test_send_interactive_response_resolves_plan_review_approved(
    session_manager: SessionManager,
) -> None:
    """send_interactive_response with accepted=True resolves a pending plan review as approved."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    session._plan_review_event = asyncio.Event()
    session._plan_review_approved = False
    session._plan_review_feedback = None
    session.buffer.push_text(
        MessageType.PLAN_REVIEW_ASK,
        {"session_id": session.id, "plan_input": {"plan": "Step 1"}},
    )

    await router.send_interactive_response(session.id, "Looks good.", accepted=True)

    assert session._plan_review_approved is True
    assert session._plan_review_feedback == "Looks good."
    assert session._plan_review_event.is_set()

    plan_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.PLAN_REVIEW_ASK]
    assert len(plan_msgs) == 1
    assert plan_msgs[0].data.get("accepted") is True


@pytest.mark.asyncio
async def test_send_interactive_response_resolves_plan_review_rejected(
    session_manager: SessionManager,
) -> None:
    """send_interactive_response with accepted=False resolves a pending plan review as rejected."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    session._plan_review_event = asyncio.Event()
    session._plan_review_approved = False
    session._plan_review_feedback = None
    session.buffer.push_text(
        MessageType.PLAN_REVIEW_ASK,
        {"session_id": session.id, "plan_input": {"plan": "Step 1"}},
    )

    feedback = "Add error handling."
    await router.send_interactive_response(session.id, feedback, accepted=False)

    assert session._plan_review_approved is False
    assert session._plan_review_feedback == feedback
    assert session._plan_review_event.is_set()

    plan_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.PLAN_REVIEW_ASK]
    assert plan_msgs[0].data.get("accepted") is False


@pytest.mark.asyncio
async def test_cancel_session_resolves_pending_plan_review_event(
    session_manager: SessionManager,
) -> None:
    """cancel_session auto-denies any pending plan review gate."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    session._plan_review_event = asyncio.Event()
    session._plan_review_approved = False

    await router.cancel_session(session.id)

    assert session._plan_review_approved is False
    assert session._plan_review_event.is_set()


@pytest.mark.asyncio
async def test_pause_session_resolves_pending_plan_review_event(
    session_manager: SessionManager,
) -> None:
    """pause_session auto-denies any pending plan review gate."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    session._plan_review_event = asyncio.Event()
    session._plan_review_approved = False

    await router.pause_session(session.id)

    assert session._plan_review_approved is False
    assert session._plan_review_event.is_set()


# ---------------------------------------------------------------------------
# handle_prompt — selected_worktree_path pre-selection
# ---------------------------------------------------------------------------


async def _empty_agentic_loop(**kwargs):
    """Async generator stub that immediately terminates the agentic loop."""
    return
    yield  # pragma: no cover — makes this an async generator


@pytest.mark.asyncio
async def test_handle_prompt_applies_selected_worktree_path(
    session_manager: SessionManager,
) -> None:
    """handle_prompt stores selected_worktree_path in session metadata on the first call."""
    router = _make_router(session_manager)
    router._llm.run_agentic_loop = _empty_agentic_loop  # type: ignore[method-assign]
    router._ensure_session_row_in_db = AsyncMock()  # type: ignore[method-assign]

    worktree_path = "/repos/myproject/.worktrees/feat-abc"
    session_id = await router.handle_prompt(
        "Hello",
        selected_worktree_path=worktree_path,
    )

    session = session_manager.get_session(session_id)
    assert session is not None
    assert session.metadata.get("selected_worktree_path") == worktree_path


@pytest.mark.asyncio
async def test_handle_prompt_does_not_override_existing_worktree_path(
    session_manager: SessionManager,
) -> None:
    """handle_prompt must not clobber a worktree path that is already set on the session."""
    router = _make_router(session_manager)
    router._llm.run_agentic_loop = _empty_agentic_loop  # type: ignore[method-assign]
    router._ensure_session_row_in_db = AsyncMock()  # type: ignore[method-assign]

    original_path = "/repos/myproject/.worktrees/original"
    new_path = "/repos/myproject/.worktrees/other"

    # First call: establish the session with an initial worktree path.
    session_id = await router.handle_prompt(
        "First message",
        selected_worktree_path=original_path,
    )

    session = session_manager.get_session(session_id)
    assert session is not None
    assert session.metadata.get("selected_worktree_path") == original_path

    # Second call on the same session: a different path must NOT override the existing one.
    await router.handle_prompt(
        "Second message",
        session_id=session_id,
        selected_worktree_path=new_path,
    )

    assert session.metadata.get("selected_worktree_path") == original_path


@pytest.mark.asyncio
async def test_handle_prompt_no_worktree_path_leaves_metadata_empty(
    session_manager: SessionManager,
) -> None:
    """When no selected_worktree_path is provided, session metadata should not gain the key."""
    router = _make_router(session_manager)
    router._llm.run_agentic_loop = _empty_agentic_loop  # type: ignore[method-assign]
    router._ensure_session_row_in_db = AsyncMock()  # type: ignore[method-assign]

    session_id = await router.handle_prompt("Hello")

    session = session_manager.get_session(session_id)
    assert session is not None
    assert "selected_worktree_path" not in session.metadata


# ---------------------------------------------------------------------------
# handle_prompt — display text / #mention stripping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_prompt_strips_tool_mention_from_buffer_when_no_display_text(
    session_manager: SessionManager,
) -> None:
    """When display_text is absent, #tool routing tags must be stripped from the user buffer entry.

    Regression test for the bug where users who typed '#ClaudeCode <task>' directly
    (without using the chip) saw the literal '#ClaudeCode' prefix in rendered chat.
    """
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    # Attach a mock executor so handle_prompt takes the fast forward-to-executor path.
    mock_executor = AsyncMock()
    session.claude_code_executor = mock_executor
    with patch.object(router, "_forward_to_claude_code", new=AsyncMock()):
        await router.handle_prompt(
            "#ClaudeCode fix the login bug",
            session_id=session.id,
        )

    user_msgs = [
        m
        for m in session.buffer.text_history
        if m.message_type == MessageType.TEXT_CHUNK and m.data.get("role") == "user"
    ]
    assert len(user_msgs) == 1
    assert user_msgs[0].data["content"] == "fix the login bug"


@pytest.mark.asyncio
async def test_handle_prompt_explicit_display_text_used_verbatim(
    session_manager: SessionManager,
) -> None:
    """When display_text is provided it is stored as-is — no extra stripping."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    mock_executor = AsyncMock()
    session.claude_code_executor = mock_executor
    with patch.object(router, "_forward_to_claude_code", new=AsyncMock()):
        await router.handle_prompt(
            "#ClaudeCode fix the login bug",
            session_id=session.id,
            display_text="fix the login bug",
        )

    user_msgs = [
        m
        for m in session.buffer.text_history
        if m.message_type == MessageType.TEXT_CHUNK and m.data.get("role") == "user"
    ]
    assert len(user_msgs) == 1
    assert user_msgs[0].data["content"] == "fix the login bug"


@pytest.mark.asyncio
async def test_handle_prompt_empty_display_text_preserved(
    session_manager: SessionManager,
) -> None:
    """An explicit empty display_text (chip + empty input) must be kept as '', not replaced by text."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    mock_executor = AsyncMock()
    session.claude_code_executor = mock_executor
    with patch.object(router, "_forward_to_claude_code", new=AsyncMock()):
        await router.handle_prompt(
            "#ClaudeCode",
            session_id=session.id,
            display_text="",
        )

    user_msgs = [
        m
        for m in session.buffer.text_history
        if m.message_type == MessageType.TEXT_CHUNK and m.data.get("role") == "user"
    ]
    assert len(user_msgs) == 1
    assert user_msgs[0].data["content"] == ""


@pytest.mark.asyncio
async def test_handle_prompt_inline_mention_stripped_mid_message(
    session_manager: SessionManager,
) -> None:
    """#mention anywhere in the text (not just prefix) is stripped from display."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    mock_executor = AsyncMock()
    session.claude_code_executor = mock_executor
    with patch.object(router, "_forward_to_claude_code", new=AsyncMock()):
        await router.handle_prompt(
            "fix the login bug #ClaudeCode",
            session_id=session.id,
        )

    user_msgs = [
        m
        for m in session.buffer.text_history
        if m.message_type == MessageType.TEXT_CHUNK and m.data.get("role") == "user"
    ]
    assert len(user_msgs) == 1
    assert user_msgs[0].data["content"] == "fix the login bug"


# ---------------------------------------------------------------------------
# OpenCode lifecycle tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_interrupt_subprocess_with_opencode_executor(session_manager: SessionManager) -> None:
    """Interrupting a session with a running OpenCode executor cancels it and pushes AGENT_GROUP_END."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    mock_executor = AsyncMock()
    mock_executor.cancel = AsyncMock()
    session.opencode_executor = mock_executor

    task_future: asyncio.Future[None] = asyncio.get_event_loop().create_future()
    task = asyncio.ensure_future(task_future)
    session._opencode_stream_task = task  # type: ignore[assignment]

    result = await router.interrupt_subprocess(session.id)

    assert result.status == SessionStatus.ACTIVE
    mock_executor.cancel.assert_awaited_once()
    assert session.opencode_executor is None
    assert session._opencode_stream_task is None

    group_end_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.AGENT_GROUP_END]
    assert len(group_end_msgs) == 1
    text_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.TEXT_CHUNK]
    assert any("[Subprocess interrupted by user]" in m.data.get("content", "") for m in text_msgs)


@pytest.mark.asyncio
async def test_cancel_session_with_opencode_executor(session_manager: SessionManager) -> None:
    """Cancelling a session with a running OpenCode executor kills it and pushes AGENT_GROUP_END."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    mock_executor = AsyncMock()
    mock_executor.cancel = AsyncMock()
    session.opencode_executor = mock_executor

    task_future: asyncio.Future[None] = asyncio.get_event_loop().create_future()
    task = asyncio.ensure_future(task_future)
    session._opencode_stream_task = task  # type: ignore[assignment]

    result = await router.cancel_session(session.id)

    assert result.status == SessionStatus.CANCELLED
    mock_executor.cancel.assert_awaited_once()
    assert session.opencode_executor is None
    assert session._opencode_stream_task is None

    group_end_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.AGENT_GROUP_END]
    assert len(group_end_msgs) == 1


@pytest.mark.asyncio
async def test_pause_session_with_opencode_executor(session_manager: SessionManager) -> None:
    """Pausing a session with a running OpenCode executor should kill it and emit AGENT_GROUP_END."""
    router = _make_router(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    mock_executor = AsyncMock()
    mock_executor.cancel = AsyncMock()
    session.opencode_executor = mock_executor

    task_future: asyncio.Future[None] = asyncio.get_event_loop().create_future()
    task = asyncio.ensure_future(task_future)
    session._opencode_stream_task = task  # type: ignore[assignment]

    result = await router.pause_session(session.id)

    assert result.status == SessionStatus.PAUSED
    mock_executor.cancel.assert_awaited_once()
    assert session.opencode_executor is None
    assert session._opencode_stream_task is None

    group_end_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.AGENT_GROUP_END]
    assert len(group_end_msgs) == 1
    paused_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_PAUSED]
    assert len(paused_msgs) == 1
    assert paused_msgs[0].data["claude_code_interrupted"] is True
    assert group_end_msgs[0].sequence < paused_msgs[0].sequence


@pytest.mark.asyncio
async def test_resume_session_reconstructs_opencode_executor(session_manager: SessionManager) -> None:
    """After pausing, resuming a session with opencode metadata should reconstruct the executor."""
    router = _make_router_with_real_registry(session_manager)
    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    # Simulate metadata written by _start_opencode
    session.metadata["opencode_session_id"] = "oc-sess-abc"
    session.metadata["opencode_tool_name"] = "opencode"
    session.metadata["opencode_parameters"] = {"prompt": "write tests", "working_directory": "/tmp"}

    session.pause()

    result = await router.resume_session(session.id)

    assert result.status == SessionStatus.ACTIVE
    assert result.opencode_executor is not None
    assert result.opencode_executor.opencode_session_id == "oc-sess-abc"


class TestBuildClaudeCodeExtraEnv:
    """Tests for ClaudeCodeAgentMixin._build_claude_code_extra_env."""

    def test_undercover_not_set_by_default(self, session_manager: SessionManager):
        """CLAUDE_CODE_UNDERCOVER is absent when no tool_settings are configured."""
        router = _make_router(session_manager)
        env = router._build_claude_code_extra_env()
        assert "CLAUDE_CODE_UNDERCOVER" not in env

    def test_undercover_set_when_enabled(self, session_manager: SessionManager):
        """CLAUDE_CODE_UNDERCOVER=1 is set when the undercover setting is enabled."""
        mock_ts = MagicMock()
        mock_ts.get_settings.return_value = {"undercover": True}
        mock_ts.get_config_dir.return_value = MagicMock()
        router = _make_router(session_manager)
        router._tool_settings = mock_ts
        env = router._build_claude_code_extra_env()
        assert env.get("CLAUDE_CODE_UNDERCOVER") == "1"

    def test_undercover_not_set_when_disabled(self, session_manager: SessionManager):
        """CLAUDE_CODE_UNDERCOVER is absent when the undercover setting is False."""
        mock_ts = MagicMock()
        mock_ts.get_settings.return_value = {"undercover": False}
        mock_ts.get_config_dir.return_value = MagicMock()
        router = _make_router(session_manager)
        router._tool_settings = mock_ts
        env = router._build_claude_code_extra_env()
        assert "CLAUDE_CODE_UNDERCOVER" not in env

    def test_undercover_not_set_when_key_missing_from_config(self, session_manager: SessionManager):
        """CLAUDE_CODE_UNDERCOVER is absent when config exists but lacks the key."""
        mock_ts = MagicMock()
        mock_ts.get_settings.return_value = {"provider": "anthropic"}
        mock_ts.get_config_dir.return_value = MagicMock()
        router = _make_router(session_manager)
        router._tool_settings = mock_ts
        env = router._build_claude_code_extra_env()
        assert "CLAUDE_CODE_UNDERCOVER" not in env


# --- New-session structured prompt format tests ---


def _make_agent_tool_def(executor: str = "claude_code", name: str = "claude_code") -> MagicMock:
    """Return a mock ToolDefinition for a coding-agent executor."""
    td = MagicMock()
    td.executor = executor
    td.name = name
    td.display_name = name
    td.executor_config = {executor: {}}
    return td


class TestNewSessionStructuredPrompt:
    """_execute_tool must format the agent prompt BEFORE pushing AGENT_SESSION_START.

    For new sessions the first coding-agent invocation goes through _execute_tool,
    so both the buffer banner and the executor must receive the structured
    Task / Description / Additional Content format.
    """

    @pytest.mark.asyncio
    async def test_agent_session_start_has_structured_prompt(self, session_manager: SessionManager) -> None:
        """AGENT_SESSION_START prompt must be structured with Task + Description sections."""
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()

        mock_td = _make_agent_tool_def("claude_code", "claude_code")
        router._tool_registry.get.return_value = mock_td  # type: ignore[attr-defined]

        tool_call = ToolCallRequest(
            tool_use_id="test-1",
            tool_name="claude_code",
            tool_input={"prompt": "Fix the login bug\nThe auth token is never refreshed."},
        )
        with patch.object(router, "_start_claude_code", AsyncMock(return_value="started")):
            await router._execute_tool(session, tool_call)

        start_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.AGENT_SESSION_START]
        assert len(start_msgs) == 1
        prompt = start_msgs[0].data["prompt"]
        assert "## Task" in prompt
        assert "## Description" in prompt

    @pytest.mark.asyncio
    async def test_code_blocks_preserved_in_additional_content(self, session_manager: SessionManager) -> None:
        """Code blocks from the raw user message are extracted into Additional Content."""
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()

        mock_td = _make_agent_tool_def("claude_code", "claude_code")
        router._tool_registry.get.return_value = mock_td  # type: ignore[attr-defined]

        tool_call = ToolCallRequest(
            tool_use_id="test-2",
            tool_name="claude_code",
            tool_input={
                "prompt": "Fix this function\n\n```python\ndef broken():\n    return None\n```",
            },
        )
        with patch.object(router, "_start_claude_code", AsyncMock(return_value="started")):
            await router._execute_tool(session, tool_call)

        start_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.AGENT_SESSION_START]
        prompt = start_msgs[0].data["prompt"]
        content_idx = prompt.index("## Additional Content")
        additional = prompt[content_idx:]
        assert "```python" in additional
        assert "def broken():" in additional

    @pytest.mark.asyncio
    async def test_executor_receives_structured_prompt(self, session_manager: SessionManager) -> None:
        """The tool_input passed to the executor must also carry the formatted prompt."""
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()

        mock_td = _make_agent_tool_def("claude_code", "claude_code")
        router._tool_registry.get.return_value = mock_td  # type: ignore[attr-defined]

        captured: dict[str, object] = {}

        async def _capture_start(s, td, tc):  # type: ignore[no-untyped-def]
            captured["prompt"] = tc.tool_input.get("prompt", "")
            return "started"

        tool_call = ToolCallRequest(
            tool_use_id="test-3",
            tool_name="claude_code",
            tool_input={"prompt": "Refactor the parser\nUse AST instead of regex."},
        )
        with patch.object(router, "_start_claude_code", _capture_start):
            await router._execute_tool(session, tool_call)

        prompt = captured["prompt"]
        assert "## Task" in prompt
        assert "## Description" in prompt

    @pytest.mark.asyncio
    async def test_prompt_consistent_between_banner_and_executor(self, session_manager: SessionManager) -> None:
        """The prompt in AGENT_SESSION_START must match the prompt the executor receives."""
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()

        mock_td = _make_agent_tool_def("claude_code", "claude_code")
        router._tool_registry.get.return_value = mock_td  # type: ignore[attr-defined]

        captured: dict[str, object] = {}

        async def _capture_start(s, td, tc):  # type: ignore[no-untyped-def]
            captured["prompt"] = tc.tool_input.get("prompt", "")
            return "started"

        tool_call = ToolCallRequest(
            tool_use_id="test-4",
            tool_name="claude_code",
            tool_input={"prompt": "Add unit tests\nCover all edge cases."},
        )
        with patch.object(router, "_start_claude_code", _capture_start):
            await router._execute_tool(session, tool_call)

        start_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.AGENT_SESSION_START]
        banner_prompt = start_msgs[0].data["prompt"]
        executor_prompt = str(captured["prompt"])
        assert banner_prompt == executor_prompt

    @pytest.mark.asyncio
    async def test_codex_agent_session_start_has_structured_prompt(self, session_manager: SessionManager) -> None:
        """Same formatting applies to Codex executor sessions."""
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()

        mock_td = _make_agent_tool_def("codex", "codex")
        router._tool_registry.get.return_value = mock_td  # type: ignore[attr-defined]

        tool_call = ToolCallRequest(
            tool_use_id="test-5",
            tool_name="codex",
            tool_input={"prompt": "Implement feature X\n\n```typescript\ntype X = string;\n```"},
        )
        with patch.object(router, "_start_codex", AsyncMock(return_value="started")):
            await router._execute_tool(session, tool_call)

        start_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.AGENT_SESSION_START]
        assert len(start_msgs) == 1
        prompt = start_msgs[0].data["prompt"]
        assert "## Task" in prompt
        content_idx = prompt.index("## Additional Content")
        assert "```typescript" in prompt[content_idx:]

    @pytest.mark.asyncio
    async def test_empty_prompt_not_formatted(self, session_manager: SessionManager) -> None:
        """An empty prompt is left unchanged (no fallback injection via _execute_tool)."""
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()

        mock_td = _make_agent_tool_def("claude_code", "claude_code")
        router._tool_registry.get.return_value = mock_td  # type: ignore[attr-defined]

        tool_call = ToolCallRequest(
            tool_use_id="test-6",
            tool_name="claude_code",
            tool_input={"prompt": ""},
        )
        with patch.object(router, "_start_claude_code", AsyncMock(return_value="started")):
            await router._execute_tool(session, tool_call)

        start_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.AGENT_SESSION_START]
        assert len(start_msgs) == 1
        assert start_msgs[0].data["prompt"] == ""


class TestPendingUserCodeBlocks:
    """Verify that fenced code blocks in the user's verbatim message reach the
    agent's ``Additional Content`` section even when the LLM omits them when
    constructing the tool call ``prompt``.
    """

    @pytest.mark.asyncio
    async def test_user_code_block_merged_when_llm_omits_it(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        # Simulate handle_prompt having captured the user's verbatim code block
        # before invoking the agentic loop.
        session._pending_user_code_blocks = ["```python\ndef broken():\n    return None\n```"]

        mock_td = _make_agent_tool_def("claude_code", "claude_code")
        router._tool_registry.get.return_value = mock_td  # type: ignore[attr-defined]

        # The LLM dropped the code block — only a paraphrased task description
        # is in the tool call's prompt argument.
        tool_call = ToolCallRequest(
            tool_use_id="codeblk-1",
            tool_name="claude_code",
            tool_input={"prompt": "Fix the broken helper function."},
        )
        with patch.object(router, "_start_claude_code", AsyncMock(return_value="started")):
            await router._execute_tool(session, tool_call)

        forwarded = tool_call.tool_input["prompt"]
        assert "## Additional Content" in forwarded
        content_idx = forwarded.index("## Additional Content")
        assert "```python" in forwarded[content_idx:]
        assert "def broken():" in forwarded[content_idx:]
        # Pending list is consumed so blocks don't leak into the next agent call.
        assert session._pending_user_code_blocks == []

    @pytest.mark.asyncio
    async def test_user_code_block_not_duplicated_when_llm_kept_it(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        block = "```python\ndef broken():\n    return None\n```"
        session._pending_user_code_blocks = [block]

        mock_td = _make_agent_tool_def("claude_code", "claude_code")
        router._tool_registry.get.return_value = mock_td  # type: ignore[attr-defined]

        tool_call = ToolCallRequest(
            tool_use_id="codeblk-2",
            tool_name="claude_code",
            tool_input={"prompt": f"Fix this function\n\n{block}"},
        )
        with patch.object(router, "_start_claude_code", AsyncMock(return_value="started")):
            await router._execute_tool(session, tool_call)

        forwarded = tool_call.tool_input["prompt"]
        # Block appears exactly once in Additional Content.
        assert forwarded.count("def broken():") == 1

    @pytest.mark.asyncio
    async def test_pending_blocks_consumed_only_by_agent_executor(self, session_manager: SessionManager) -> None:
        router = _make_router(session_manager)
        session = session_manager.create_session(SessionType.CONVERSATIONAL)
        session.set_active()
        session._pending_user_code_blocks = ["```python\nprint('hi')\n```"]

        # Non-agent executor (e.g. shell): pending blocks must be left alone so
        # a subsequent agent call in the same turn still picks them up.
        mock_td = _make_agent_tool_def("shell", "shell_tool")
        router._tool_registry.get.return_value = mock_td  # type: ignore[attr-defined]

        tool_call = ToolCallRequest(
            tool_use_id="codeblk-3",
            tool_name="shell_tool",
            tool_input={"command": "echo hi"},
        )
        # Stub out the shell executor lookup so we never actually run anything.
        mock_executor = MagicMock()
        mock_executor.execute = AsyncMock(return_value=MagicMock(exit_code=0, output="hi", error=""))
        with patch.object(router, "_get_executor", return_value=mock_executor):
            await router._execute_tool(session, tool_call)

        assert session._pending_user_code_blocks == ["```python\nprint('hi')\n```"]
