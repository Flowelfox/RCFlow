"""``rcflow_rename_session`` — set the current session's title."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.native_tools import NativeToolContext

_MAX_TITLE = 120


async def run(ctx: NativeToolContext, params: dict[str, Any]) -> str:
    """Set the session title, persist it, and broadcast the change to clients."""
    title = str(params.get("title", "")).strip()
    if not title:
        raise ValueError("rename_session requires a non-empty 'title'")
    title = title[:_MAX_TITLE]
    ctx.session.title = title
    ctx.router._fire_persist_session_metadata(ctx.session)
    if ctx.router._session_manager is not None:
        ctx.router._session_manager.broadcast_session_update(ctx.session)
    return f"Session renamed to: {title}"
