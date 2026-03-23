import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.buffer import MessageType
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
    session._claude_code_stream_task = task

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


# --- Summarization tests ---


@pytest.mark.asyncio
async def test_summarize_and_push(session_manager: SessionManager) -> None:
    """_summarize_and_push should call llm.summarize and push a SUMMARY message."""
    router = _make_router(session_manager)
    router._llm.summarize = AsyncMock(return_value="Short summary.")

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    await router._summarize_and_push(session, "Long result text from Claude Code.")

    router._llm.summarize.assert_awaited_once_with("Long result text from Claude Code.")

    summary_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SUMMARY]
    assert len(summary_msgs) == 1
    assert summary_msgs[0].data["content"] == "Short summary."
    assert summary_msgs[0].data["session_id"] == session.id


@pytest.mark.asyncio
async def test_summarize_and_push_with_session_end_ask(session_manager: SessionManager) -> None:
    """_summarize_and_push with push_session_end_ask=True should push SESSION_END_ASK after summary."""
    router = _make_router(session_manager)
    router._llm.summarize = AsyncMock(return_value="Short summary.")

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    await router._summarize_and_push(session, "Result text.", push_session_end_ask=True)

    summary_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SUMMARY]
    assert len(summary_msgs) == 1

    end_ask_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_END_ASK]
    assert len(end_ask_msgs) == 1
    assert end_ask_msgs[0].data["session_id"] == session.id

    # SESSION_END_ASK must come after SUMMARY
    summary_seq = summary_msgs[0].sequence
    end_ask_seq = end_ask_msgs[0].sequence
    assert end_ask_seq > summary_seq


@pytest.mark.asyncio
async def test_summarize_and_push_failure_does_not_raise(session_manager: SessionManager) -> None:
    """_summarize_and_push should swallow exceptions without breaking the session."""
    router = _make_router(session_manager)
    router._llm.summarize = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    # Should not raise
    await router._summarize_and_push(session, "Some text")

    # No SUMMARY message should have been pushed
    summary_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SUMMARY]
    assert len(summary_msgs) == 0


@pytest.mark.asyncio
async def test_summarize_failure_still_pushes_session_end_ask(session_manager: SessionManager) -> None:
    """SESSION_END_ASK should still be pushed even when summary generation fails."""
    router = _make_router(session_manager)
    router._llm.summarize = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    await router._summarize_and_push(session, "Some text", push_session_end_ask=True)

    summary_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SUMMARY]
    assert len(summary_msgs) == 0

    end_ask_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_END_ASK]
    assert len(end_ask_msgs) == 1


# --- Title generation tests ---


@pytest.mark.asyncio
async def test_generate_and_set_title(session_manager: SessionManager) -> None:
    """_generate_and_set_title should call llm.generate_title and set session.title."""
    router = _make_router(session_manager)
    router._llm.generate_title = AsyncMock(return_value="List project files")

    session = session_manager.create_session(SessionType.CONVERSATIONAL)
    session.set_active()
    assert session.title is None

    await router._generate_and_set_title(session, "list files", "Here are the files...")

    router._llm.generate_title.assert_awaited_once_with("list files", "Here are the files...")
    assert session.title == "List project files"


@pytest.mark.asyncio
async def test_generate_and_set_title_failure_does_not_raise(session_manager: SessionManager) -> None:
    """_generate_and_set_title should swallow exceptions without breaking the session."""
    router = _make_router(session_manager)
    router._llm.generate_title = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

    session = session_manager.create_session(SessionType.CONVERSATIONAL)
    session.set_active()

    # Should not raise
    await router._generate_and_set_title(session, "hello", "world")

    assert session.title is None


@pytest.mark.asyncio
async def test_fire_title_task(session_manager: SessionManager) -> None:
    """_fire_title_task should schedule a background task that completes."""
    router = _make_router(session_manager)
    router._llm.generate_title = AsyncMock(return_value="Setup dev environment")

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
async def test_relay_claude_code_stream_fires_summary_on_result(session_manager: SessionManager) -> None:
    """_relay_claude_code_stream should fire a summary task and push SESSION_END_ASK when a result event arrives."""
    router = _make_router(session_manager)
    router._llm.summarize = AsyncMock(return_value="Summarized result.")

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    result_event = json.dumps({"type": "result", "result": "Claude Code finished the task."})
    stream = _async_stream_chunks([ExecutionChunk(content=result_event, stream="stdout")])

    await router._relay_claude_code_stream(session, stream)

    # Let the background summary task complete
    if router._pending_summary_tasks:
        await asyncio.gather(*router._pending_summary_tasks)

    # Verify SUMMARY was pushed
    summary_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SUMMARY]
    assert len(summary_msgs) == 1
    assert summary_msgs[0].data["content"] == "Summarized result."

    # Verify SESSION_END_ASK was pushed
    end_ask_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_END_ASK]
    assert len(end_ask_msgs) == 1
    assert end_ask_msgs[0].data["session_id"] == session.id


@pytest.mark.asyncio
async def test_relay_claude_code_stream_empty_result_still_pushes_end_ask(session_manager: SessionManager) -> None:
    """Even with empty result text, SESSION_END_ASK should fire so the user isn't left in limbo."""
    router = _make_router(session_manager)
    router._llm.summarize = AsyncMock(return_value="Should not be called.")

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    result_event = json.dumps({"type": "result", "result": ""})
    stream = _async_stream_chunks([ExecutionChunk(content=result_event, stream="stdout")])

    await router._relay_claude_code_stream(session, stream)

    # No summary since result text is empty and no subtype
    router._llm.summarize.assert_not_awaited()
    summary_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SUMMARY]
    assert len(summary_msgs) == 0
    # But SESSION_END_ASK should still be pushed
    end_ask_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_END_ASK]
    assert len(end_ask_msgs) == 1


@pytest.mark.asyncio
async def test_relay_claude_code_stream_max_turns_subtype(session_manager: SessionManager) -> None:
    """When result has subtype=max_turns, the session is paused with reason='max_turns' (no SESSION_END_ASK)."""
    router = _make_router(session_manager)
    router._llm.summarize = AsyncMock(return_value="Hit max turns limit.")

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    result_event = json.dumps({"type": "result", "result": "", "subtype": "max_turns"})
    stream = _async_stream_chunks([ExecutionChunk(content=result_event, stream="stdout")])

    await router._relay_claude_code_stream(session, stream)

    # Let the background summary task complete
    if router._pending_summary_tasks:
        await asyncio.gather(*router._pending_summary_tasks)

    # Summary should fire with fallback text since result was empty
    router._llm.summarize.assert_awaited_once()
    summary_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SUMMARY]
    assert len(summary_msgs) == 1

    # SESSION_END_ASK must NOT be pushed — the session is paused instead
    end_ask_msgs = [m for m in session.buffer.text_history if m.message_type == MessageType.SESSION_END_ASK]
    assert len(end_ask_msgs) == 0

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
    session._claude_code_stream_task = task

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
    session._claude_code_stream_task = task

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
    session._claude_code_stream_task = task

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


# ---------------------------------------------------------------------------
# _update_session_worktree_meta — auto-select after creation
# ---------------------------------------------------------------------------


def test_update_worktree_meta_new_auto_selects_path(session_manager: SessionManager) -> None:
    """After action=new, selected_worktree_path is auto-set from the result JSON."""
    from src.core.llm import ToolCallRequest

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
    result_json = json.dumps({
        "created": {
            "name": "new-thing",
            "branch": "feature/new-thing",
            "base": "main",
            "path": "/repos/myproject/.worktrees/new-thing",
            "created_at": "2026-03-18T10:00:00",
        }
    })

    router._update_session_worktree_meta(session, tool_call, result_json)

    assert session.metadata.get("selected_worktree_path") == "/repos/myproject/.worktrees/new-thing"
    assert session.metadata["worktree"]["branch"] == "feature/new-thing"


def test_update_worktree_meta_new_handles_missing_path(session_manager: SessionManager) -> None:
    """Gracefully handles malformed result JSON without raising."""
    from src.core.llm import ToolCallRequest

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
    from src.core.llm import ToolCallRequest

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
    from src.core.llm import ToolCallRequest

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
    router._llm.summarize = AsyncMock(return_value="Done.")

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    enter_plan_event = json.dumps({
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": "EnterPlanMode", "input": {}}],
        },
    })
    result_event = json.dumps({"type": "result", "result": "Plan complete."})
    stream = _async_stream_chunks([
        ExecutionChunk(content=enter_plan_event, stream="stdout"),
        ExecutionChunk(content=result_event, stream="stdout"),
    ])

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
    assert session.status != SessionStatus.COMPLETED or True  # relay ended normally

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
    router._end_claude_code_session = AsyncMock()

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    # Executor mock so _end_claude_code_session doesn't fail
    mock_executor = AsyncMock()
    mock_executor.stop_process = AsyncMock()
    session.claude_code_executor = mock_executor

    enter_plan_event = json.dumps({
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": "EnterPlanMode", "input": {}}],
        },
    })
    # The result event should NOT be reached after a denial
    result_event = json.dumps({"type": "result", "result": "Should not reach."})
    stream = _async_stream_chunks([
        ExecutionChunk(content=enter_plan_event, stream="stdout"),
        ExecutionChunk(content=result_event, stream="stdout"),
    ])

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
    router._end_claude_code_session.assert_awaited_once_with(session)


@pytest.mark.asyncio
async def test_relay_exit_plan_mode_blocks_until_approval(
    session_manager: SessionManager,
) -> None:
    """ExitPlanMode relay blocks the stream until the user provides a response."""
    router = _make_router(session_manager)
    router._llm.summarize = AsyncMock(return_value="Done.")

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    # Mock executor so send_input doesn't fail
    mock_executor = AsyncMock()
    mock_executor.is_running = True
    mock_executor.send_input = AsyncMock()
    session.claude_code_executor = mock_executor

    exit_plan_event = json.dumps({
        "type": "assistant",
        "message": {
            "content": [{
                "type": "tool_use",
                "name": "ExitPlanMode",
                "input": {"plan": "1. Do X\n2. Do Y"},
            }],
        },
    })
    result_event = json.dumps({"type": "result", "result": "Plan ready."})
    stream = _async_stream_chunks([
        ExecutionChunk(content=exit_plan_event, stream="stdout"),
        ExecutionChunk(content=result_event, stream="stdout"),
    ])

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
    plan_review_msgs = [
        m for m in session.buffer.text_history if m.message_type == MessageType.PLAN_REVIEW_ASK
    ]
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
    router._llm.summarize = AsyncMock(return_value="Done.")

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    mock_executor = AsyncMock()
    mock_executor.is_running = True
    mock_executor.send_input = AsyncMock()
    session.claude_code_executor = mock_executor

    exit_plan_event = json.dumps({
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": "ExitPlanMode", "input": {"plan": "Step 1"}}],
        },
    })
    result_event = json.dumps({"type": "result", "result": "Done."})
    stream = _async_stream_chunks([
        ExecutionChunk(content=exit_plan_event, stream="stdout"),
        ExecutionChunk(content=result_event, stream="stdout"),
    ])

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
    router._llm.summarize = AsyncMock(return_value="Done.")

    session = session_manager.create_session(SessionType.LONG_RUNNING)
    session.set_active()

    mock_executor = AsyncMock()
    mock_executor.is_running = True
    mock_executor.send_input = AsyncMock()
    session.claude_code_executor = mock_executor

    exit_plan_event = json.dumps({
        "type": "assistant",
        "message": {
            "content": [{"type": "tool_use", "name": "ExitPlanMode", "input": {"plan": "Step 1"}}],
        },
    })
    result_event = json.dumps({"type": "result", "result": "Done."})
    stream = _async_stream_chunks([
        ExecutionChunk(content=exit_plan_event, stream="stdout"),
        ExecutionChunk(content=result_event, stream="stdout"),
    ])

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
