"""Pydantic schemas for authentication endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class _ClaudeCodeLoginBody(BaseModel):
    code: str
