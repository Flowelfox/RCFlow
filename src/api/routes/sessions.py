from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from src.api.deps import verify_http_api_key
from src.models.db import Session as SessionModel
from src.models.db import SessionMessage as SessionMessageModel

if TYPE_CHECKING:
    from src.core.prompt_router import PromptRouter
    from src.core.session import SessionManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Sessions"])


@router.get(
    "/sessions",
    summary="List all sessions",
    description=(
        "Returns all sessions (in-memory and archived) with their status, "
        "type, and creation time, sorted by created_at descending."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def list_sessions(
    request: Request,
) -> dict[str, Any]:
    session_manager = request.app.state.session_manager
    db_session_factory = request.app.state.db_session_factory
    if db_session_factory is not None:
        async with db_session_factory() as db:
            all_sessions = await session_manager.list_all_with_archived(db)
        sessions = [
            {
                "session_id": s["session_id"],
                "status": s["status"],
                "activity_state": s.get("activity_state", "idle"),
                "session_type": s["session_type"],
                "created_at": s["created_at"].isoformat(),
                "title": s.get("title"),
                "input_tokens": s.get("input_tokens", 0),
                "output_tokens": s.get("output_tokens", 0),
                "cache_creation_input_tokens": s.get("cache_creation_input_tokens", 0),
                "cache_read_input_tokens": s.get("cache_read_input_tokens", 0),
                "tool_input_tokens": s.get("tool_input_tokens", 0),
                "tool_output_tokens": s.get("tool_output_tokens", 0),
                "tool_cost_usd": s.get("tool_cost_usd", 0.0),
                "main_project_path": s.get("main_project_path"),
                "agent_type": s.get("agent_type"),
            }
            for s in all_sessions
        ]
    else:
        sessions = [
            {
                "session_id": s.id,
                "status": s.status.value,
                "activity_state": s.activity_state.value,
                "session_type": s.session_type.value,
                "created_at": s.created_at.isoformat(),
                "title": s.title,
                "input_tokens": s.input_tokens,
                "output_tokens": s.output_tokens,
                "cache_creation_input_tokens": s.cache_creation_input_tokens,
                "cache_read_input_tokens": s.cache_read_input_tokens,
                "tool_input_tokens": s.tool_input_tokens,
                "tool_output_tokens": s.tool_output_tokens,
                "tool_cost_usd": s.tool_cost_usd,
                "main_project_path": s.main_project_path,
                "agent_type": s.agent_type,
            }
            for s in session_manager.list_all_sessions()
        ]

    return {"sessions": sessions}


@router.get(
    "/sessions/{session_id}/messages",
    summary="Get session messages",
    description=(
        "Returns the message history for a session. Checks in-memory buffer "
        "first, then falls back to the database for archived sessions."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def get_session_messages(
    session_id: str,
    request: Request,
    before: int | None = Query(None, description="Cursor: return messages with sequence < this value"),
    limit: int | None = Query(None, ge=1, le=200, description="Max messages to return (enables pagination)"),
) -> dict[str, Any]:
    session_manager = request.app.state.session_manager
    db_session_factory = request.app.state.db_session_factory

    # Check in-memory first
    session = session_manager.get_session(session_id)
    if session is not None:
        all_msgs = session.buffer.text_history
        total_count = len(all_msgs)

        if limit is not None:
            # Apply pagination to in-memory messages
            filtered = [m for m in all_msgs if m.sequence < before] if before is not None else list(all_msgs)
            # Take the last `limit` messages (most recent)
            page = filtered[-limit:] if len(filtered) > limit else filtered
            has_more = len(filtered) > limit
            next_cursor = page[0].sequence if has_more and page else None
        else:
            page = all_msgs
            has_more = False
            next_cursor = None

        messages = [
            {
                "type": msg.message_type.value,
                "sequence": msg.sequence,
                "content": msg.data.get("content", ""),
                "metadata": msg.data,
            }
            for msg in page
        ]
        return {
            "session_id": session_id,
            "messages": messages,
            "pagination": {
                "total_count": total_count,
                "has_more": has_more,
                "next_cursor": next_cursor,
            },
        }

    # Fall back to database
    if db_session_factory is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid session ID: {session_id}") from None

    async with db_session_factory() as db:
        # Verify the session exists in the DB
        session_row = await db.get(SessionModel, session_uuid)
        if session_row is None:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

        # Total count for pagination metadata
        count_stmt = (
            select(func.count()).select_from(SessionMessageModel).where(SessionMessageModel.session_id == session_uuid)
        )
        count_result = await db.execute(count_stmt)
        total_count = count_result.scalar_one()

        if limit is not None:
            # Paginated query: fetch `limit` messages ordered by sequence DESC
            stmt = select(SessionMessageModel).where(SessionMessageModel.session_id == session_uuid)
            if before is not None:
                stmt = stmt.where(SessionMessageModel.sequence < before)
            stmt = stmt.order_by(SessionMessageModel.sequence.desc()).limit(limit)

            result = await db.execute(stmt)
            rows = list(reversed(result.scalars().all()))  # Reverse to chronological order

            has_more = bool(rows) and rows[0].sequence > 1
            # Verify has_more by checking if there are messages before our oldest
            if has_more and rows:
                check_stmt = (
                    select(func.count())
                    .select_from(SessionMessageModel)
                    .where(SessionMessageModel.session_id == session_uuid)
                    .where(SessionMessageModel.sequence < rows[0].sequence)
                )
                check_result = await db.execute(check_stmt)
                has_more = check_result.scalar_one() > 0
            next_cursor = rows[0].sequence if has_more and rows else None
        else:
            # No pagination — return all messages
            stmt = (
                select(SessionMessageModel)
                .where(SessionMessageModel.session_id == session_uuid)
                .order_by(SessionMessageModel.sequence)
            )
            result = await db.execute(stmt)
            rows = list(result.scalars().all())
            has_more = False
            next_cursor = None

    messages = [
        {
            "type": row.message_type,
            "sequence": row.sequence,
            "content": row.content or "",
            "metadata": row.metadata_,
        }
        for row in rows
    ]
    return {
        "session_id": session_id,
        "messages": messages,
        "pagination": {
            "total_count": total_count,
            "has_more": has_more,
            "next_cursor": next_cursor,
        },
    }


@router.post(
    "/sessions/{session_id}/cancel",
    summary="Cancel a running session",
    description=(
        "Terminates a running session by killing any active subprocess, "
        "cancelling background tasks, and marking the session as CANCELLED."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def cancel_session(session_id: str, request: Request) -> dict[str, Any]:
    prompt_router: PromptRouter = request.app.state.prompt_router
    try:
        session = await prompt_router.cancel_session(session_id)
    except ValueError:
        # Session not in memory — check if it exists in the DB with a non-terminal status
        db_session_factory = request.app.state.db_session_factory
        if db_session_factory is not None:
            try:
                session_uuid = uuid.UUID(session_id)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid session ID: {session_id}") from None
            async with db_session_factory() as db:
                row = await db.get(SessionModel, session_uuid)
                if row is not None and row.status not in ("completed", "failed", "cancelled"):
                    row.status = "cancelled"
                    row.ended_at = datetime.now(UTC)
                    await db.commit()
                    logger.info("Session %s cancelled in DB (was not in memory)", session_id)
                    return {
                        "session_id": session_id,
                        "status": "cancelled",
                        "cancelled_at": row.ended_at.isoformat(),
                    }
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}") from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    logger.info("Session %s cancelled via HTTP API", session_id)
    return {
        "session_id": session.id,
        "status": session.status.value,
        "cancelled_at": session.ended_at.isoformat() if session.ended_at else None,
    }


@router.post(
    "/sessions/{session_id}/end",
    summary="End a session",
    description=(
        "Gracefully ends a session after user confirmation. "
        "Kills any active subprocess and marks the session as COMPLETED."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def end_session(session_id: str, request: Request) -> dict[str, Any]:
    prompt_router: PromptRouter = request.app.state.prompt_router
    try:
        session = await prompt_router.end_session(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}") from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    logger.info("Session %s ended via HTTP API", session_id)
    return {
        "session_id": session.id,
        "status": session.status.value,
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
    }


@router.post(
    "/sessions/{session_id}/pause",
    summary="Pause a session",
    description=(
        "Pauses an active session. The session's subprocess (if any) continues "
        "running and output is buffered. New prompts are rejected until resumed."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def pause_session(session_id: str, request: Request) -> dict[str, Any]:
    prompt_router: PromptRouter = request.app.state.prompt_router
    try:
        session = await prompt_router.pause_session(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}") from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    logger.info("Session %s paused via HTTP API", session_id)
    return {
        "session_id": session.id,
        "status": session.status.value,
        "paused_at": session.paused_at.isoformat() if session.paused_at else None,
    }


@router.post(
    "/sessions/{session_id}/interrupt",
    summary="Interrupt a running subprocess",
    description=(
        "Kills any active Claude Code or Codex subprocess in the session without "
        "pausing it. The session remains ACTIVE and ready to accept new prompts "
        "immediately after the interrupt. A null subprocess_status message is "
        "broadcast so clients can clear their subprocess indicator."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def interrupt_subprocess(session_id: str, request: Request) -> dict[str, Any]:
    prompt_router: PromptRouter = request.app.state.prompt_router
    try:
        session = await prompt_router.interrupt_subprocess(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}") from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    logger.info("Subprocess interrupted for session %s via HTTP API", session_id)
    return {
        "session_id": session.id,
        "status": session.status.value,
    }


@router.post(
    "/sessions/{session_id}/resume",
    summary="Resume a paused session",
    description=(
        "Resumes a paused session. The client can then subscribe to the session's "
        "output channel to receive all buffered messages, then send new prompts."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def resume_session(session_id: str, request: Request) -> dict[str, Any]:
    prompt_router: PromptRouter = request.app.state.prompt_router
    try:
        session = await prompt_router.resume_session(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}") from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    logger.info("Session %s resumed via HTTP API", session_id)
    return {
        "session_id": session.id,
        "status": session.status.value,
    }


@router.post(
    "/sessions/{session_id}/restore",
    summary="Restore an archived session",
    description=(
        "Restores a completed/failed/cancelled session from the database back to "
        "active state. Rebuilds conversation history and buffer. For Claude Code "
        "sessions, prepares the executor for lazy restart on the next message."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def restore_session(session_id: str, request: Request) -> dict[str, Any]:
    prompt_router: PromptRouter = request.app.state.prompt_router
    try:
        session = await prompt_router.restore_session(session_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}") from None
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    logger.info("Session %s restored via HTTP API", session_id)
    return {
        "session_id": session.id,
        "status": session.status.value,
        "session_type": session.session_type.value,
        "title": session.title,
    }


class RenameSessionRequest(BaseModel):
    """Body for the rename-session endpoint."""

    title: str | None = None


@router.patch(
    "/sessions/{session_id}/title",
    summary="Rename a session",
    description=(
        "Set or clear a session's title. The title must be at most 200 characters. "
        "Sending null or a blank string clears the title."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def rename_session(
    session_id: str,
    body: RenameSessionRequest,
    request: Request,
) -> dict[str, Any]:
    session_manager: SessionManager = request.app.state.session_manager
    db_session_factory = request.app.state.db_session_factory

    # Normalize: strip whitespace, blank → None
    title = body.title.strip() if body.title else None
    if title == "":
        title = None

    if title is not None and len(title) > 200:
        raise HTTPException(status_code=422, detail="Title must be at most 200 characters")

    # Try in-memory first
    session = session_manager.get_session(session_id)
    if session is not None:
        session.title = title
        # Also update DB if available (archived sessions may exist in DB)
        if db_session_factory is not None:
            try:
                session_uuid = uuid.UUID(session_id)
            except ValueError:
                pass
            else:
                async with db_session_factory() as db:
                    row = await db.get(SessionModel, session_uuid)
                    if row is not None:
                        row.title = title
                        await db.commit()
        logger.info("Session %s renamed to %r via HTTP API", session_id, title)
        return {"session_id": session_id, "title": title}

    # Fall back to DB-only (archived sessions)
    if db_session_factory is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid session ID: {session_id}") from None

    async with db_session_factory() as db:
        row = await db.get(SessionModel, session_uuid)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        row.title = title
        await db.commit()

    logger.info("Session %s (archived) renamed to %r via HTTP API", session_id, title)
    return {"session_id": session_id, "title": title}


@router.get(
    "/sessions/{session_id}/todos",
    summary="Get current todo items for a session",
    description=(
        "Returns the current TodoWrite task list for an in-memory session. "
        "Returns an empty list if the session has no todos or is archived."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def get_session_todos(session_id: str, request: Request) -> dict[str, Any]:
    session_manager: SessionManager = request.app.state.session_manager
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"session_id": session_id, "todos": session.todos}


class SetSessionWorktreeRequest(BaseModel):
    """Body for the set-session-worktree endpoint."""

    path: str | None = None


@router.patch(
    "/sessions/{session_id}/worktree",
    summary="Set the active worktree for a session",
    description=(
        "Set or clear the selected worktree path for a session. "
        "When set, Claude Code and Codex agents will use this path as their "
        "working directory instead of the path provided by the LLM tool call. "
        "Send `path: null` to clear the selection and restore default behaviour."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def set_session_worktree(
    session_id: str,
    body: SetSessionWorktreeRequest,
    request: Request,
) -> dict[str, Any]:
    session_manager: SessionManager = request.app.state.session_manager
    session = session_manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    path = body.path.strip() if body.path else None
    if path == "":
        path = None

    if path is not None:
        session.metadata["selected_worktree_path"] = path
    else:
        session.metadata.pop("selected_worktree_path", None)

    session_manager.broadcast_session_update(session)
    logger.info(
        "Session %s selected_worktree_path set to %r via HTTP API", session_id, path
    )
    return {"session_id": session_id, "selected_worktree_path": path}
