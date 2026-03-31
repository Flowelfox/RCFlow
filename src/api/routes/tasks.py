from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from src.api.deps import verify_http_api_key
from src.models.db import Session as SessionModel
from src.models.db import Task as TaskModel
from src.models.db import TaskSession as TaskSessionModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.config import Settings
    from src.core.session import SessionManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Tasks"])

# ── Status-transition rules ──────────────────────────────────────────────

VALID_TASK_TRANSITIONS: dict[str, set[str]] = {
    "todo": {"in_progress", "done"},
    "in_progress": {"todo", "review", "done"},
    "review": {"in_progress", "done"},
    "done": {"todo", "in_progress"},
}

AI_FORBIDDEN_STATUSES = {"done"}


def validate_status_transition(current: str, new: str, *, source: str | None = None) -> None:
    """Raise HTTPException if the transition is invalid."""
    if current == new:
        return
    allowed = VALID_TASK_TRANSITIONS.get(current)
    if allowed is None or new not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Invalid status transition: {current!r} -> {new!r}",
        )
    if source == "ai" and new in AI_FORBIDDEN_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"AI agents cannot set task status to {new!r}",
        )


# ── Serialisation helpers ────────────────────────────────────────────────


def _task_to_dict(task: TaskModel) -> dict[str, Any]:
    """Serialise a Task ORM instance (with loaded sessions) to a JSON-friendly dict."""
    sessions: list[dict[str, Any]] = []
    for ts in getattr(task, "sessions", []):
        sessions.append(
            {
                "session_id": str(ts.id),
                "title": ts.title,
                "status": ts.status,
                "attached_at": "",  # filled below if we have the link row
            }
        )
    return {
        "task_id": str(task.id),
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "source": task.source,
        "created_at": task.created_at.isoformat() if task.created_at else "",
        "updated_at": task.updated_at.isoformat() if task.updated_at else "",
        "sessions": sessions,
    }


async def _task_to_dict_full(task: TaskModel, db: AsyncSession) -> dict[str, Any]:
    """Serialise a Task with its session refs (including attached_at)."""
    stmt = (
        select(TaskSessionModel, SessionModel)
        .join(SessionModel, TaskSessionModel.session_id == SessionModel.id)
        .where(TaskSessionModel.task_id == task.id)
        .order_by(TaskSessionModel.attached_at.desc())
    )
    result = await db.execute(stmt)
    sessions: list[dict[str, Any]] = []
    for ts_row, sess_row in result.all():
        sessions.append(
            {
                "session_id": str(sess_row.id),
                "title": sess_row.title,
                "status": sess_row.status,
                "attached_at": ts_row.attached_at.isoformat() if ts_row.attached_at else "",
            }
        )
    return {
        "task_id": str(task.id),
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "source": task.source,
        "created_at": task.created_at.isoformat() if task.created_at else "",
        "updated_at": task.updated_at.isoformat() if task.updated_at else "",
        "sessions": sessions,
    }


# ── Request models ───────────────────────────────────────────────────────


class CreateTaskRequest(BaseModel):
    """Body for POST /api/tasks."""

    title: str
    description: str | None = None
    source: str = "user"
    session_id: str | None = None


class UpdateTaskRequest(BaseModel):
    """Body for PATCH /api/tasks/{task_id}."""

    title: str | None = None
    description: str | None = None
    status: str | None = None


class AttachSessionRequest(BaseModel):
    """Body for POST /api/tasks/{task_id}/sessions."""

    session_id: str


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get(
    "/tasks",
    summary="List tasks",
    description=(
        "Returns all tasks for the current backend. "
        "Supports optional ?status= and ?source= filters. Sorted by updated_at descending."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def list_tasks(
    request: Request,
    status: str | None = Query(None, description="Filter by task status (todo, in_progress, review, done)"),
    source: str | None = Query(None, description="Filter by task source (ai, user)"),
) -> dict[str, Any]:
    """List all tasks for the current backend."""
    settings: Settings = request.app.state.settings
    db_session_factory = request.app.state.db_session_factory
    if db_session_factory is None:
        return {"tasks": []}

    async with db_session_factory() as db:
        stmt = (
            select(TaskModel)
            .where(TaskModel.backend_id == settings.RCFLOW_BACKEND_ID)
            .order_by(TaskModel.updated_at.desc())
        )
        if status:
            stmt = stmt.where(TaskModel.status == status)
        if source:
            stmt = stmt.where(TaskModel.source == source)
        result = await db.execute(stmt)
        tasks = result.scalars().all()
        return {"tasks": [await _task_to_dict_full(t, db) for t in tasks]}


@router.get(
    "/tasks/{task_id}",
    summary="Get a single task",
    description="Returns a task with its attached sessions.",
    dependencies=[Depends(verify_http_api_key)],
)
async def get_task(task_id: str, request: Request) -> dict[str, Any]:
    """Get a single task by ID."""
    db_session_factory = request.app.state.db_session_factory
    if db_session_factory is None:
        raise HTTPException(status_code=404, detail="Database not configured")

    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid task ID: {task_id}") from None

    async with db_session_factory() as db:
        task = await db.get(TaskModel, task_uuid)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        return await _task_to_dict_full(task, db)


@router.post(
    "/tasks",
    summary="Create a task",
    description=(
        "Creates a new task. Optionally attaches it to a session on creation. "
        "Used by LLM agents (source: 'ai') or for manual user creation (source: 'user')."
    ),
    dependencies=[Depends(verify_http_api_key)],
    status_code=201,
)
async def create_task(body: CreateTaskRequest, request: Request) -> dict[str, Any]:
    """Create a new task."""
    settings: Settings = request.app.state.settings
    db_session_factory = request.app.state.db_session_factory
    if db_session_factory is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    now = datetime.now(UTC)
    task = TaskModel(
        backend_id=settings.RCFLOW_BACKEND_ID,
        title=body.title,
        description=body.description,
        status="todo",
        source=body.source,
        created_at=now,
        updated_at=now,
    )

    async with db_session_factory() as db:
        db.add(task)
        await db.flush()

        if body.session_id:
            try:
                session_uuid = uuid.UUID(body.session_id)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"Invalid session ID: {body.session_id}") from None
            # Ensure session row exists in DB (may still be in-memory only)
            existing_session = await db.get(SessionModel, session_uuid)
            if existing_session is None:
                sm: SessionManager = request.app.state.session_manager
                active = sm.get_session(str(session_uuid))
                if active is None:
                    raise HTTPException(status_code=404, detail=f"Session not found: {body.session_id}")
                db.add(
                    SessionModel(
                        id=session_uuid,
                        backend_id=settings.RCFLOW_BACKEND_ID,
                        created_at=active.created_at,
                        ended_at=active.ended_at,
                        session_type=active.session_type.value,
                        status=active.status.value,
                        title=active.title,
                        metadata_={},
                    )
                )
                await db.flush()
            link = TaskSessionModel(task_id=task.id, session_id=session_uuid)
            db.add(link)

        await db.commit()
        result = await _task_to_dict_full(task, db)

    # Broadcast task update
    session_manager: SessionManager = request.app.state.session_manager
    session_manager.broadcast_task_update(result)

    return result


@router.patch(
    "/tasks/{task_id}",
    summary="Update a task",
    description=(
        "Update task fields (title, description, status). "
        "Status transitions are validated. Returns 409 for invalid transitions."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def update_task(task_id: str, body: UpdateTaskRequest, request: Request) -> dict[str, Any]:
    """Update a task's fields."""
    db_session_factory = request.app.state.db_session_factory
    if db_session_factory is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid task ID: {task_id}") from None

    async with db_session_factory() as db:
        task = await db.get(TaskModel, task_uuid)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

        changed = False
        if body.title is not None and body.title != task.title:
            task.title = body.title
            changed = True
        if body.description is not None and body.description != task.description:
            task.description = body.description
            changed = True
        if body.status is not None and body.status != task.status:
            validate_status_transition(task.status, body.status)
            task.status = body.status
            changed = True

        if changed:
            task.updated_at = datetime.now(UTC)
            await db.commit()

        result = await _task_to_dict_full(task, db)

    # Broadcast
    session_manager: SessionManager = request.app.state.session_manager
    session_manager.broadcast_task_update(result)

    return result


@router.delete(
    "/tasks/{task_id}",
    summary="Delete a task",
    description="Deletes a task and all its session associations.",
    dependencies=[Depends(verify_http_api_key)],
)
async def delete_task(task_id: str, request: Request) -> dict[str, str]:
    """Delete a task."""
    db_session_factory = request.app.state.db_session_factory
    if db_session_factory is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid task ID: {task_id}") from None

    async with db_session_factory() as db:
        task = await db.get(TaskModel, task_uuid)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        await db.delete(task)
        await db.commit()

    # Broadcast deletion
    session_manager: SessionManager = request.app.state.session_manager
    session_manager.broadcast_task_deleted(task_id)

    return {"task_id": task_id, "deleted": "ok"}


@router.post(
    "/tasks/{task_id}/sessions",
    summary="Attach a session to a task",
    description="Creates a link between a task and a session.",
    dependencies=[Depends(verify_http_api_key)],
    status_code=201,
)
async def attach_session_to_task(
    task_id: str,
    body: AttachSessionRequest,
    request: Request,
) -> dict[str, Any]:
    """Attach a session to a task."""
    db_session_factory = request.app.state.db_session_factory
    if db_session_factory is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid task ID: {task_id}") from None

    try:
        session_uuid = uuid.UUID(body.session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid session ID: {body.session_id}") from None

    async with db_session_factory() as db:
        task = await db.get(TaskModel, task_uuid)
        if task is None:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")

        # Check for existing link
        existing = await db.execute(
            select(TaskSessionModel).where(
                TaskSessionModel.task_id == task_uuid,
                TaskSessionModel.session_id == session_uuid,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="Session already attached to this task")

        # Ensure the session row exists in the DB before creating the FK reference.
        # Sessions live in-memory and are only persisted on archive/shutdown,
        # so we create a minimal placeholder row if it doesn't exist yet.
        existing_session = await db.get(SessionModel, session_uuid)
        if existing_session is None:
            session_manager: SessionManager = request.app.state.session_manager
            active = session_manager.get_session(str(session_uuid))
            if active is None:
                raise HTTPException(status_code=404, detail=f"Session not found: {body.session_id}")
            db.add(
                SessionModel(
                    id=session_uuid,
                    backend_id=request.app.state.settings.RCFLOW_BACKEND_ID,
                    created_at=active.created_at,
                    ended_at=active.ended_at,
                    session_type=active.session_type.value,
                    status=active.status.value,
                    title=active.title,
                    metadata_={},
                )
            )
            await db.flush()

        link = TaskSessionModel(task_id=task_uuid, session_id=session_uuid)
        db.add(link)
        task.updated_at = datetime.now(UTC)
        await db.commit()

        result = await _task_to_dict_full(task, db)

    session_manager: SessionManager = request.app.state.session_manager
    session_manager.broadcast_task_update(result)

    return result


@router.delete(
    "/tasks/{task_id}/sessions/{session_id}",
    summary="Detach a session from a task",
    description="Removes the link between a task and a session.",
    dependencies=[Depends(verify_http_api_key)],
)
async def detach_session_from_task(
    task_id: str,
    session_id: str,
    request: Request,
) -> dict[str, Any]:
    """Detach a session from a task."""
    db_session_factory = request.app.state.db_session_factory
    if db_session_factory is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid task ID: {task_id}") from None

    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid session ID: {session_id}") from None

    async with db_session_factory() as db:
        link = await db.execute(
            select(TaskSessionModel).where(
                TaskSessionModel.task_id == task_uuid,
                TaskSessionModel.session_id == session_uuid,
            )
        )
        link_row = link.scalar_one_or_none()
        if link_row is None:
            raise HTTPException(status_code=404, detail="Session is not attached to this task")

        await db.delete(link_row)

        task = await db.get(TaskModel, task_uuid)
        if task is not None:
            task.updated_at = datetime.now(UTC)
        await db.commit()

        if task is not None:
            result = await _task_to_dict_full(task, db)
        else:
            result = {"task_id": task_id}

    session_manager: SessionManager = request.app.state.session_manager
    session_manager.broadcast_task_update(result)

    return result
