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
