"""Native task tools: ``task_list`` / ``task_create`` / ``task_update`` (exposed as ``rcflow_task_*``).

Agents manage RCFlow tasks (the Tasks tab / Linear-synced board) from inside a
session. Create/update mutate the board — the bridge gates them (not
``agent_safe``); list is read-only.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from src.database.models import Task as TaskModel
from src.database.models import TaskSession as TaskSessionModel

if TYPE_CHECKING:
    from src.core.native_tools import NativeToolContext

# Statuses an AI-initiated update may set (mirrors the REST route's guard:
# agents may not mark a task done).
_AI_ALLOWED_STATUSES = {"todo", "in_progress", "review"}


def _task_summary(task: TaskModel) -> dict[str, Any]:
    return {
        "task_id": str(task.id),
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "source": task.source,
    }


def _backend_id(ctx: NativeToolContext) -> str:
    return ctx.router._settings.RCFLOW_BACKEND_ID if ctx.router._settings else ""


def _require_db(ctx: NativeToolContext):
    factory = ctx.router._db_session_factory
    if factory is None:
        raise RuntimeError("Task management requires a database (not available in this mode)")
    return factory


async def list_tasks(ctx: NativeToolContext, params: dict[str, Any]) -> str:
    """List tasks for this worker, optionally filtered by ``status``."""
    factory = _require_db(ctx)
    status = params.get("status")
    async with factory() as db:
        stmt = select(TaskModel).where(TaskModel.backend_id == _backend_id(ctx))
        if status:
            stmt = stmt.where(TaskModel.status == str(status))
        stmt = stmt.order_by(TaskModel.updated_at.desc())
        rows = (await db.execute(stmt)).scalars().all()
    return json.dumps([_task_summary(t) for t in rows], indent=2)


async def create_task(ctx: NativeToolContext, params: dict[str, Any]) -> str:
    """Create a task (source=ai) and attach it to the current session."""
    subject = str(params.get("subject", "")).strip()
    if not subject:
        raise ValueError("task_create requires a non-empty 'subject'")
    description = str(params.get("description", "")).strip() or None
    factory = _require_db(ctx)

    async with factory() as db:
        task = TaskModel(
            backend_id=_backend_id(ctx),
            title=subject[:300],
            description=description,
            status="todo",
            source="ai",
        )
        db.add(task)
        await db.flush()
        with contextlib.suppress(ValueError):
            # non-UUID session id (test/transient) — task still created
            db.add(TaskSessionModel(task_id=task.id, session_id=uuid.UUID(ctx.session.id)))
        await db.commit()
        result = _task_summary(task)

    if ctx.router._session_manager is not None:
        ctx.router._session_manager.broadcast_task_update(result)
    return f"Task created: {result['task_id']} — {subject}"


async def update_task(ctx: NativeToolContext, params: dict[str, Any]) -> str:
    """Update a task's status and/or title."""
    task_id = str(params.get("task_id", "")).strip()
    if not task_id:
        raise ValueError("task_update requires 'task_id'")
    try:
        task_uuid = uuid.UUID(task_id)
    except ValueError as e:
        raise ValueError(f"Invalid task_id: {task_id}") from e

    new_status = params.get("status")
    new_title = params.get("subject")
    if new_status is None and new_title is None:
        raise ValueError("task_update needs at least one of 'status' or 'subject'")
    if new_status is not None and str(new_status) not in _AI_ALLOWED_STATUSES:
        raise ValueError(f"status must be one of {sorted(_AI_ALLOWED_STATUSES)} (agents may not mark tasks done)")

    factory = _require_db(ctx)
    async with factory() as db:
        task = await db.get(TaskModel, task_uuid, options=[selectinload(TaskModel.sessions)])
        if task is None or task.backend_id != _backend_id(ctx):
            raise ValueError(f"Task not found: {task_id}")
        if new_status is not None:
            task.status = str(new_status)
        if new_title is not None:
            task.title = str(new_title)[:300]
        await db.commit()
        result = _task_summary(task)

    if ctx.router._session_manager is not None:
        ctx.router._session_manager.broadcast_task_update(result)
    return f"Task updated: {result['task_id']} (status={result['status']})"
