import json
import logging
import platform
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anthropic
import openai

from src.config import Settings
from src.prompts import PromptBuilder
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class TextChunk:
    """A chunk of streamed text from the LLM."""

    content: str


@dataclass
class ToolCallRequest:
    """The LLM wants to call a tool."""

    tool_use_id: str
    tool_name: str
    tool_input: dict[str, Any]


@dataclass
class TurnUsage:
    """Usage statistics from a single LLM API turn."""

    message_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    stop_reason: str
    service_tier: str | None
    inference_geo: str | None
    started_at: datetime
    ended_at: datetime


@dataclass
class StreamDone:
    """The LLM stream is complete for this turn."""

    stop_reason: str
    usage: TurnUsage | None = None


type LLMStreamEvent = TextChunk | ToolCallRequest | StreamDone


@dataclass
class ConversationTurn:
    """Accumulated result of one LLM turn."""

    text: str = ""
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    stop_reason: str = ""


class LLMClient:
    def __init__(self, settings: Settings, tool_registry: ToolRegistry) -> None:
        self._provider = settings.LLM_PROVIDER.lower()
        self._tool_registry = tool_registry
        self._anthropic_client: anthropic.AsyncAnthropic | anthropic.AsyncAnthropicBedrock | None = None
        self._openai_client: openai.AsyncOpenAI | None = None

        if self._provider == "bedrock":
            kwargs: dict[str, Any] = {"aws_region": settings.AWS_REGION}
            if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
                kwargs["aws_access_key"] = settings.AWS_ACCESS_KEY_ID
                kwargs["aws_secret_key"] = settings.AWS_SECRET_ACCESS_KEY
            self._anthropic_client = anthropic.AsyncAnthropicBedrock(**kwargs)
            self._model = settings.ANTHROPIC_MODEL
            logger.info("LLM provider: AWS Bedrock (region=%s)", settings.AWS_REGION)

        elif self._provider == "anthropic":
            self._anthropic_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
            self._model = settings.ANTHROPIC_MODEL
            logger.info("LLM provider: Anthropic (direct API)")

        elif self._provider == "openai":
            self._openai_client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            self._model = settings.OPENAI_MODEL
            logger.info("LLM provider: OpenAI (model=%s)", settings.OPENAI_MODEL)

        else:
            msg = f"Unknown LLM provider: {self._provider!r}. Must be 'anthropic', 'bedrock', or 'openai'."
            raise ValueError(msg)

        self._summary_model = settings.SUMMARY_MODEL or self._model
        self._system_prompt = PromptBuilder().build(
            projects_dir=str(settings.PROJECTS_DIR.expanduser().resolve()),
            os_name=platform.system(),
        )

    # ------------------------------------------------------------------
    # Streaming turn dispatch
    # ------------------------------------------------------------------

    async def stream_turn(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Stream a single LLM turn, yielding text chunks and tool call requests."""
        if self._provider == "openai":
            async for event in self._stream_turn_openai(messages, system):
                yield event
        else:
            async for event in self._stream_turn_anthropic(messages, system):
                yield event

    # ------------------------------------------------------------------
    # Anthropic / Bedrock streaming
    # ------------------------------------------------------------------

    async def _stream_turn_anthropic(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Stream a turn using the Anthropic/Bedrock client."""
        assert self._anthropic_client is not None
        tools = self._tool_registry.to_anthropic_tools()

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 4096,
            "messages": messages,
            "system": system or self._system_prompt,
        }
        if tools:
            kwargs["tools"] = tools

        current_tool_name: str | None = None
        current_tool_id: str | None = None
        current_tool_input_json = ""

        started_at = datetime.now(UTC)

        async with self._anthropic_client.messages.stream(**kwargs) as stream:
            async for event in stream:
                event_type = event.type

                if event_type == "content_block_start":
                    content_block = getattr(event, "content_block", None)
                    if content_block and getattr(content_block, "type", None) == "tool_use":
                        current_tool_id = content_block.id
                        current_tool_name = content_block.name
                        current_tool_input_json = ""

                elif event_type == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    if delta:
                        delta_type = getattr(delta, "type", None)
                        if delta_type == "text_delta":
                            yield TextChunk(content=delta.text)
                        elif delta_type == "input_json_delta":
                            current_tool_input_json += delta.partial_json

                elif event_type == "content_block_stop":
                    if current_tool_name and current_tool_id:
                        try:
                            tool_input = json.loads(current_tool_input_json) if current_tool_input_json else {}
                        except json.JSONDecodeError:
                            tool_input = {}
                            logger.error("Failed to parse tool input JSON: %s", current_tool_input_json)

                        yield ToolCallRequest(
                            tool_use_id=current_tool_id,
                            tool_name=current_tool_name,
                            tool_input=tool_input,
                        )
                        current_tool_name = None
                        current_tool_id = None
                        current_tool_input_json = ""

            final_message = await stream.get_final_message()
            ended_at = datetime.now(UTC)

            stop_reason = final_message.stop_reason or "end_turn"
            usage = final_message.usage

            turn_usage = TurnUsage(
                message_id=final_message.id,
                model=final_message.model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", None) or 0,
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", None) or 0,
                stop_reason=stop_reason,
                service_tier=getattr(final_message, "service_tier", None),
                inference_geo=getattr(final_message, "inference_geo", None),
                started_at=started_at,
                ended_at=ended_at,
            )

            yield StreamDone(stop_reason=stop_reason, usage=turn_usage)

    # ------------------------------------------------------------------
    # OpenAI streaming
    # ------------------------------------------------------------------

    async def _stream_turn_openai(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Stream a turn using the OpenAI client."""
        assert self._openai_client is not None
        tools = self._tool_registry.to_openai_tools()

        # OpenAI uses a system message instead of a top-level system parameter.
        openai_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system or self._system_prompt},
            *messages,
        ]

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 4096,
            "messages": openai_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools

        started_at = datetime.now(UTC)

        # Track tool calls being assembled across streamed chunks.
        # OpenAI streams tool calls incrementally with an index per call.
        tool_calls_in_progress: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        chunk_id = ""
        chunk_model = ""

        stream = await self._openai_client.chat.completions.create(**kwargs)
        async for chunk in stream:
            chunk_id = chunk.id or chunk_id
            chunk_model = chunk.model or chunk_model

            # Final chunk with usage stats (empty choices)
            if not chunk.choices and chunk.usage:
                ended_at = datetime.now(UTC)
                usage = chunk.usage
                stop = finish_reason or "end_turn"
                cached = 0
                details = getattr(usage, "prompt_tokens_details", None)
                if details:
                    cached = getattr(details, "cached_tokens", 0) or 0
                yield StreamDone(
                    stop_reason=stop,
                    usage=TurnUsage(
                        message_id=chunk_id,
                        model=chunk_model,
                        input_tokens=usage.prompt_tokens or 0,
                        output_tokens=usage.completion_tokens or 0,
                        cache_creation_input_tokens=0,
                        cache_read_input_tokens=cached,
                        stop_reason=stop,
                        service_tier=getattr(chunk, "service_tier", None),
                        inference_geo=None,
                        started_at=started_at,
                        ended_at=ended_at,
                    ),
                )
                continue

            if not chunk.choices:
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            # Text content
            if delta and delta.content:
                yield TextChunk(content=delta.content)

            # Tool calls (streamed incrementally by index)
            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_in_progress:
                        tool_calls_in_progress[idx] = {"id": "", "name": "", "arguments": ""}
                    tc = tool_calls_in_progress[idx]
                    if tc_delta.id:
                        tc["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc["arguments"] += tc_delta.function.arguments

            # End of response — emit completed tool calls
            if choice.finish_reason:
                finish_reason = choice.finish_reason
                for tc in tool_calls_in_progress.values():
                    try:
                        tool_input = json.loads(tc["arguments"]) if tc["arguments"] else {}
                    except json.JSONDecodeError:
                        tool_input = {}
                        logger.error("Failed to parse OpenAI tool arguments: %s", tc["arguments"])

                    yield ToolCallRequest(
                        tool_use_id=tc["id"],
                        tool_name=tc["name"],
                        tool_input=tool_input,
                    )
                tool_calls_in_progress.clear()

    # ------------------------------------------------------------------
    # Provider-aware message builders (for the agentic loop)
    # ------------------------------------------------------------------

    def _build_assistant_message(self, turn: ConversationTurn) -> dict[str, Any]:
        """Build the assistant message in the correct format for the current provider."""
        if self._provider == "openai":
            msg: dict[str, Any] = {"role": "assistant", "content": turn.text or None}
            if turn.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.tool_use_id,
                        "type": "function",
                        "function": {
                            "name": tc.tool_name,
                            "arguments": json.dumps(tc.tool_input),
                        },
                    }
                    for tc in turn.tool_calls
                ]
            return msg

        # Anthropic / Bedrock format
        content: list[dict[str, Any]] = []
        if turn.text:
            content.append({"type": "text", "text": turn.text})
        for tc in turn.tool_calls:
            content.append(
                {
                    "type": "tool_use",
                    "id": tc.tool_use_id,
                    "name": tc.tool_name,
                    "input": tc.tool_input,
                }
            )
        return {"role": "assistant", "content": content}

    def _build_tool_result_messages(
        self, tool_calls: list[ToolCallRequest], results: list[str]
    ) -> list[dict[str, Any]]:
        """Build tool result messages in the correct format for the current provider."""
        if self._provider == "openai":
            # OpenAI: one message per tool result with role="tool"
            return [
                {"role": "tool", "tool_call_id": tc.tool_use_id, "content": result}
                for tc, result in zip(tool_calls, results, strict=True)
            ]

        # Anthropic: single user message with tool_result content blocks
        return [
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": tc.tool_use_id, "content": result}
                    for tc, result in zip(tool_calls, results, strict=True)
                ],
            }
        ]

    # ------------------------------------------------------------------
    # Agentic loop
    # ------------------------------------------------------------------

    async def run_agentic_loop(
        self,
        messages: list[dict[str, Any]],
        execute_tool_fn: Callable[[ToolCallRequest], Coroutine[Any, Any, str]],
        system: str | None = None,
        should_stop_after_tools: Callable[[], bool] | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Run the full agentic loop: stream LLM -> execute tools -> feed results back -> repeat.

        Args:
            should_stop_after_tools: Optional callback checked after tool execution.
                When it returns True the assistant message and tool results are still
                appended to ``messages`` (preserving conversation history) but
                the loop exits without calling the LLM again.
        """
        while True:
            turn = ConversationTurn()

            async for event in self.stream_turn(messages, system):
                yield event

                match event:
                    case TextChunk(content=text):
                        turn.text += text
                    case ToolCallRequest() as tool_call:
                        turn.tool_calls.append(tool_call)
                    case StreamDone(stop_reason=reason):
                        turn.stop_reason = reason

            # Build the assistant message in provider-appropriate format
            assistant_msg = self._build_assistant_message(turn)
            has_content = assistant_msg.get("content") or assistant_msg.get("tool_calls")
            if has_content:
                messages.append(assistant_msg)

            # If no tool calls, the loop is done
            if not turn.tool_calls:
                break

            # Execute tools and build tool_result messages.
            # If an exception occurs after the assistant message was appended
            # but before tool results are appended, remove the orphaned
            # assistant message to keep conversation history valid.
            try:
                results: list[str] = []
                for tc in turn.tool_calls:
                    result = await execute_tool_fn(tc)
                    results.append(result)

                tool_msgs = self._build_tool_result_messages(turn.tool_calls, results)
                messages.extend(tool_msgs)
            except BaseException:
                messages.pop()  # remove orphaned assistant message with tool_use blocks
                raise

            # Check if caller wants to stop the loop (e.g. after starting claude_code)
            if should_stop_after_tools is not None and should_stop_after_tools():
                break

    # ------------------------------------------------------------------
    # Utility methods (title generation, summarization)
    # ------------------------------------------------------------------

    async def _anthropic_create(self, system: str, content: str, max_tokens: int) -> str:
        """Make a non-streaming Anthropic/Bedrock call and return the text."""
        assert self._anthropic_client is not None
        response = await self._anthropic_client.messages.create(
            model=self._summary_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        return response.content[0].text.strip()

    async def _openai_create(self, system: str, content: str, max_tokens: int) -> str:
        """Make a non-streaming OpenAI call and return the text."""
        assert self._openai_client is not None
        response = await self._openai_client.chat.completions.create(
            model=self._summary_model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        )
        return (response.choices[0].message.content or "").strip()

    async def generate_title(self, user_prompt: str, assistant_response: str) -> str:
        """Generate a short title for a conversation from the first exchange."""
        if assistant_response:
            content = f"User: {user_prompt}\n\nAssistant: {assistant_response}"
        else:
            content = f"User: {user_prompt}"
        system = (
            "Generate a short title (max 6 words) for this conversation. "
            "Return only the title, no quotes or punctuation."
        )
        if self._provider == "openai":
            return await self._openai_create(system, content, max_tokens=30)
        return await self._anthropic_create(system, content, max_tokens=30)

    async def summarize(self, text: str) -> str:
        """Generate a short TTS-friendly summary of the given text using a fast model."""
        system = (
            "You are a concise summarizer. Produce a 2-3 sentence summary of the following text. "
            "The summary will be read aloud via text-to-speech, so keep it natural, conversational, "
            "and free of markdown, code blocks, or special formatting."
        )
        if self._provider == "openai":
            return await self._openai_create(system, text, max_tokens=256)
        return await self._anthropic_create(system, text, max_tokens=256)

    async def close(self) -> None:
        if self._anthropic_client is not None:
            await self._anthropic_client.close()
        if self._openai_client is not None:
            await self._openai_client.close()
