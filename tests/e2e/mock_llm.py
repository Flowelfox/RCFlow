"""MockLLMClient — drop-in LLMClient replacement for backend e2e tests.

Accepts a list of scripted *turns*.  Each turn is a sequence of
``LLMStreamEvent`` objects that ``stream_turn`` yields in order.

Turn shapes
-----------
Text-only (happy path)::

    [TextChunk("Done!"), StreamDone(stop_reason="end_turn")]

Tool-use (first turn) + follow-up (second turn)::

    [
        [TextChunk("Running…"), ToolCallRequest("toolu_01", "shell_exec", {"command": "echo hi"}),
         StreamDone(stop_reason="tool_use")],
        [TextChunk("Done!"), StreamDone(stop_reason="end_turn")],
    ]

The mock implements the full ``run_agentic_loop`` so real tool execution
still happens (ShellExecutor, etc.) — only the LLM step is scripted.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import UTC, datetime
from typing import Any

from src.core.llm import ConversationTurn, LLMStreamEvent, StreamDone, TextChunk, ToolCallRequest, TurnUsage

logger = logging.getLogger(__name__)

_MAX_AGENTIC_TURNS = 50


def _fake_usage(stop_reason: str = "end_turn") -> TurnUsage:
    now = datetime.now(UTC)
    return TurnUsage(
        message_id="msg_mock",
        model="mock-model",
        input_tokens=10,
        output_tokens=5,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        stop_reason=stop_reason,
        service_tier=None,
        inference_geo=None,
        started_at=now,
        ended_at=now,
    )


class MockLLMClient:
    """Deterministic LLM mock for e2e tests.

    Parameters
    ----------
    turns:
        Ordered list of turn scripts.  Each script is a list of
        ``LLMStreamEvent`` objects.  Turns are consumed left-to-right;
        once exhausted every subsequent call yields a bare
        ``StreamDone(stop_reason="end_turn")``.
    """

    def __init__(self, turns: list[list[LLMStreamEvent]]) -> None:
        self._turns: deque[list[LLMStreamEvent]] = deque(turns)
        # Interface attributes read by PromptRouter
        self.provider = "anthropic"
        self.attachment_capabilities: dict[str, bool] = {"images": False, "text_files": True}
        self.supports_vision = False

    # ------------------------------------------------------------------
    # Core streaming
    # ------------------------------------------------------------------

    async def stream_turn(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
    ):  # type: ignore[return]
        """Yield the next scripted turn's events."""
        events: list[LLMStreamEvent] = self._turns.popleft() if self._turns else [StreamDone(stop_reason="end_turn")]
        for event in events:
            yield event
            await asyncio.sleep(0)  # let other coroutines run between events

    # ------------------------------------------------------------------
    # Message builders (Anthropic format — matches real LLMClient)
    # ------------------------------------------------------------------

    def _build_assistant_message(self, turn: ConversationTurn) -> dict[str, Any]:
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
        self,
        tool_calls: list[ToolCallRequest],
        results: list[str],
    ) -> list[dict[str, Any]]:
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tc.tool_use_id,
                        "content": result,
                    }
                    for tc, result in zip(tool_calls, results, strict=True)
                ],
            }
        ]

    # ------------------------------------------------------------------
    # Agentic loop (mirrors real LLMClient.run_agentic_loop logic)
    # ------------------------------------------------------------------

    async def run_agentic_loop(
        self,
        messages: list[dict[str, Any]],
        execute_tool_fn: Any,
        system: str | None = None,
        should_stop_after_tools: Any = None,
    ):  # type: ignore[return]
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

            assistant_msg = self._build_assistant_message(turn)
            has_content = assistant_msg.get("content")
            if has_content:
                messages.append(assistant_msg)

            if not turn.tool_calls:
                break

            try:
                results: list[str] = []
                for tc in turn.tool_calls:
                    result = await execute_tool_fn(tc)
                    results.append(result)
                tool_msgs = self._build_tool_result_messages(turn.tool_calls, results)
                messages.extend(tool_msgs)
            except BaseException:
                messages.pop()
                raise

            if should_stop_after_tools is not None and should_stop_after_tools():
                break

    # ------------------------------------------------------------------
    # Utility stubs (called in background tasks — must not block)
    # ------------------------------------------------------------------

    async def generate_title(self, user_prompt: str, assistant_response: str) -> str:
        return "E2E Test Session"

    async def summarize(self, text: str) -> str:
        return "E2E test summary."

    async def extract_or_match_tasks(
        self,
        user_prompt: str,
        assistant_response: str,
        existing_tasks: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {"new_tasks": [], "attach_task_ids": []}

    async def evaluate_task_status(
        self,
        task_title: str,
        task_description: str | None,
        current_status: str,
        session_result: str,
    ) -> dict[str, str]:
        return {"status": current_status, "description": task_description or ""}

    async def close(self) -> None:
        pass
