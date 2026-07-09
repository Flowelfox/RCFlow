"""``notify`` (exposed to agents as ``rcflow_notify``) — push a client notification."""

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
    # Ephemeral: the client routes this to its NotificationService (like any
    # other app notification), so it must NOT be archived to session history or
    # replayed into the transcript — same contract as subprocess_status.
    ctx.session.buffer.push_ephemeral(
        MessageType.NOTIFICATION,
        {"session_id": ctx.session.id, "content": message, "level": level},
    )
    return f"Notification sent ({level}): {message}"
