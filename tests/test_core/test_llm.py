"""Tests for LLMClient core logic.

Covers:
- ``_parse_llm_json`` — robust JSON extraction from raw LLM output
- ``_build_assistant_message`` — provider-specific message formatting
- ``_build_tool_result_messages`` — provider-specific tool result formatting
- ``stream_turn`` (Anthropic) — text chunks, tool calls, StreamDone, usage
- ``stream_turn`` (OpenAI) — text chunks, tool calls, StreamDone, usage
- ``run_agentic_loop`` — single-turn (no tools), multi-turn (with tools),
  loop termination via ``should_stop_after_tools``
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.llm import (
    ConversationTurn,
    LLMClient,
    StreamDone,
    TextChunk,
    ToolCallRequest,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm_client(provider: str, model: str) -> LLMClient:
    """Create an LLMClient with mocked SDK clients."""
    settings = MagicMock()
    settings.LLM_PROVIDER = provider
    settings.ANTHROPIC_MODEL = model
    settings.OPENAI_MODEL = model
    settings.AWS_REGION = "us-east-1"
    settings.AWS_ACCESS_KEY_ID = ""
    settings.AWS_SECRET_ACCESS_KEY = ""
    settings.ANTHROPIC_API_KEY = "test"
    settings.OPENAI_API_KEY = "test"
    settings.SUMMARY_MODEL = ""
    settings.TITLE_MODEL = ""
    settings.TASK_MODEL = ""
    settings.GLOBAL_PROMPT = ""
    settings.projects_dirs = []

    tool_registry = MagicMock()
    tool_registry.to_anthropic_tools.return_value = []
    tool_registry.to_openai_tools.return_value = []

    with (
        patch("src.core.llm.anthropic.AsyncAnthropic"),
        patch("src.core.llm.anthropic.AsyncAnthropicBedrock"),
        patch("src.core.llm.openai.AsyncOpenAI"),
        patch("src.core.llm.PromptBuilder"),
    ):
        return LLMClient(settings, tool_registry)


class _Obj:
    """Simple attribute bag used to construct mock SDK event objects."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# _parse_llm_json
# ---------------------------------------------------------------------------


class TestParseLlmJson:
    def test_valid_json_object(self) -> None:
        result = LLMClient._parse_llm_json('{"key": "value", "n": 42}', {})
        assert result == {"key": "value", "n": 42}

    def test_json_with_json_code_fence(self) -> None:
        raw = '```json\n{"key": "value"}\n```'
        assert LLMClient._parse_llm_json(raw, {}) == {"key": "value"}

    def test_json_with_generic_code_fence(self) -> None:
        raw = '```\n{"key": "value"}\n```'
        assert LLMClient._parse_llm_json(raw, {}) == {"key": "value"}

    def test_json_at_start_of_text(self) -> None:
        """Parser extracts the first JSON object when it appears at the start."""
        raw = '{"status": "ok"}'
        result = LLMClient._parse_llm_json(raw, {})
        assert result == {"status": "ok"}

    def test_truncated_json_missing_closing_brace(self) -> None:
        raw = '{"key": "value"'
        result = LLMClient._parse_llm_json(raw, {"default": True})
        assert result == {"key": "value"}

    def test_truncated_json_with_nested_object(self) -> None:
        raw = '{"outer": {"inner": "v"}'
        result = LLMClient._parse_llm_json(raw, {"default": True})
        assert isinstance(result, dict)
        assert "outer" in result or result == {"default": True}

    def test_unterminated_string_gets_repaired(self) -> None:
        raw = '{"key": "val'
        result = LLMClient._parse_llm_json(raw, {"default": True})
        # Should not raise; either repaired or fallback
        assert isinstance(result, dict)

    def test_completely_unparseable_returns_fallback(self) -> None:
        fallback = {"default": True}
        assert LLMClient._parse_llm_json("not json at all", fallback) == fallback

    def test_empty_string_returns_fallback(self) -> None:
        fallback = {"fallback": 1}
        assert LLMClient._parse_llm_json("", fallback) == fallback

    def test_whitespace_only_returns_fallback(self) -> None:
        fallback = {"x": 0}
        assert LLMClient._parse_llm_json("   \n  ", fallback) == fallback

    def test_nested_valid_json(self) -> None:
        raw = '{"new_tasks": [{"title": "Fix bug", "description": "desc"}], "attach_task_ids": []}'
        result = LLMClient._parse_llm_json(raw, {})
        assert result["new_tasks"][0]["title"] == "Fix bug"
        assert result["attach_task_ids"] == []


# ---------------------------------------------------------------------------
# _build_assistant_message
# ---------------------------------------------------------------------------


class TestBuildAssistantMessage:
    def test_anthropic_text_only(self) -> None:
        client = _make_llm_client("anthropic", "claude-sonnet-4-6")
        turn = ConversationTurn(text="Hello!", tool_calls=[])
        msg = client._build_assistant_message(turn)

        assert msg["role"] == "assistant"
        assert msg["content"] == [{"type": "text", "text": "Hello!"}]

    def test_anthropic_tool_call_only(self) -> None:
        client = _make_llm_client("anthropic", "claude-sonnet-4-6")
        tc = ToolCallRequest(tool_use_id="tc1", tool_name="read_file", tool_input={"path": "/tmp/x"})
        turn = ConversationTurn(text="", tool_calls=[tc])
        msg = client._build_assistant_message(turn)

        assert msg["role"] == "assistant"
        content = msg["content"]
        assert len(content) == 1
        assert content[0]["type"] == "tool_use"
        assert content[0]["id"] == "tc1"
        assert content[0]["name"] == "read_file"
        assert content[0]["input"] == {"path": "/tmp/x"}

    def test_anthropic_text_and_tool_call(self) -> None:
        client = _make_llm_client("anthropic", "claude-sonnet-4-6")
        tc = ToolCallRequest(tool_use_id="tc1", tool_name="list_dir", tool_input={})
        turn = ConversationTurn(text="Let me check.", tool_calls=[tc])
        msg = client._build_assistant_message(turn)

        content = msg["content"]
        assert len(content) == 2
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "tool_use"

    def test_openai_text_only(self) -> None:
        client = _make_llm_client("openai", "gpt-5.4")
        turn = ConversationTurn(text="Hello!", tool_calls=[])
        msg = client._build_assistant_message(turn)

        assert msg["role"] == "assistant"
        assert msg["content"] == "Hello!"
        assert "tool_calls" not in msg

    def test_openai_tool_call(self) -> None:
        client = _make_llm_client("openai", "gpt-5.4")
        tc = ToolCallRequest(tool_use_id="tc1", tool_name="read_file", tool_input={"path": "/tmp/x"})
        turn = ConversationTurn(text="", tool_calls=[tc])
        msg = client._build_assistant_message(turn)

        assert "tool_calls" in msg
        tc_out = msg["tool_calls"][0]
        assert tc_out["id"] == "tc1"
        assert tc_out["type"] == "function"
        assert tc_out["function"]["name"] == "read_file"
        assert json.loads(tc_out["function"]["arguments"]) == {"path": "/tmp/x"}

    def test_openai_multiple_tool_calls(self) -> None:
        client = _make_llm_client("openai", "gpt-5.4")
        tc1 = ToolCallRequest(tool_use_id="t1", tool_name="tool_a", tool_input={})
        tc2 = ToolCallRequest(tool_use_id="t2", tool_name="tool_b", tool_input={"x": 1})
        turn = ConversationTurn(text="", tool_calls=[tc1, tc2])
        msg = client._build_assistant_message(turn)

        assert len(msg["tool_calls"]) == 2


# ---------------------------------------------------------------------------
# _build_tool_result_messages
# ---------------------------------------------------------------------------


class TestBuildToolResultMessages:
    def test_anthropic_single_result(self) -> None:
        client = _make_llm_client("anthropic", "claude-sonnet-4-6")
        tc = ToolCallRequest(tool_use_id="tc1", tool_name="tool", tool_input={})
        msgs = client._build_tool_result_messages([tc], ["result text"])

        assert len(msgs) == 1
        msg = msgs[0]
        assert msg["role"] == "user"
        assert msg["content"][0]["type"] == "tool_result"
        assert msg["content"][0]["tool_use_id"] == "tc1"
        assert msg["content"][0]["content"] == "result text"

    def test_anthropic_multiple_results_in_one_message(self) -> None:
        client = _make_llm_client("anthropic", "claude-sonnet-4-6")
        tc1 = ToolCallRequest(tool_use_id="t1", tool_name="a", tool_input={})
        tc2 = ToolCallRequest(tool_use_id="t2", tool_name="b", tool_input={})
        msgs = client._build_tool_result_messages([tc1, tc2], ["r1", "r2"])

        assert len(msgs) == 1  # Anthropic: single user message with multiple blocks
        assert len(msgs[0]["content"]) == 2

    def test_openai_single_result(self) -> None:
        client = _make_llm_client("openai", "gpt-5.4")
        tc = ToolCallRequest(tool_use_id="tc1", tool_name="tool", tool_input={})
        msgs = client._build_tool_result_messages([tc], ["result text"])

        assert len(msgs) == 1
        msg = msgs[0]
        assert msg["role"] == "tool"
        assert msg["tool_call_id"] == "tc1"
        assert msg["content"] == "result text"

    def test_openai_multiple_results_are_separate_messages(self) -> None:
        client = _make_llm_client("openai", "gpt-5.4")
        tc1 = ToolCallRequest(tool_use_id="t1", tool_name="a", tool_input={})
        tc2 = ToolCallRequest(tool_use_id="t2", tool_name="b", tool_input={})
        msgs = client._build_tool_result_messages([tc1, tc2], ["r1", "r2"])

        assert len(msgs) == 2  # OpenAI: one message per tool result
        assert msgs[0]["tool_call_id"] == "t1"
        assert msgs[1]["tool_call_id"] == "t2"


# ---------------------------------------------------------------------------
# Anthropic streaming — _stream_turn_anthropic
# ---------------------------------------------------------------------------


class _MockAnthropicStream:
    """Async context manager that yields a list of mock stream events."""

    def __init__(self, events: list, final_message: MagicMock) -> None:
        self._events = events
        self._final_message = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for event in self._events:
            yield event

    async def get_final_message(self):
        return self._final_message


def _make_anthropic_final_message(
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> MagicMock:
    fm = MagicMock()
    fm.stop_reason = stop_reason
    fm.id = "msg_test"
    fm.model = "claude-sonnet-4-6"
    fm.usage = MagicMock(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    fm.service_tier = None
    fm.inference_geo = None
    return fm


class TestStreamTurnAnthropic:
    async def test_text_only_turn(self) -> None:
        client = _make_llm_client("anthropic", "claude-sonnet-4-6")

        events = [
            _Obj(type="content_block_delta", delta=_Obj(type="text_delta", text="Hello ")),
            _Obj(type="content_block_delta", delta=_Obj(type="text_delta", text="world")),
        ]
        final_msg = _make_anthropic_final_message()
        client._anthropic_client.messages.stream.return_value = _MockAnthropicStream(events, final_msg)

        collected = []
        async for event in client._stream_turn_anthropic([{"role": "user", "content": "hi"}]):
            collected.append(event)

        text_chunks = [e for e in collected if isinstance(e, TextChunk)]
        stream_done = [e for e in collected if isinstance(e, StreamDone)]

        assert len(text_chunks) == 2
        assert text_chunks[0].content == "Hello "
        assert text_chunks[1].content == "world"
        assert len(stream_done) == 1
        assert stream_done[0].stop_reason == "end_turn"

    async def test_tool_call_turn(self) -> None:
        client = _make_llm_client("anthropic", "claude-sonnet-4-6")

        events = [
            _Obj(
                type="content_block_start",
                content_block=_Obj(type="tool_use", id="tc_1", name="read_file"),
            ),
            _Obj(
                type="content_block_delta",
                delta=_Obj(type="input_json_delta", partial_json='{"path":'),
            ),
            _Obj(
                type="content_block_delta",
                delta=_Obj(type="input_json_delta", partial_json='"/tmp/x"}'),
            ),
            _Obj(type="content_block_stop"),
        ]
        final_msg = _make_anthropic_final_message(stop_reason="tool_use")
        client._anthropic_client.messages.stream.return_value = _MockAnthropicStream(events, final_msg)

        collected = []
        async for event in client._stream_turn_anthropic([{"role": "user", "content": "read it"}]):
            collected.append(event)

        tool_calls = [e for e in collected if isinstance(e, ToolCallRequest)]
        assert len(tool_calls) == 1
        assert tool_calls[0].tool_use_id == "tc_1"
        assert tool_calls[0].tool_name == "read_file"
        assert tool_calls[0].tool_input == {"path": "/tmp/x"}

    async def test_usage_populated_in_stream_done(self) -> None:
        client = _make_llm_client("anthropic", "claude-sonnet-4-6")
        events = [_Obj(type="content_block_delta", delta=_Obj(type="text_delta", text="hi"))]
        final_msg = _make_anthropic_final_message(input_tokens=20, output_tokens=8)
        client._anthropic_client.messages.stream.return_value = _MockAnthropicStream(events, final_msg)

        collected = []
        async for event in client._stream_turn_anthropic([]):
            collected.append(event)

        stream_done = next(e for e in collected if isinstance(e, StreamDone))
        assert stream_done.usage is not None
        assert stream_done.usage.input_tokens == 20
        assert stream_done.usage.output_tokens == 8

    async def test_malformed_tool_input_json_does_not_raise(self) -> None:
        """Malformed JSON in a tool input must be handled gracefully (empty dict)."""
        client = _make_llm_client("anthropic", "claude-sonnet-4-6")

        events = [
            _Obj(type="content_block_start", content_block=_Obj(type="tool_use", id="t1", name="tool")),
            _Obj(type="content_block_delta", delta=_Obj(type="input_json_delta", partial_json="{bad json")),
            _Obj(type="content_block_stop"),
        ]
        final_msg = _make_anthropic_final_message()
        client._anthropic_client.messages.stream.return_value = _MockAnthropicStream(events, final_msg)

        collected = []
        async for event in client._stream_turn_anthropic([]):
            collected.append(event)

        tool_calls = [e for e in collected if isinstance(e, ToolCallRequest)]
        assert len(tool_calls) == 1
        assert tool_calls[0].tool_input == {}


# ---------------------------------------------------------------------------
# OpenAI streaming — _stream_turn_openai
# ---------------------------------------------------------------------------


def _make_openai_chunk(
    text: str | None = None,
    tool_calls: list | None = None,
    finish_reason: str | None = None,
    usage=None,
    model: str = "gpt-5.4",
    chunk_id: str = "chatcmpl-test",
) -> MagicMock:
    chunk = MagicMock()
    chunk.id = chunk_id
    chunk.model = model
    chunk.usage = usage

    if usage is not None and not text and not tool_calls and not finish_reason:
        # Final usage chunk (no choices)
        chunk.choices = []
        return chunk

    choice = MagicMock()
    choice.finish_reason = finish_reason
    delta = MagicMock()
    delta.content = text
    delta.tool_calls = tool_calls
    choice.delta = delta
    chunk.choices = [choice]
    return chunk


class _MockOpenAIStream:
    """Async iterable over a list of mock OpenAI stream chunks."""

    def __init__(self, chunks: list) -> None:
        self._chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for chunk in self._chunks:
            yield chunk


class TestStreamTurnOpenAI:
    async def test_text_only_turn(self) -> None:
        client = _make_llm_client("openai", "gpt-5.4")

        usage_mock = MagicMock()
        usage_mock.prompt_tokens = 10
        usage_mock.completion_tokens = 4
        usage_mock.prompt_tokens_details = None

        chunks = [
            _make_openai_chunk(text="Hello "),
            _make_openai_chunk(text="world", finish_reason="stop"),
            _make_openai_chunk(usage=usage_mock),
        ]
        client._openai_client.chat.completions.create = AsyncMock(return_value=_MockOpenAIStream(chunks))

        collected = []
        async for event in client._stream_turn_openai([{"role": "user", "content": "hi"}]):
            collected.append(event)

        text_chunks = [e for e in collected if isinstance(e, TextChunk)]
        stream_done = [e for e in collected if isinstance(e, StreamDone)]

        assert any(c.content == "Hello " for c in text_chunks)
        assert len(stream_done) == 1
        assert stream_done[0].stop_reason == "stop"

    async def test_tool_call_assembled_from_chunks(self) -> None:
        client = _make_llm_client("openai", "gpt-5.4")

        tc_delta_1 = MagicMock()
        tc_delta_1.index = 0
        tc_delta_1.id = "tc_1"
        tc_delta_1.function = MagicMock(name="read_file", arguments='{"path":')
        tc_delta_1.function.name = "read_file"
        tc_delta_1.function.arguments = '{"path":'

        tc_delta_2 = MagicMock()
        tc_delta_2.index = 0
        tc_delta_2.id = None
        tc_delta_2.function = MagicMock()
        tc_delta_2.function.name = None
        tc_delta_2.function.arguments = '"/tmp/x"}'

        usage_mock = MagicMock()
        usage_mock.prompt_tokens = 15
        usage_mock.completion_tokens = 5
        usage_mock.prompt_tokens_details = None

        chunks = [
            _make_openai_chunk(tool_calls=[tc_delta_1]),
            _make_openai_chunk(tool_calls=[tc_delta_2]),
            _make_openai_chunk(finish_reason="tool_calls"),
            _make_openai_chunk(usage=usage_mock),
        ]
        client._openai_client.chat.completions.create = AsyncMock(return_value=_MockOpenAIStream(chunks))

        collected = []
        async for event in client._stream_turn_openai([]):
            collected.append(event)

        tool_calls = [e for e in collected if isinstance(e, ToolCallRequest)]
        assert len(tool_calls) == 1
        assert tool_calls[0].tool_use_id == "tc_1"
        assert tool_calls[0].tool_name == "read_file"
        assert tool_calls[0].tool_input == {"path": "/tmp/x"}

    async def test_usage_populated_in_stream_done(self) -> None:
        client = _make_llm_client("openai", "gpt-5.4")

        usage_mock = MagicMock()
        usage_mock.prompt_tokens = 20
        usage_mock.completion_tokens = 7
        usage_mock.prompt_tokens_details = None

        chunks = [
            _make_openai_chunk(text="hi", finish_reason="stop"),
            _make_openai_chunk(usage=usage_mock),
        ]
        client._openai_client.chat.completions.create = AsyncMock(return_value=_MockOpenAIStream(chunks))

        collected = []
        async for event in client._stream_turn_openai([]):
            collected.append(event)

        stream_done = next(e for e in collected if isinstance(e, StreamDone))
        assert stream_done.usage is not None
        assert stream_done.usage.input_tokens == 20
        assert stream_done.usage.output_tokens == 7


# ---------------------------------------------------------------------------
# run_agentic_loop
# ---------------------------------------------------------------------------


class TestRunAgenticLoop:
    async def test_single_turn_no_tools_terminates(self) -> None:
        """Loop must terminate immediately when the LLM returns no tool calls."""
        client = _make_llm_client("anthropic", "claude-sonnet-4-6")

        async def _fake_stream_turn(messages, system=None):
            yield TextChunk(content="Done.")
            yield StreamDone(stop_reason="end_turn")

        client.stream_turn = _fake_stream_turn
        tool_fn = AsyncMock(return_value="tool result")

        collected = []
        messages = [{"role": "user", "content": "hi"}]
        async for event in client.run_agentic_loop(messages, tool_fn):
            collected.append(event)

        text_chunks = [e for e in collected if isinstance(e, TextChunk)]
        assert any(c.content == "Done." for c in text_chunks)
        tool_fn.assert_not_called()

    async def test_loop_executes_tool_then_continues(self) -> None:
        """When the LLM requests a tool, it must be executed and the loop must
        continue with a second turn."""
        client = _make_llm_client("anthropic", "claude-sonnet-4-6")

        tc = ToolCallRequest(tool_use_id="t1", tool_name="echo", tool_input={"msg": "hello"})
        call_count = 0

        async def _fake_stream_turn(messages, system=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First turn: request a tool
                yield tc
                yield StreamDone(stop_reason="tool_use")
            else:
                # Second turn: return final text
                yield TextChunk(content="All done.")
                yield StreamDone(stop_reason="end_turn")

        client.stream_turn = _fake_stream_turn
        tool_fn = AsyncMock(return_value="echo result")

        messages = [{"role": "user", "content": "echo hello"}]
        collected = []
        async for event in client.run_agentic_loop(messages, tool_fn):
            collected.append(event)

        tool_fn.assert_awaited_once()
        assert call_count == 2
        text_chunks = [e for e in collected if isinstance(e, TextChunk)]
        assert any(c.content == "All done." for c in text_chunks)

    async def test_should_stop_after_tools_exits_loop(self) -> None:
        """When ``should_stop_after_tools`` returns True, the loop must not call
        the LLM for a second turn."""
        client = _make_llm_client("anthropic", "claude-sonnet-4-6")

        tc = ToolCallRequest(tool_use_id="t1", tool_name="echo", tool_input={})
        call_count = 0

        async def _fake_stream_turn(messages, system=None):
            nonlocal call_count
            call_count += 1
            yield tc
            yield StreamDone(stop_reason="tool_use")

        client.stream_turn = _fake_stream_turn
        tool_fn = AsyncMock(return_value="result")

        messages = [{"role": "user", "content": "go"}]
        async for _ in client.run_agentic_loop(messages, tool_fn, should_stop_after_tools=lambda: True):
            pass

        assert call_count == 1  # Only one LLM call; loop stopped after tools

    async def test_tool_exception_removes_orphaned_assistant_message(self) -> None:
        """If tool execution raises, the orphaned assistant message (with tool_use
        blocks) must be removed from the conversation history to keep it valid."""
        client = _make_llm_client("anthropic", "claude-sonnet-4-6")

        tc = ToolCallRequest(tool_use_id="t1", tool_name="bad_tool", tool_input={})

        async def _fake_stream_turn(messages, system=None):
            yield tc
            yield StreamDone(stop_reason="tool_use")

        client.stream_turn = _fake_stream_turn
        tool_fn = AsyncMock(side_effect=RuntimeError("tool exploded"))

        messages = [{"role": "user", "content": "do it"}]
        initial_len = len(messages)

        with pytest.raises(RuntimeError, match="tool exploded"):
            async for _ in client.run_agentic_loop(messages, tool_fn):
                pass

        # Orphaned assistant message must have been rolled back
        assert len(messages) == initial_len
