"""WebSocket test helpers for backend e2e tests.

All helpers assume Starlette's synchronous TestClient WebSocket sessions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

_API_KEY = "test-api-key"
_INPUT_URL = f"/ws/input/text?api_key={_API_KEY}"
_OUTPUT_URL = f"/ws/output/text?api_key={_API_KEY}"

# Default number of messages to look through before giving up.
_MAX_DRAIN = 60


def send_prompt(client: TestClient, text: str, session_id: str | None = None) -> str:
    """Send a prompt via /ws/input/text and return the session_id from the ack.

    Closes the input channel immediately after the ack — the background task
    lives on in the server event loop and keeps producing output.
    """
    with client.websocket_connect(_INPUT_URL) as ws:
        payload: dict = {"type": "prompt", "text": text}
        if session_id:
            payload["session_id"] = session_id
        ws.send_json(payload)
        ack = ws.receive_json()

    assert ack["type"] == "ack", f"Expected ack, got: {ack}"
    return ack["session_id"]


def drain_output(
    client: TestClient,
    session_id: str,
    stop_types: frozenset[str] = frozenset({"summary", "turn_complete", "session_end", "error"}),
    stop_after: int = 1,
) -> list[dict]:
    """Subscribe to /ws/output/text and drain until a stop-type message arrives.

    No pre-subscription sleep is needed: ``SessionBuffer.subscribe_text``
    replays the full history and either adds a live subscriber (buffer still
    open) or appends a ``None`` sentinel (buffer already closed).  In both
    cases the drain loop receives every message that was or will be pushed —
    including those produced by still-running background tasks — without any
    timing dependency.

    Parameters
    ----------
    client:
        Starlette TestClient instance.
    session_id:
        Session to subscribe to.
    stop_types:
        Receiving any message with a ``type`` in this set counts toward stopping.
    stop_after:
        Stop after this many stop-type messages have been received. Use > 1 for
        multi-turn conversations where earlier turns also emit stop-type messages.
    """
    messages: list[dict] = []
    stop_count = 0
    with client.websocket_connect(_OUTPUT_URL) as ws:
        ws.send_json({"type": "subscribe", "session_id": session_id})
        for _ in range(_MAX_DRAIN):
            msg = ws.receive_json()
            messages.append(msg)
            if msg.get("type") in stop_types:
                stop_count += 1
                if stop_count >= stop_after:
                    break
    return messages


def send_control(client: TestClient, msg: dict) -> dict:
    """Send a single control message via /ws/input/text and return the ack/error."""
    with client.websocket_connect(_INPUT_URL) as ws:
        ws.send_json(msg)
        return ws.receive_json()


# ---------------------------------------------------------------------------
# Assertion helpers
# ---------------------------------------------------------------------------


def msg_types(messages: list[dict]) -> list[str]:
    """Return the ``type`` field of each message."""
    return [m.get("type", "") for m in messages]


def find_msg(messages: list[dict], type_: str) -> dict | None:
    """Return the first message with the given type, or None."""
    return next((m for m in messages if m.get("type") == type_), None)


def find_all(messages: list[dict], type_: str) -> list[dict]:
    """Return all messages with the given type."""
    return [m for m in messages if m.get("type") == type_]
