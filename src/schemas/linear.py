"""Pydantic schemas for Linear integration endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class TestLinearConnectionRequest(BaseModel):
    api_key: str


class CreateIssueRequest(BaseModel):
    title: str
    description: str | None = None
    priority: int = 0
    team_id: str | None = None  # Required when LINEAR_TEAM_ID is not configured


class UpdateIssueRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    state_id: str | None = None
    priority: int | None = None


class LinkTaskRequest(BaseModel):
    task_id: str
