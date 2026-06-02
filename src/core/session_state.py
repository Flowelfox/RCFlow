"""Composable sub-state objects for :class:`~src.core.session.ActiveSession`.

ActiveSession owns one instance of each of these and re-exposes their fields
through delegating properties, so the historical attribute surface
(``session.input_tokens``, ``session.scheduled_wakes``, ``session.mirror_add_wake``,
``session.subprocess_started_at`` …) is preserved unchanged for callers.

Splitting the concerns out keeps each one small and independently testable:

* :class:`SessionTokenAccumulator` — running token / cost totals.
* :class:`SessionSubprocessTracker` — transient "what is the agent running" fields.
* :class:`SessionPendingState` — in-memory mirror of queued user messages.
* :class:`SessionWakeMirror` — in-memory mirror of pending ``ScheduleWakeup`` calls.

The dataclasses ``MonitorState``, ``PendingMessage`` and ``ScheduledWake`` live
here (rather than in ``session.py``) so this module has no dependency on
``ActiveSession`` and the import graph stays acyclic.  ``session.py`` re-exports
them for backwards compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime


@dataclass
class MonitorState:
    """In-memory tracking for a live Claude Code ``Monitor`` tool invocation.

    Keyed by the Claude Code ``tool_use_id`` so concurrent monitors stay
    separated.  Each stdout-line batch from the watched script becomes a
    ``MONITOR_EVENT`` buffer message; the entry is removed when the watch
    terminates (script exit, timeout, cancel, or session end).
    """

    description: str
    command: str
    timeout_ms: int
    persistent: bool
    started_at: datetime
    event_count: int = 0


@dataclass
class PendingMessage:
    """A user message queued while the agent was busy with a prior turn.

    Mirrors one row of the ``session_pending_messages`` DB table.  The
    authoritative store is the DB; this in-memory copy lets the server
    answer queue queries and broadcasts without a round-trip.  See
    ``Queued User Messages`` in ``docs/design/sessions.md``.
    """

    queued_id: str
    position: int
    content: str
    display_content: str
    attachments_path: str | None
    project_name: str | None
    selected_worktree_path: str | None
    task_id: str | None
    submitted_at: datetime
    updated_at: datetime

    def to_snapshot(self) -> dict[str, Any]:
        """Lightweight dict included in ``session_update.queued_messages``."""
        return {
            "queued_id": self.queued_id,
            "position": self.position,
            "display_content": self.display_content,
            "submitted_at": self.submitted_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass
class ScheduledWake:
    """A pending ``ScheduleWakeup`` call the agent placed on itself.

    Mirrors one row of the ``session_scheduled_wakes`` DB table.  The
    authoritative store is the DB; this in-memory copy lets the
    badge / inline card render without a query each broadcast.  See
    ``Scheduled Wakeups`` in ``docs/design/sessions.md``.
    """

    wake_id: str
    prompt: str
    reason: str
    fire_at: datetime
    created_at: datetime

    def to_snapshot(self) -> dict[str, Any]:
        """Lightweight dict included in ``session_update.scheduled_wakes``."""
        return {
            "wake_id": self.wake_id,
            "prompt": self.prompt,
            "reason": self.reason,
            "fire_at": self.fire_at.isoformat(),
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class SessionTokenAccumulator:
    """Running token-usage and cost totals across all turns of a session."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    # Tool agent token usage (Claude Code / Codex / OpenCode)
    tool_input_tokens: int = 0
    tool_output_tokens: int = 0
    tool_cost_usd: float = 0.0


@dataclass
class SessionSubprocessTracker:
    """Transient "what is the managed agent running" fields.

    Not persisted to the DB; always cleared after a session restore and
    whenever the subprocess stops (normal end, cancel, pause, crash).
    """

    started_at: datetime | None = None
    current_tool: str | None = None
    type: str | None = None
    display_name: str | None = None
    working_directory: str | None = None

    def clear(self) -> None:
        """Reset every tracking field to ``None``."""
        self.started_at = None
        self.current_tool = None
        self.type = None
        self.display_name = None
        self.working_directory = None


class SessionPendingState:
    """In-memory mirror of the ``session_pending_messages`` table for a session.

    Ordered by ``position`` ascending (FIFO).  Mutations go through
    :class:`~src.core.pending_store.SessionPendingMessageStore`, which writes
    the DB then calls these helpers; they only touch the in-memory list and are
    safe to call inside the store's DB transaction.
    """

    def __init__(self) -> None:
        self.messages: list[PendingMessage] = []

    def snapshot(self) -> list[dict[str, Any]]:
        """Return the current queue as snapshot dicts (for ``session_update``)."""
        return [p.to_snapshot() for p in self.messages]

    def _find_index(self, queued_id: str) -> int | None:
        for idx, p in enumerate(self.messages):
            if p.queued_id == queued_id:
                return idx
        return None

    def add(self, entry: PendingMessage) -> None:
        """Insert *entry* into the in-memory queue at its ``position``."""
        for idx, existing in enumerate(self.messages):
            if existing.position > entry.position:
                self.messages.insert(idx, entry)
                return
        self.messages.append(entry)

    def update(self, queued_id: str, content: str, display_content: str, updated_at: datetime) -> None:
        """Update text fields on a queued entry."""
        idx = self._find_index(queued_id)
        if idx is None:
            return
        entry = self.messages[idx]
        entry.content = content
        entry.display_content = display_content
        entry.updated_at = updated_at

    def remove(self, queued_id: str) -> PendingMessage | None:
        """Remove the named entry and renumber positions densely from 0."""
        idx = self._find_index(queued_id)
        if idx is None:
            return None
        removed = self.messages.pop(idx)
        for new_pos, entry in enumerate(self.messages):
            entry.position = new_pos
        return removed

    def clear(self) -> list[PendingMessage]:
        """Drop all queued entries and return them (for per-entry cleanup)."""
        dropped = list(self.messages)
        self.messages.clear()
        return dropped


class SessionWakeMirror:
    """In-memory mirror of the agent's pending ``ScheduleWakeup`` calls.

    Ordered by ``fire_at`` ascending.  The badge label, the inline wakeup card,
    and ``broadcast_session_update`` all read from this list.
    """

    def __init__(self) -> None:
        self.wakes: list[ScheduledWake] = []

    def snapshot(self) -> list[dict[str, Any]]:
        """Return the pending wake list as snapshot dicts."""
        return [w.to_snapshot() for w in self.wakes]

    def _find_index(self, wake_id: str) -> int | None:
        for idx, w in enumerate(self.wakes):
            if w.wake_id == wake_id:
                return idx
        return None

    def add(self, entry: ScheduledWake) -> None:
        """Insert *entry* into the wake list ordered by ``fire_at``."""
        for idx, existing in enumerate(self.wakes):
            if existing.fire_at > entry.fire_at:
                self.wakes.insert(idx, entry)
                return
        self.wakes.append(entry)

    def remove(self, wake_id: str) -> ScheduledWake | None:
        """Remove and return the named wake, or None if not present."""
        idx = self._find_index(wake_id)
        if idx is None:
            return None
        return self.wakes.pop(idx)

    def clear(self) -> list[ScheduledWake]:
        """Drop all pending wakes; used on session end / cancel."""
        dropped = list(self.wakes)
        self.wakes.clear()
        return dropped


__all__ = [
    "MonitorState",
    "PendingMessage",
    "ScheduledWake",
    "SessionPendingState",
    "SessionSubprocessTracker",
    "SessionTokenAccumulator",
    "SessionWakeMirror",
]
