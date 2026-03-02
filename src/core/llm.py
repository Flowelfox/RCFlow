import json
import logging
import platform
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import anthropic

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
        provider = settings.LLM_PROVIDER.lower()
        if provider == "bedrock":
            kwargs: dict[str, Any] = {"aws_region": settings.AWS_REGION}
            if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
                kwargs["aws_access_key"] = settings.AWS_ACCESS_KEY_ID
                kwargs["aws_secret_key"] = settings.AWS_SECRET_ACCESS_KEY
            self._client: anthropic.AsyncAnthropic | anthropic.AsyncAnthropicBedrock = anthropic.AsyncAnthropicBedrock(
                **kwargs
            )
            logger.info("LLM provider: AWS Bedrock (region=%s)", settings.AWS_REGION)
        elif provider == "anthropic":
            self._client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
            logger.info("LLM provider: Anthropic (direct API)")
        else:
            msg = f"Unknown LLM provider: {provider!r}. Must be 'anthropic' or 'bedrock'."
            raise ValueError(msg)

        self._model = settings.ANTHROPIC_MODEL
        self._summary_model = settings.SUMMARY_MODEL or self._model
        self._tool_registry = tool_registry
        self._system_prompt = PromptBuilder().build(
            projects_dir=str(settings.PROJECTS_DIR.expanduser().resolve()),
            os_name=platform.system(),
        )

    async def stream_turn(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
    ) -> AsyncIterator[LLMStreamEvent]:
        """Stream a single LLM turn, yielding text chunks and tool call requests."""
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

        async with self._client.messages.stream(**kwargs) as stream:
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

            # Build the assistant message content blocks
            assistant_content: list[dict[str, Any]] = []
            if turn.text:
                assistant_content.append({"type": "text", "text": turn.text})
            for tc in turn.tool_calls:
                assistant_content.append(
                    {
                        "type": "tool_use",
                        "id": tc.tool_use_id,
                        "name": tc.tool_name,
                        "input": tc.tool_input,
                    }
                )

            if assistant_content:
                messages.append({"role": "assistant", "content": assistant_content})

            # If no tool calls, the loop is done
            if not turn.tool_calls:
                break

            # Execute tools and build tool_result messages.
            # If an exception occurs after the assistant message was appended
            # but before tool results are appended, remove the orphaned
            # assistant message to keep conversation history valid.
            try:
                tool_results: list[dict[str, Any]] = []
                for tc in turn.tool_calls:
                    result = await execute_tool_fn(tc)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tc.tool_use_id,
                            "content": result,
                        }
                    )

                messages.append({"role": "user", "content": tool_results})
            except BaseException:
                messages.pop()  # remove orphaned assistant message with tool_use blocks
                raise

            # Check if caller wants to stop the loop (e.g. after starting claude_code)
            if should_stop_after_tools is not None and should_stop_after_tools():
                break

    async def generate_title(self, user_prompt: str, assistant_response: str) -> str:
        """Generate a short title for a conversation from the first exchange."""
        if assistant_response:
            content = f"User: {user_prompt}\n\nAssistant: {assistant_response}"
        else:
            content = f"User: {user_prompt}"
        response = await self._client.messages.create(
            model=self._summary_model,
            max_tokens=30,
            system="Generate a short title (max 6 words) for this conversation. "
            "Return only the title, no quotes or punctuation.",
            messages=[{"role": "user", "content": content}],
        )
        return response.content[0].text.strip()

    async def summarize(self, text: str) -> str:
        """Generate a short TTS-friendly summary of the given text using a fast model."""
        response = await self._client.messages.create(
            model=self._summary_model,
            max_tokens=256,
            system="You are a concise summarizer. Produce a 2-3 sentence summary of the following text. "
            "The summary will be read aloud via text-to-speech, so keep it natural, conversational, "
            "and free of markdown, code blocks, or special formatting.",
            messages=[{"role": "user", "content": text}],
        )
        return response.content[0].text

    async def close(self) -> None:
        await self._client.close()
