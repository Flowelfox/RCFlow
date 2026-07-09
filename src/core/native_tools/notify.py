"""``rcflow_notify`` — push a notification to the connected client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.core.buffer import MessageType

if TYPE_CHECKING:
    from src.core.native_tools import NativeToolContext

_VALID_LEVELS = {"info", "success", "warning", "error"}


async def run(ctx: NativeToolContext, params: dict[str, Any]) -> str:
    """Send a notification message to the client subscribed to this session."""
    message = str(params.get("message", "")).strip()
    if not message:
        raise ValueError("notify requires a non-empty 'message'")
    level = str(params.get("level", "info")).lower()
    if level not in _VALID_LEVELS:
        level = "info"
    ctx.session.buffer.push_text(
        MessageType.NOTIFICATION,
        {"session_id": ctx.session.id, "content": message, "level": level},
    )
    return f"Notification sent ({level}): {message}"
