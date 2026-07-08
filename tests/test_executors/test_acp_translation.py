"""Unit tests for the pure ACP update → event translation (src/executors/acp.py)."""

from __future__ import annotations

from types import SimpleNamespace as Ns

from src.executors.acp import acp_update_to_event


def _text_update(kind: str, text: str, message_id: str | None = None) -> Ns:
    return Ns(session_update=kind, content=Ns(text=text), message_id=message_id)


class TestTextChunks:
    def test_message_chunk(self) -> None:
        event = acp_update_to_event(_text_update("agent_message_chunk", "hello", "m1"))
        assert event == {"type": "text", "text": "hello", "message_id": "m1"}

    def test_thought_chunk(self) -> None:
        event = acp_update_to_event(_text_update("agent_thought_chunk", "hmm"))
        assert event == {"type": "thought", "text": "hmm", "message_id": None}

    def test_empty_text_dropped(self) -> None:
        assert acp_update_to_event(_text_update("agent_message_chunk", "")) is None


class TestToolCalls:
    def test_tool_call_pending(self) -> None:
        update = Ns(
            session_update="tool_call",
            tool_call_id="t1",
            title="write",
            kind="edit",
            status="pending",
            raw_input={"filePath": "/a/b.txt"},
            raw_output=None,
            locations=[Ns(path="/a/b.txt")],
            content=None,
        )
        event = acp_update_to_event(update)
        assert event is not None
        assert event["type"] == "tool_call"
        assert event["id"] == "t1"
        assert event["title"] == "write"
        assert event["kind"] == "edit"
        assert event["status"] == "pending"
        assert event["raw_input"] == {"filePath": "/a/b.txt"}
        assert event["locations"] == ["/a/b.txt"]

    def test_tool_call_update_with_diff_and_content(self) -> None:
        update = Ns(
            session_update="tool_call_update",
            tool_call_id="t1",
            title=None,
            kind=None,
            status="completed",
            raw_input=None,
            raw_output={"ok": True},
            locations=None,
            content=[
                Ns(type="diff", path="/a/b.txt", old_text=None, new_text="new content"),
                Ns(type="content", content=Ns(text="wrote it")),
            ],
        )
        event = acp_update_to_event(update)
        assert event is not None
        assert event["status"] == "completed"
        assert event["diff"] == {"path": "/a/b.txt", "old_text": None, "new_text": "new content"}
        assert event["locations"] == ["/a/b.txt"]  # diff path feeds locations
        assert event["content_texts"] == ["wrote it"]
        assert event["raw_output"] == '{"ok": true}'


class TestOtherKinds:
    def test_plan(self) -> None:
        update = Ns(
            session_update="plan",
            entries=[Ns(content="step one", priority="high", status="pending")],
        )
        event = acp_update_to_event(update)
        assert event == {
            "type": "plan",
            "entries": [{"content": "step one", "priority": "high", "status": "pending"}],
        }

    def test_usage(self) -> None:
        update = Ns(session_update="usage_update", used=123, size=1000, cost=Ns(amount=0.5, currency="USD"))
        event = acp_update_to_event(update)
        assert event == {"type": "usage", "used": 123, "size": 1000, "cost_amount": 0.5, "cost_currency": "USD"}

    def test_usage_without_cost(self) -> None:
        update = Ns(session_update="usage_update", used=1, size=2, cost=None)
        event = acp_update_to_event(update)
        assert event is not None
        assert event["cost_amount"] is None

    def test_available_commands(self) -> None:
        update = Ns(
            session_update="available_commands_update",
            available_commands=[Ns(name="cmd", description="desc")],
        )
        event = acp_update_to_event(update)
        assert event == {"type": "available_commands", "commands": [{"name": "cmd", "description": "desc"}]}

    def test_unknown_kind_dropped(self) -> None:
        assert acp_update_to_event(Ns(session_update="current_mode_update", mode_id="auto")) is None
