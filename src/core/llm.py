import json
import logging
import platform
import re
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

_OPENAI_REASONING_PREFIXES = ("o1", "o3", "o4")

# Maximum number of LLM ↔ tool-execution round-trips per prompt (F5).
# Prevents runaway agentic loops that could exhaust memory or API budget.
_MAX_AGENTIC_TURNS = 50

# ---------------------------------------------------------------------------
# Caveman mode — terse, token-efficient LLM output
# ---------------------------------------------------------------------------

_CAVEMAN_PROMPTS: dict[str, str] = {
    "lite": (
        "Respond terse like smart caveman. All technical substance stay. Only fluff die.\n"
        "Lite mode: keep articles + full sentences. Drop filler and hedging only."
    ),
    "full": (
        "Respond terse like smart caveman. All technical substance stay. Only fluff die.\n"
        "Drop: articles (a/an/the), filler (just/really/basically), pleasantries, hedging. "
        "Fragments OK. Short synonyms. Code unchanged."
    ),
    "ultra": (
        "Respond terse like smart caveman. All technical substance stay. Only fluff die.\n"
        "Ultra mode: abbreviate DB/auth/config/req/res/fn/impl. Arrows for causality (X → Y). "
        "One word when one word enough. Strip conjunctions."
    ),
}

_CAVEMAN_SUFFIX = (
    '\n\nStop: "stop caveman" or "normal mode"\n'
    "Auto-Clarity: drop caveman for security warnings, irreversible actions. Resume after.\n"
    "Boundaries: code/commits/PRs written normal."
)


def _caveman_instruction(level: str) -> str:
    """Return the caveman system-prompt block for the given intensity level."""
    base = _CAVEMAN_PROMPTS.get(level, _CAVEMAN_PROMPTS["full"])
    return base + _CAVEMAN_SUFFIX


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

        self._title_model = settings.TITLE_MODEL or self._model
        self._task_model = settings.TASK_MODEL or self._model
        self._settings = settings
        self._base_system_prompt = PromptBuilder().build(
            projects_dirs=", ".join(str(d) for d in settings.projects_dirs),
            os_name=platform.system(),
        )

    @property
    def provider(self) -> str:
        """The active LLM provider name: ``'anthropic'``, ``'bedrock'``, or ``'openai'``."""
        return self._provider

    @property
    def attachment_capabilities(self) -> dict[str, bool]:
        """Granular attachment capability flags for the active model.

        ``images``      — True when the model accepts image content blocks
                          (JPEG, PNG, GIF, WEBP).
        ``text_files``  — Always True; text/code files are always inlined as
                          plain text regardless of model.
        """
        return {
            "images": self.supports_vision,
            "text_files": True,
        }

    @property
    def supports_vision(self) -> bool:
        """True if the active model supports image/vision attachments.

        Anthropic/Bedrock: claude-3.x series and claude-[variant]-4 family.
        OpenAI: gpt-4o, gpt-4-turbo, gpt-4-vision, gpt-4.1+, gpt-5+ are multimodal
          and support image input. Reasoning-only models (o1/o3/o4 series) do not.
        """
        model = self._model.lower()
        if self._provider in ("anthropic", "bedrock"):
            # claude-3.x family and claude-[name]-4 family support vision
            return bool(re.search(r"claude-3", model) or re.search(r"claude-\w+-4", model))
        if self._provider == "openai":
            # Reasoning-only models (o1/o3/o4 series) do not support image input.
            # GPT-5 and GPT-4 series are general/multimodal — do NOT exclude them here.
            if any(model.startswith(p) for p in _OPENAI_REASONING_PREFIXES):
                return False
            # gpt-4o, gpt-4-turbo, gpt-4.1+, gpt-5+ and any model with "vision" in the
            # name all support image/vision input per the 2026-03-16 model catalog.
            return bool(
                "gpt-4o" in model
                or "gpt-4-turbo" in model
                or "vision" in model
                or re.search(r"gpt-4[._]\d", model)
                or model.startswith("gpt-5")
            )
        return False

    @property
    def _system_prompt(self) -> str:
        """Compose the full system prompt.

        Order: base → caveman (if enabled) → global_prompt (if set).
        Caveman is placed after the base so it follows RCFlow's core
        instructions, and before GLOBAL_PROMPT so user overrides still
        take final precedence.
        """
        parts = [self._base_system_prompt]
        if self._settings.CAVEMAN_MODE:
            parts.append(_caveman_instruction(self._settings.CAVEMAN_LEVEL))
        global_prompt = self._settings.GLOBAL_PROMPT.strip()
        if global_prompt:
            parts.append(global_prompt)
        return "\n\n".join(parts)

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
            "max_completion_tokens": 4096,
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
        for _turn_number in range(_MAX_AGENTIC_TURNS):
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
        else:
            # Reached the turn limit without a natural stop — surface this clearly.
            logger.warning(
                "Agentic loop reached the maximum turn limit (%d). "
                "Stopping to prevent runaway execution and API cost overrun.",
                _MAX_AGENTIC_TURNS,
            )
            yield TextChunk(content=f"\n\n[Stopped: reached the maximum of {_MAX_AGENTIC_TURNS} agentic turns.]")

    # ------------------------------------------------------------------
    # Utility methods (title generation, summarization)
    # ------------------------------------------------------------------

    async def _anthropic_create(self, system: str, content: str, max_tokens: int, *, model: str | None = None) -> str:
        """Make a non-streaming Anthropic/Bedrock call and return the text."""
        assert self._anthropic_client is not None
        use_model = model or self._model
        response = await self._anthropic_client.messages.create(
            model=use_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
        )
        block = response.content[0]
        assert isinstance(block, anthropic.types.TextBlock), f"Expected TextBlock, got {type(block)}"
        return block.text.strip()

    async def _openai_create(self, system: str, content: str, max_tokens: int, *, model: str | None = None) -> str:
        """Make a non-streaming OpenAI call and return the text."""
        assert self._openai_client is not None
        use_model = model or self._model
        response = await self._openai_client.chat.completions.create(
            model=use_model,
            max_completion_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
        )
        return (response.choices[0].message.content or "").strip()

    async def generate_title(self, user_prompt: str, assistant_response: str) -> str:
        """Generate a short title for a conversation from the first exchange."""
        truncated_response = assistant_response[:500] if assistant_response else ""
        if truncated_response:
            content = f"User: {user_prompt}\n\nAssistant: {truncated_response}"
        else:
            content = f"User: {user_prompt}"
        system = (
            "You are a title generator. Generate a concise title (3-6 words) for this conversation. "
            "Rules: return ONLY the title text, nothing else. "
            "No descriptions, no explanations, no markdown, no headers, no quotes, "
            "no punctuation at the end, no special characters. Just a few words as a title."
        )
        if self._provider == "openai":
            title = await self._openai_create(system, content, max_tokens=30, model=self._title_model)
        else:
            title = await self._anthropic_create(system, content, max_tokens=30, model=self._title_model)
        # Safety net: strip markdown headers and take only the first line
        title = title.split("\n")[0].lstrip("#").strip()
        return title

    async def summarize(self, text: str) -> str:
        """Generate a concise 2-3 sentence summary of the given text."""
        system = (
            "You are a concise summarizer. Produce a single sentence summary of the following text. "
            "Be direct and informative. No markdown."
        )
        if self._provider == "openai":
            return await self._openai_create(system, text, max_tokens=80)
        return await self._anthropic_create(system, text, max_tokens=80)

    @staticmethod
    def _parse_llm_json(raw: str, fallback: dict) -> dict:
        """Parse JSON from an LLM response with robust extraction and repair.

        Handles markdown code fences, truncated strings, and missing brackets.
        Returns *fallback* if parsing is impossible.
        """
        text = raw.strip()

        # Strip markdown code fences (```json ... ``` or ``` ... ```)
        fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()
        elif text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*\n?", "", text)
            text = re.sub(r"```\s*$", "", text)
            text = text.strip()

        # First attempt: direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to extract the first JSON object from the text
        obj_match = re.search(r"\{.*", text, re.DOTALL)
        if obj_match:
            candidate = obj_match.group(0)

            # Try as-is
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

            # Repair: balance braces/brackets and close unterminated strings
            repaired = candidate
            # Close an unterminated string (odd number of unescaped quotes)
            quote_count = len(re.findall(r'(?<!\\)"', repaired))
            if quote_count % 2 != 0:
                repaired += '"'
            # Balance brackets/braces
            open_braces = repaired.count("{") - repaired.count("}")
            open_brackets = repaired.count("[") - repaired.count("]")
            repaired += "]" * max(open_brackets, 0)
            repaired += "}" * max(open_braces, 0)

            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass

        logger.debug("Failed to parse LLM JSON, raw content: %s", raw)
        return fallback

    async def extract_or_match_tasks(
        self, user_prompt: str, assistant_response: str, existing_tasks: list[dict]
    ) -> dict:
        """Extract new tasks or match to existing ones.

        Returns: {"new_tasks": [...], "attach_task_ids": [...]}
        """
        content = f"User: {user_prompt}\n\nAssistant: {assistant_response}"
        existing_section = ""
        if existing_tasks:
            task_lines = "\n".join(
                f"- [{t['task_id']}] {t['title']} (status: {t['status']}): {t.get('description', '')}"
                for t in existing_tasks
            )
            existing_section = (
                f"\n\nExisting tasks in the system:\n{task_lines}\n\n"
                "If the conversation relates to an existing task, attach it instead of "
                "creating a duplicate. Match by semantic similarity, not exact title match."
            )
        system = (
            "Analyze this conversation and determine if it implies any actionable tasks "
            "or work items. If the user is asking for something to be done (code changes, "
            "bug fixes, feature implementations, investigations, etc.), extract each "
            "distinct task.\n\n"
            f"{existing_section}"
            "Return a JSON object with two keys:\n"
            '- "new_tasks": Array of new task objects, each with "title" (max 100 chars) '
            'and "description" (markdown-formatted string). The description must include:\n'
            "  1. A brief summary paragraph (1-3 sentences) of what needs to be done.\n"
            "  2. A **Review Checklist** section with a markdown checklist (using `- [ ]` items) "
            "specifying what reviewers should verify when the task moves to review status. "
            "Include items like code correctness, edge cases, tests, documentation, etc. "
            "that are specific to the task.\n\n"
            "Example description format:\n"
            "```\n"
            "Refactor the authentication middleware to support JWT refresh tokens.\n\n"
            "## Review Checklist\n"
            "- [ ] Refresh token generation and validation logic is correct\n"
            "- [ ] Token expiry and rotation are handled properly\n"
            "- [ ] Existing auth tests still pass\n"
            "- [ ] New edge cases (expired refresh token, revoked token) are covered\n"
            "```\n\n"
            "Only create new tasks for work that does NOT match any existing task.\n"
            '- "attach_task_ids": Array of existing task IDs (from the list above) that this '
            "session relates to.\n\n"
            "If the conversation is just a question, greeting, or doesn't imply actionable work, "
            'return: {"new_tasks": [], "attach_task_ids": []}\n\n'
            "Return ONLY valid JSON, no markdown fences around the JSON itself."
        )
        try:
            if self._provider == "openai":
                raw = await self._openai_create(system, content, max_tokens=1024, model=self._task_model)
            else:
                raw = await self._anthropic_create(system, content, max_tokens=1024, model=self._task_model)
            fallback = {"new_tasks": [], "attach_task_ids": []}
            return self._parse_llm_json(raw, fallback)
        except Exception:
            logger.exception("Failed to extract/match tasks from session context")
            return {"new_tasks": [], "attach_task_ids": []}

    async def evaluate_task_status(
        self,
        task_title: str,
        task_description: str | None,
        current_status: str,
        session_result: str,
    ) -> dict[str, str]:
        """Evaluate whether a task's status should change based on session results.

        Returns: {"status": "...", "description": "..."}
        """
        system = (
            "You are evaluating whether a task's status should be updated based on "
            "the results of a work session.\n\n"
            f"Task: {task_title}\n"
            f"Description: {task_description or 'No description'}\n"
            f"Current status: {current_status}\n\n"
            "Based on the session results below, determine:\n"
            "1. Whether the task status should change. Valid statuses: todo, in_progress, review\n"
            "   - 'review' means the work appears complete and needs user review\n"
            "   - You CANNOT set status to 'done' -- only users can do that\n"
            "2. Whether the description should be updated with new context.\n\n"
            "The description MUST be markdown-formatted and include:\n"
            "- A brief summary paragraph of what was done or needs to be done.\n"
            "- A **Review Checklist** section (using `- [ ]` items) listing what reviewers "
            "should verify. This checklist should be specific to the work performed — e.g., "
            "correctness of logic, edge cases handled, tests added/passing, documentation updated, "
            "files changed, etc.\n\n"
            "If the task is moving to 'review', ensure the review checklist reflects the actual "
            "work completed in this session so reviewers know exactly what to check.\n\n"
            "If the existing description already has a review checklist, update it to reflect "
            "the current state of the work rather than duplicating items.\n\n"
            'Return JSON with "status" and "description" keys.\n'
            "Return ONLY valid JSON, no markdown fences around the JSON itself."
        )
        truncated = session_result[:2000] if session_result else ""
        try:
            if self._provider == "openai":
                raw = await self._openai_create(system, truncated, max_tokens=512, model=self._task_model)
            else:
                raw = await self._anthropic_create(system, truncated, max_tokens=512, model=self._task_model)
            fallback = {"status": current_status, "description": task_description or ""}
            result = self._parse_llm_json(raw, fallback)
            # Ensure AI never sets done
            if result.get("status") == "done":
                result["status"] = current_status
            return result
        except Exception:
            logger.exception("Failed to evaluate task status")
            return {"status": current_status, "description": task_description or ""}

    async def close(self) -> None:
        if self._anthropic_client is not None:
            await self._anthropic_client.close()
        if self._openai_client is not None:
            await self._openai_client.close()
