"""``session_status`` (exposed to agents as ``rcflow_session_status``) — report session context."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.core.native_tools import NativeToolContext


async def run(ctx: NativeToolContext, params: dict[str, Any]) -> str:
    """Return the current session's worktree, project, todos, and queue as JSON."""
    del params
    session = ctx.session
    meta = session.metadata
    status = {
        "session_id": session.id,
        "status": session.status.value,
        "activity_state": session.activity_state.value,
        "title": session.title,
        "agent_type": session.agent_type,
        "project_path": session.main_project_path,
        "selected_worktree_path": meta.get("selected_worktree_path"),
        "agent_cwd": meta.get("agent_cwd"),
        "todos": session.todos,
        "queued_messages": len(session.pending_user_messages),
        "attached_task_ids": meta.get("attached_task_ids", []),
        "input_tokens": session.input_tokens,
        "output_tokens": session.output_tokens,
    }
    return json.dumps(status, indent=2)
