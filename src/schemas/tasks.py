"""Pydantic schemas for task management endpoints."""

from __future__ import annotations

from pydantic import BaseModel


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
    plan_artifact_id: str | None = None  # None means "not provided"; explicit null clears the plan link


class StartPlanRequest(BaseModel):
    """Body for POST /api/tasks/{task_id}/plan."""

    project_name: str | None = None
    selected_worktree_path: str | None = None


class AttachSessionRequest(BaseModel):
    """Body for POST /api/tasks/{task_id}/sessions."""

    session_id: str
