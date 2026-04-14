"""Standalone mock Anthropic SSE server for E2E tests.

Implements the subset of the Anthropic Messages API needed by RCFlow:
  POST /v1/messages          — streams a scripted response as SSE
  PUT  /v1/admin/responses   — replace the scripted *text* response queue
  PUT  /v1/admin/turns       — replace with turn-based scripted responses
  GET  /v1/admin/responses   — inspect the current queue / turns
  GET  /health               — readiness probe

Turn-based scripting (for tool_use flows)
------------------------------------------
Use ``PUT /v1/admin/turns`` with a JSON body::

    {
      "turns": [
        {
          "type": "tool_use",
          "tool_name": "shell_exec",
          "tool_input": {"command": "echo hello"},
          "preamble": "Let me run that."
        },
        {
          "type": "text",
          "text": "Done! The output was: hello"
        }
      ]
    }

The server inspects each incoming ``POST /v1/messages`` request to detect
whether it contains ``tool_result`` content blocks (i.e. the backend is
feeding back a tool result).  If so, the server advances to the next turn.

Run:
    python tests/e2e/mock_anthropic_server.py --port 19000

Or import and use programmatically in pytest fixtures:
    from tests.e2e.mock_anthropic_server import MockAnthropicServer
    async with MockAnthropicServer() as server:
        base_url = server.base_url  # "http://127.0.0.1:<port>"
        server.set_responses(["Hello!", "World!"])
        # or turn-based:
        server.set_turns([
            {"type": "tool_use", "tool_name": "shell_exec",
             "tool_input": {"command": "echo hi"}, "preamble": "Running…"},
            {"type": "text", "text": "Done!"},
        ])
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

_DEFAULT_RESPONSES = ["Hello from mock LLM!"]


def _sse_event(event_type: str, data: Any) -> str:
    """Format a single SSE event line."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _message_start(msg_id: str, model: str) -> str:
    return _sse_event(
        "message_start",
        {
            "type": "message_start",
            "message": {
                "id": msg_id,
                "type": "message",
                "role": "assistant",
                "content": [],
                "model": model,
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        },
    )


def _text_block_frames(text: str, index: int = 0) -> list[str]:
    """Emit content_block_start + word-by-word deltas + content_block_stop for text."""
    frames: list[str] = []
    frames.append(
        _sse_event(
            "content_block_start",
            {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}},
        )
    )
    words = text.split()
    for i, word in enumerate(words):
        chunk = word if i == len(words) - 1 else word + " "
        frames.append(
            _sse_event(
                "content_block_delta",
                {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": chunk}},
            )
        )
    frames.append(_sse_event("content_block_stop", {"type": "content_block_stop", "index": index}))
    return frames


def _build_sse_stream(text: str, model: str = "claude-sonnet-4-6") -> list[str]:
    """Build the full SSE frame sequence for a single text response (end_turn)."""
    msg_id = f"msg_{uuid.uuid4().hex[:16]}"
    frames: list[str] = [_message_start(msg_id, model), _sse_event("ping", {"type": "ping"})]
    frames.extend(_text_block_frames(text, index=0))
    words = text.split()
    frames.append(
        _sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": len(words)},
            },
        )
    )
    frames.append(_sse_event("message_stop", {"type": "message_stop"}))
    return frames


def _build_tool_use_sse_stream(
    tool_name: str,
    tool_input: dict[str, Any],
    preamble: str = "",
    tool_id: str | None = None,
    model: str = "claude-sonnet-4-6",
) -> list[str]:
    """Build SSE frames for a tool_use response (stop_reason='tool_use').

    Emits an optional preamble text block (index 0) followed by a tool_use
    content block (index 1 or 0 if no preamble), then message_delta with
    stop_reason='tool_use'.
    """
    msg_id = f"msg_{uuid.uuid4().hex[:16]}"
    effective_tool_id = tool_id or f"toolu_{uuid.uuid4().hex[:16]}"
    frames: list[str] = [_message_start(msg_id, model), _sse_event("ping", {"type": "ping"})]

    block_index = 0
    if preamble:
        frames.extend(_text_block_frames(preamble, index=block_index))
        block_index += 1

    # tool_use content block
    frames.append(
        _sse_event(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": block_index,
                "content_block": {
                    "type": "tool_use",
                    "id": effective_tool_id,
                    "name": tool_name,
                    "input": {},
                },
            },
        )
    )
    # Stream tool input as a single input_json_delta
    input_json = json.dumps(tool_input)
    frames.append(
        _sse_event(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": block_index,
                "delta": {"type": "input_json_delta", "partial_json": input_json},
            },
        )
    )
    frames.append(_sse_event("content_block_stop", {"type": "content_block_stop", "index": block_index}))

    frames.append(
        _sse_event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 10},
            },
        )
    )
    frames.append(_sse_event("message_stop", {"type": "message_stop"}))
    return frames


def _has_tool_result(body: dict[str, Any]) -> bool:
    """Return True if the request body contains tool_result content blocks."""
    for msg in body.get("messages", []):
        content = msg.get("content", [])
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    return True
    return False


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class _State:
    """Mutable server state — one instance per server process.

    Two scripting modes are supported and are mutually exclusive:

    **Simple text mode** (default):
        ``set_responses(["Hello!", "World!"])`` — each call to ``/v1/messages``
        pops one response string and emits it as a text SSE stream.  The queue
        cycles so it never exhausts.

    **Turn mode**:
        ``set_turns([...])`` — each call to ``/v1/messages`` inspects the
        request body:

        * If it contains ``tool_result`` blocks the server advances to the next
          turn (the LLM is being fed tool results and should reply).
        * Otherwise it uses the current turn.

        Turn dicts:
          ``{"type": "text", "text": "Hello!"}``
          ``{"type": "tool_use", "tool_name": "shell_exec",
             "tool_input": {"command": "echo hi"}, "preamble": "Running…"}``
    """

    def __init__(self) -> None:
        self._responses: deque[str] = deque(_DEFAULT_RESPONSES)
        self._turns: list[dict[str, Any]] = []
        self._turn_index: int = 0
        self._call_count: int = 0

    # ------------------------------------------------------------------
    # Simple text mode
    # ------------------------------------------------------------------

    def next_response(self) -> str:
        """Pop and return next scripted response, cycling if exhausted."""
        self._call_count += 1
        if not self._responses:
            return f"mock response #{self._call_count}"
        text = self._responses.popleft()
        self._responses.append(text)
        return text

    def set_responses(self, responses: list[str]) -> None:
        self._responses = deque(responses)
        self._turns = []
        self._turn_index = 0

    # ------------------------------------------------------------------
    # Turn mode
    # ------------------------------------------------------------------

    def set_turns(self, turns: list[dict[str, Any]]) -> None:
        """Switch to turn mode with the given list of turn descriptors."""
        self._turns = turns
        self._turn_index = 0
        self._responses = deque()

    def next_frames(self, body: dict[str, Any], model: str) -> list[str]:
        """Return SSE frames for the next turn, advancing on tool_result input."""
        self._call_count += 1

        if not self._turns:
            # Simple text mode
            return _build_sse_stream(self.next_response(), model=model)

        # Turn mode: advance index when the request carries tool results
        if _has_tool_result(body) and self._turn_index < len(self._turns) - 1:
            self._turn_index += 1

        turn = self._turns[self._turn_index % len(self._turns)]

        if turn.get("type") == "tool_use":
            return _build_tool_use_sse_stream(
                tool_name=turn["tool_name"],
                tool_input=turn.get("tool_input", {}),
                preamble=turn.get("preamble", ""),
                model=model,
            )
        # default: text
        return _build_sse_stream(turn.get("text", ""), model=model)

    @property
    def call_count(self) -> int:
        return self._call_count


# ---------------------------------------------------------------------------
# FastAPI app factory
# ---------------------------------------------------------------------------


def create_app(state: _State | None = None) -> FastAPI:
    _state = state or _State()

    app = FastAPI(title="Mock Anthropic Server", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "call_count": _state.call_count}

    @app.post("/v1/messages")
    async def messages(request: Request) -> StreamingResponse:
        """Fake Anthropic POST /v1/messages — streams SSE."""
        body = await request.json()
        model = body.get("model", "claude-sonnet-4-6")
        frames = _state.next_frames(body, model)

        async def generate() -> AsyncIterator[str]:
            for frame in frames:
                yield frame
                await asyncio.sleep(0)  # yield control between frames

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "anthropic-ratelimit-requests-limit": "100",
                "anthropic-ratelimit-requests-remaining": "99",
                "anthropic-ratelimit-tokens-limit": "100000",
                "anthropic-ratelimit-tokens-remaining": "99000",
                "request-id": f"req_{uuid.uuid4().hex[:16]}",
            },
        )

    @app.put("/v1/admin/responses")
    async def set_responses(request: Request) -> dict[str, Any]:
        """Replace the scripted response queue.

        Body: {"responses": ["first reply", "second reply", ...]}
        """
        body = await request.json()
        responses = body.get("responses", [])
        if not isinstance(responses, list):
            return {"error": "responses must be a list"}
        _state.set_responses([str(r) for r in responses])
        return {"ok": True, "count": len(responses)}

    @app.put("/v1/admin/turns")
    async def set_turns(request: Request) -> dict[str, Any]:
        """Switch to turn mode with scripted tool_use / text turns.

        Body::

            {
              "turns": [
                {"type": "tool_use", "tool_name": "shell_exec",
                 "tool_input": {"command": "echo hi"}, "preamble": "Running…"},
                {"type": "text", "text": "Done!"}
              ]
            }
        """
        body = await request.json()
        turns = body.get("turns", [])
        if not isinstance(turns, list):
            return {"error": "turns must be a list"}
        _state.set_turns(turns)
        return {"ok": True, "count": len(turns)}

    @app.get("/v1/admin/responses")
    async def get_responses() -> dict[str, Any]:
        return {
            "responses": list(_state._responses),
            "turns": _state._turns,
            "turn_index": _state._turn_index,
            "call_count": _state.call_count,
        }

    return app


# ---------------------------------------------------------------------------
# Programmatic server (for pytest fixtures)
# ---------------------------------------------------------------------------


class MockAnthropicServer:
    """Async context manager that runs the mock server on a free port.

    Usage::

        async with MockAnthropicServer() as server:
            # server.base_url → "http://127.0.0.1:XXXXX"
            server.set_responses(["Hello!", "Goodbye!"])
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._port = port
        self._state = _State()
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None
        self.base_url: str = ""

    def set_responses(self, responses: list[str]) -> None:
        """Simple text mode: cycle through these responses."""
        self._state.set_responses(responses)

    def set_turns(self, turns: list[dict[str, Any]]) -> None:
        """Turn mode: scripted tool_use / text sequence per LLM call."""
        self._state.set_turns(turns)

    @property
    def call_count(self) -> int:
        return self._state.call_count

    async def __aenter__(self) -> MockAnthropicServer:
        app = create_app(self._state)
        config = uvicorn.Config(
            app=app,
            host=self._host,
            port=self._port,
            log_level="warning",
            access_log=False,
        )
        self._server = uvicorn.Server(config)

        # Start in background task.
        self._task = asyncio.create_task(self._server.serve())

        # Wait until the server has bound its port.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if self._server.started:
                break
            await asyncio.sleep(0.05)
        else:
            raise RuntimeError("MockAnthropicServer failed to start within 5 s")

        # Extract actual bound port (port=0 → OS-assigned).
        sockets = self._server.servers[0].sockets  # type: ignore[union-attr]
        actual_port = sockets[0].getsockname()[1]
        self.base_url = f"http://{self._host}:{actual_port}"
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._server:
            self._server.should_exit = True
        if self._task:
            await self._task


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _main() -> None:
    parser = argparse.ArgumentParser(description="Mock Anthropic SSE server for E2E tests")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=19000)
    parser.add_argument("--responses", nargs="*", help="Initial scripted responses")
    args = parser.parse_args()

    state = _State()
    if args.responses:
        state.set_responses(args.responses)

    app = create_app(state)
    print(f"Mock Anthropic server running on http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    _main()
