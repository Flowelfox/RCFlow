"""Tests for the Agent-SDK → legacy stream-json converter.

The converter is the contract that lets the existing relay consume SDK messages
unchanged, so it's worth pinning precisely.  Pure functions — no subprocess.
"""

from __future__ import annotations

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from src.executors.claude_code_sdk import sdk_message_to_events


def _task_notification(**kw):
    base = dict(
        subtype="task_notification",
        data={},
        task_id="t1",
        status="completed",
        output_file="",
        summary="watch ended",
        uuid="u1",
        session_id="s1",
        tool_use_id="tu_mon",
    )
    base.update(kw)
    return TaskNotificationMessage(**base)


def _assistant(content):
    return AssistantMessage(content=content, model="claude-opus-4-8")


def _result(**kw):
    base = dict(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=2,
        session_id="s1",
    )
    base.update(kw)
    return ResultMessage(**base)


class TestConverter:
    def test_text_block(self):
        events = sdk_message_to_events(_assistant([TextBlock(text="hello")]))
        assert events == [{"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}}]

    def test_thinking_block(self):
        events = sdk_message_to_events(_assistant([ThinkingBlock(thinking="hmm", signature="sig")]))
        assert events[0]["message"]["content"] == [{"type": "thinking", "thinking": "hmm"}]

    def test_tool_use_block(self):
        block = ToolUseBlock(id="tu1", name="Bash", input={"command": "ls"})
        events = sdk_message_to_events(_assistant([block]))
        assert events[0]["message"]["content"] == [
            {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {"command": "ls"}}
        ]

    def test_mixed_blocks_preserve_order(self):
        events = sdk_message_to_events(_assistant([TextBlock(text="a"), ToolUseBlock(id="t", name="Read", input={})]))
        kinds = [b["type"] for b in events[0]["message"]["content"]]
        assert kinds == ["text", "tool_use"]

    def test_user_tool_result(self):
        block = ToolResultBlock(tool_use_id="tu1", content="ok", is_error=False)
        events = sdk_message_to_events(UserMessage(content=[block]))
        assert events == [
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "ok", "is_error": False}]
                },
            }
        ]

    def test_user_error_result(self):
        block = ToolResultBlock(tool_use_id="tu1", content="boom", is_error=True)
        events = sdk_message_to_events(UserMessage(content=[block]))
        assert events[0]["message"]["content"][0]["is_error"] is True

    def test_user_plain_text_is_dropped(self):
        assert sdk_message_to_events(UserMessage(content="just text")) == []

    def test_result_maps_cost_and_usage(self):
        msg = _result(
            result="done",
            total_cost_usd=0.12,
            usage={"input_tokens": 100, "output_tokens": 20},
        )
        events = sdk_message_to_events(msg)
        assert events == [
            {
                "type": "result",
                "subtype": "success",
                "result": "done",
                "cost_usd": 0.12,
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }
        ]

    def test_result_maps_max_turns_subtype(self):
        events = sdk_message_to_events(_result(subtype="error_max_turns", result="hit limit"))
        assert events[0]["subtype"] == "max_turns"

    def test_result_without_cost_omits_keys(self):
        events = sdk_message_to_events(_result(result=None))
        assert events[0] == {"type": "result", "subtype": "success", "result": ""}

    def test_system_usage_passthrough(self):
        msg = SystemMessage(subtype="usage", data={"usage": {"input_tokens": 5}})
        events = sdk_message_to_events(msg)
        assert events == [{"type": "system", "subtype": "usage", "usage": {"input_tokens": 5}}]

    def test_system_init_has_no_usage(self):
        msg = SystemMessage(subtype="init", data={"session_id": "s1"})
        events = sdk_message_to_events(msg)
        assert events == [{"type": "system", "subtype": "init"}]


class TestTaskNotificationMapping:
    """A Monitor watch's terminal arrives as TaskNotificationMessage → it must
    become a monitor-terminal tool_result the relay routes to MONITOR_END.
    """

    def test_completed_maps_to_clean_terminal(self):
        events = sdk_message_to_events(_task_notification(status="completed", summary="done"))
        assert events == [
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_mon",
                            "content": "Monitor exited: done",
                            "is_error": False,
                        }
                    ]
                },
            }
        ]

    def test_stopped_is_terminal_and_error(self):
        ev = sdk_message_to_events(_task_notification(status="stopped", summary="killed"))[0]
        block = ev["message"]["content"][0]
        assert block["content"] == "Monitor stopped: killed"
        assert block["is_error"] is True

    def test_failed_is_error(self):
        ev = sdk_message_to_events(_task_notification(status="failed", summary="boom"))[0]
        block = ev["message"]["content"][0]
        assert block["content"].startswith("Monitor failed:")
        assert block["is_error"] is True

    def test_without_tool_use_id_dropped(self):
        assert sdk_message_to_events(_task_notification(tool_use_id=None)) == []
