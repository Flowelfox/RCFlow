"""Task status-transition rules — domain data, not HTTP.

Lives in the services layer so both the REST routes and the background
task-update path (`src.core.background_tasks`) import it *downward* instead of
core reaching up into `src.api` for it.
"""

from __future__ import annotations

# Allowed status transitions: current -> {permitted next states}.
VALID_TASK_TRANSITIONS: dict[str, set[str]] = {
    "todo": {"in_progress", "done"},
    "in_progress": {"todo", "review", "done"},
    "review": {"in_progress", "done"},
    "done": {"todo", "in_progress"},
}

# Statuses an AI agent may never set directly.
AI_FORBIDDEN_STATUSES = {"done"}
