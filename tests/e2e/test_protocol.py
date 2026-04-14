"""Backend WebSocket e2e tests — protocol contract from a user's perspective.

Each test class represents one user-facing scenario.  The mock LLM is
scripted per class; the real session manager, buffer, and tool executors
run end-to-end so these tests catch regressions in the full pipeline.

Markers
-------
All tests are tagged ``@pytest.mark.e2e``.  Run only e2e tests with::

    pytest -m e2e tests/e2e/

Protocol under test
-------------------
Input channel  : /ws/input/text  (send prompts and control messages)
Output channel : /ws/output/text  (subscribe + drain streamed output)
"""

from __future__ import annotations

import pytest

from src.core.llm import StreamDone, TextChunk, ToolCallRequest
from src.core.session import SessionManager, SessionStatus
from tests.e2e.ws_helpers import (
    drain_output,
    find_all,
    find_msg,
    msg_types,
    send_control,
    send_prompt,
)

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# 1. Text-only happy path
#    User sends a message; assistant replies with plain text.
# ---------------------------------------------------------------------------


class TestTextResponseHappyPath:
    """Prompt → text chunks appear on output channel → session_end_ask fires."""

    @pytest.fixture
    def client(self, make_e2e_client):
        return make_e2e_client(
            [
                [TextChunk(content="Hello from mock! [SessionEndAsk]"), StreamDone(stop_reason="end_turn")],
            ]
        )

    def test_ack_contains_session_id(self, client):
        with client.websocket_connect("/ws/input/text?api_key=test-api-key") as ws:
            ws.send_json({"type": "prompt", "text": "hi"})
            ack = ws.receive_json()
        assert ack["type"] == "ack"
        assert isinstance(ack["session_id"], str) and len(ack["session_id"]) > 0

    def test_text_chunk_appears_in_output(self, client):
        session_id = send_prompt(client, "hello")
        msgs = drain_output(client, session_id)

        text_chunks = find_all(msgs, "text_chunk")
        assert text_chunks, "Expected at least one text_chunk"
        full_text = "".join(m.get("content", "") for m in text_chunks)
        assert "Hello from mock" in full_text

    def test_session_end_ask_fires_after_response(self, client):
        session_id = send_prompt(client, "hello")
        msgs = drain_output(client, session_id)

        assert find_msg(msgs, "session_end_ask") is not None, f"session_end_ask not found in: {msg_types(msgs)}"

    def test_output_messages_carry_session_id(self, client):
        session_id = send_prompt(client, "hello")
        msgs = drain_output(client, session_id)

        for msg in msgs:
            if msg.get("type") in {"text_chunk", "session_end_ask"}:
                assert msg.get("session_id") == session_id, f"Wrong session_id on {msg['type']}: {msg}"


# ---------------------------------------------------------------------------
# 2. Tool use flow
#    User prompt → LLM calls shell_exec → tool output streams → final reply.
# ---------------------------------------------------------------------------


class TestToolUseFlow:
    """Prompt → tool_start → tool_output → follow-up text → session_end_ask."""

    @pytest.fixture
    def client(self, make_e2e_client):
        return make_e2e_client(
            [
                # Turn 1: LLM decides to run shell_exec
                [
                    TextChunk(content="Let me run that for you."),
                    ToolCallRequest(
                        tool_use_id="toolu_e2e_01",
                        tool_name="shell_exec",
                        tool_input={"command": "echo e2e_tool_test"},
                    ),
                    StreamDone(stop_reason="tool_use"),
                ],
                # Turn 2: LLM wraps up after seeing tool result
                [
                    TextChunk(content="Done! [SessionEndAsk]"),
                    StreamDone(stop_reason="end_turn"),
                ],
            ]
        )

    def test_tool_start_message_appears(self, client):
        session_id = send_prompt(client, "run something")
        msgs = drain_output(client, session_id)

        tool_start = find_msg(msgs, "tool_start")
        assert tool_start is not None, f"No tool_start in: {msg_types(msgs)}"
        assert tool_start["tool_name"] == "shell_exec"

    def test_tool_output_contains_command_result(self, client):
        session_id = send_prompt(client, "run something")
        msgs = drain_output(client, session_id)

        tool_outputs = find_all(msgs, "tool_output")
        assert tool_outputs, f"No tool_output in: {msg_types(msgs)}"
        combined = "".join(m.get("content", "") for m in tool_outputs)
        assert "e2e_tool_test" in combined, f"Expected 'e2e_tool_test' in tool output, got: {combined!r}"

    def test_follow_up_text_chunk_appears(self, client):
        session_id = send_prompt(client, "run something")
        msgs = drain_output(client, session_id)

        # At least one text_chunk after a tool_output
        tool_output_idx = next((i for i, m in enumerate(msgs) if m.get("type") == "tool_output"), -1)
        later_text = [m for m in msgs[tool_output_idx + 1 :] if m.get("type") == "text_chunk"]
        assert later_text, "Expected text_chunk after tool_output"

    def test_session_end_ask_fires_after_tool_flow(self, client):
        session_id = send_prompt(client, "run something")
        msgs = drain_output(client, session_id)

        assert find_msg(msgs, "session_end_ask") is not None, f"session_end_ask not found in: {msg_types(msgs)}"

    def test_message_order_is_correct(self, client):
        session_id = send_prompt(client, "run something")
        msgs = drain_output(client, session_id)

        types = msg_types(msgs)
        # tool_start must precede tool_output, which must precede session_end_ask
        assert "tool_start" in types
        assert "tool_output" in types
        assert "session_end_ask" in types

        tool_start_idx = types.index("tool_start")
        tool_output_idx = types.index("tool_output")
        session_end_ask_idx = types.index("session_end_ask")

        assert tool_start_idx < tool_output_idx < session_end_ask_idx, f"Unexpected message order: {types}"


# ---------------------------------------------------------------------------
# 3. Multi-turn conversation
#    User sends a second message to the same session; second response arrives.
# ---------------------------------------------------------------------------


class TestMultiTurnConversation:
    """Send → reply → send again → second reply — both appear on output."""

    @pytest.fixture
    def client(self, make_e2e_client):
        return make_e2e_client(
            [
                # Turn 1: assistant replies without ending
                [TextChunk(content="First response."), StreamDone(stop_reason="end_turn")],
                # Turn 2: assistant ends
                [TextChunk(content="Second response. [SessionEndAsk]"), StreamDone(stop_reason="end_turn")],
            ]
        )

    def test_second_response_arrives(self, client):
        # First prompt — no SessionEndAsk in turn 1, so drain until first text_chunk
        session_id = send_prompt(client, "first message")

        # Second prompt to same session; drain until session_end_ask from turn 2
        send_prompt(client, "second message", session_id=session_id)
        msgs = drain_output(client, session_id)

        text_chunks = find_all(msgs, "text_chunk")
        combined = "".join(m.get("content", "") for m in text_chunks)
        assert "Second response" in combined, f"Expected 'Second response' in combined text: {combined!r}"

    def test_session_id_is_same_across_turns(self, client):
        session_id = send_prompt(client, "first message")
        second_ack_id = send_prompt(client, "second message", session_id=session_id)
        assert session_id == second_ack_id

    def test_session_end_ask_only_after_second_turn(self, client):
        session_id = send_prompt(client, "first message")
        send_prompt(client, "second message", session_id=session_id)
        msgs = drain_output(client, session_id)

        assert find_msg(msgs, "session_end_ask") is not None


# ---------------------------------------------------------------------------
# 4. Session end — user confirms
#    After session_end_ask, user sends end_session → session_end arrives.
# ---------------------------------------------------------------------------


class TestSessionEndConfirm:
    """session_end_ask → end_session → session_end with status completed."""

    @pytest.fixture
    def client(self, make_e2e_client):
        return make_e2e_client(
            [
                [TextChunk(content="Done! [SessionEndAsk]"), StreamDone(stop_reason="end_turn")],
            ]
        )

    def test_session_end_message_arrives_after_end_session(self, client):
        session_id = send_prompt(client, "do something")
        # Drain until session_end_ask
        msgs_before = drain_output(client, session_id)
        assert find_msg(msgs_before, "session_end_ask") is not None

        # User confirms end
        send_control(client, {"type": "end_session", "session_id": session_id})

        # Drain for session_end — use longer settle since end_session fires archiving
        msgs_after = drain_output(
            client,
            session_id,
            stop_types=frozenset({"session_end"}),
        )
        assert find_msg(msgs_after, "session_end") is not None, f"session_end not found in: {msg_types(msgs_after)}"

    def test_session_end_has_correct_session_id(self, client):
        session_id = send_prompt(client, "do something")
        drain_output(client, session_id)

        send_control(client, {"type": "end_session", "session_id": session_id})
        msgs = drain_output(client, session_id, stop_types=frozenset({"session_end"}))

        session_end = find_msg(msgs, "session_end")
        assert session_end is not None
        assert session_end.get("session_id") == session_id


# ---------------------------------------------------------------------------
# 5. Session end — user dismisses
#    After session_end_ask, user dismisses; no session_end appears.
# ---------------------------------------------------------------------------


class TestSessionEndDismiss:
    """session_end_ask → dismiss_session_end_ask → session stays alive."""

    @pytest.fixture
    def client(self, make_e2e_client):
        return make_e2e_client(
            [
                [TextChunk(content="Done! [SessionEndAsk]"), StreamDone(stop_reason="end_turn")],
            ]
        )

    def test_dismiss_ack_is_returned(self, client):
        session_id = send_prompt(client, "do something")
        drain_output(client, session_id)

        ack = send_control(client, {"type": "dismiss_session_end_ask", "session_id": session_id})
        assert ack["type"] == "ack"
        assert ack["session_id"] == session_id

    def test_session_not_ended_after_dismiss(self, client):
        session_id = send_prompt(client, "do something")
        drain_output(client, session_id)

        send_control(client, {"type": "dismiss_session_end_ask", "session_id": session_id})

        # Session must still be registered as active (not completed/failed)
        session_manager: SessionManager = client.app.state.session_manager
        session = session_manager.get_session(session_id)
        assert session is not None, "Session should still be in memory"
        assert session.status not in {SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED}


# ---------------------------------------------------------------------------
# 6. Session not found
#    Subscribing to a nonexistent session returns a SESSION_NOT_FOUND error.
# ---------------------------------------------------------------------------


class TestSessionNotFound:
    """Subscribe to a ghost session → SESSION_NOT_FOUND error on output channel."""

    @pytest.fixture
    def client(self, make_e2e_client):
        return make_e2e_client()

    def test_nonexistent_session_returns_error(self, client):
        msgs = drain_output(
            client,
            session_id="00000000-0000-0000-0000-000000000000",
            stop_types=frozenset({"error"}),
        )
        err = find_msg(msgs, "error")
        assert err is not None
        assert err.get("code") == "SESSION_NOT_FOUND"

    def test_error_carries_requested_session_id(self, client):
        ghost_id = "00000000-0000-0000-0000-000000000001"
        msgs = drain_output(client, session_id=ghost_id, stop_types=frozenset({"error"}))
        err = find_msg(msgs, "error")
        assert err is not None
        assert err.get("session_id") == ghost_id
