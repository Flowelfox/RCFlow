"""Unified badge management for RCFlow sessions.

A *badge* is a typed, renderable indicator attached to a session that conveys
one discrete property to the client (status, worker, agent, project, worktree,
caveman mode, etc.).

:class:`BadgeSpec` is the serialisable value object sent inside
``session_update`` messages.  :class:`BadgeState` computes the authoritative
badge list from an :class:`~src.core.session.ActiveSession` instance.  It is a
pure, stateless compute class — all business logic for which badges to show
lives here so that adding a new badge type requires a single new ``_*_badge``
method and one call site in :meth:`BadgeState.compute`.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from src.core.session import ActiveSession

logger = logging.getLogger(__name__)


@dataclass
class BadgeSpec:
    """Serialisable value object describing a single session badge.

    Attributes:
        type: Stable string identifier (e.g. ``"status"``, ``"worker"``).
        label: Human-readable text displayed on the chip.
        priority: Sort order within the badge bar; lower values appear first.
        visible: Whether the client should display this badge.
        interactive: Whether tapping the badge triggers an action on the client.
        payload: Type-specific data; opaque dict on the wire, typed on the client.
    """

    type: str
    label: str
    priority: int
    visible: bool
    interactive: bool
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict suitable for JSON serialisation."""
        return asdict(self)


class BadgePriority:
    """Canonical priority constants for built-in badge types."""

    STATUS = 0
    WORKER = 10
    AGENT = 20
    PROJECT = 30
    WORKTREE = 40
    CAVEMAN = 50


class BadgeState:
    """Computes the authoritative badge list for a session.

    This class is stateless — instantiate once per :class:`SessionManager`
    and call :meth:`compute` on every broadcast.
    """

    def compute(
        self,
        session: ActiveSession,
        *,
        worker_id: str | None = None,
        worker_name: str | None = None,
    ) -> list[BadgeSpec]:
        """Return the full badge list for *session*.

        Args:
            session: The live session whose state drives badge visibility.
            worker_id: Backend identifier of the worker (server-side ``backend_id``).
            worker_name: Human-readable worker name if known; falls back to
                *worker_id* then ``"unknown"``.
        """
        badges: list[BadgeSpec] = []
        try:
            badges.append(self._status_badge(session))
        except Exception:
            logger.warning("Failed to compute status badge for %s", session.id, exc_info=True)

        try:
            badges.append(self._worker_badge(session, worker_id=worker_id, worker_name=worker_name))
        except Exception:
            logger.warning("Failed to compute worker badge for %s", session.id, exc_info=True)

        try:
            if b := self._agent_badge(session):
                badges.append(b)
        except Exception:
            logger.warning("Failed to compute agent badge for %s", session.id, exc_info=True)

        try:
            if b := self._project_badge(session):
                badges.append(b)
        except Exception:
            logger.warning("Failed to compute project badge for %s", session.id, exc_info=True)

        try:
            if b := self._worktree_badge(session):
                badges.append(b)
        except Exception:
            logger.warning("Failed to compute worktree badge for %s", session.id, exc_info=True)

        try:
            if b := self._caveman_badge(session):
                badges.append(b)
        except Exception:
            logger.warning("Failed to compute caveman badge for %s", session.id, exc_info=True)

        return badges

    # ------------------------------------------------------------------
    # Individual badge builders
    # ------------------------------------------------------------------

    def _status_badge(self, session: ActiveSession) -> BadgeSpec:
        return BadgeSpec(
            type="status",
            label=session.status.value,
            priority=BadgePriority.STATUS,
            visible=True,
            interactive=False,
            payload={"activity_state": session.activity_state.value},
        )

    def _worker_badge(
        self,
        session: ActiveSession,
        *,
        worker_id: str | None,
        worker_name: str | None,
    ) -> BadgeSpec:
        label = worker_name or worker_id or "unknown"
        return BadgeSpec(
            type="worker",
            label=label,
            priority=BadgePriority.WORKER,
            visible=True,
            interactive=False,
            payload={"worker_id": worker_id or ""},
        )

    # Map internal agent_type identifiers to user-facing badge labels.
    _AGENT_DISPLAY_LABELS: ClassVar[dict[str, str]] = {
        "claude_code": "ClaudeCode",
        "codex": "Codex",
        "opencode": "OpenCode",
    }

    def _agent_badge(self, session: ActiveSession) -> BadgeSpec | None:
        agent = session.agent_type
        if not agent:
            return None
        return BadgeSpec(
            type="agent",
            label=self._AGENT_DISPLAY_LABELS.get(agent, agent),
            priority=BadgePriority.AGENT,
            visible=True,
            interactive=False,
            payload={"agent_type": agent},
        )

    def _project_badge(self, session: ActiveSession) -> BadgeSpec | None:
        path = session.main_project_path
        error = session.project_name_error
        # Show the project badge when a path is resolved OR when there is an
        # error (so the user can see the invalid project name in red).
        if not path and not error:
            return None
        label = path.rstrip("/").split("/")[-1] if path else "unknown"
        return BadgeSpec(
            type="project",
            label=label,
            priority=BadgePriority.PROJECT,
            visible=True,
            interactive=False,
            payload={"path": path, "error": error},
        )

    def _worktree_badge(self, session: ActiveSession) -> BadgeSpec | None:
        wt = session.metadata.get("worktree")
        if not wt:
            return None
        # wt may be a plain dict (serialised from metadata) or a WorktreeInfo
        # dataclass; handle both.
        if hasattr(wt, "branch"):
            branch = wt.branch
            repo_path = getattr(wt, "repo_path", None)
            last_action = getattr(wt, "last_action", "")
            base = getattr(wt, "base", None)
            payload: dict[str, Any] = {
                "repo_path": repo_path,
                "branch": branch,
                "base": base,
                "last_action": last_action,
            }
        else:
            branch = wt.get("branch")
            payload = dict(wt)
        label = branch or (wt.get("repo_path") if isinstance(wt, dict) else None) or "worktree"
        return BadgeSpec(
            type="worktree",
            label=label,
            priority=BadgePriority.WORKTREE,
            visible=True,
            interactive=False,
            payload=payload,
        )

    def _caveman_badge(self, session: ActiveSession) -> BadgeSpec | None:
        if not session.metadata.get("caveman_mode", False):
            return None
        return BadgeSpec(
            type="caveman",
            label="Caveman",
            priority=BadgePriority.CAVEMAN,
            visible=True,
            interactive=False,
            payload={"level": session.metadata.get("caveman_level", "full")},
        )

    def compute_archived(
        self,
        status: str,
        *,
        worker_id: str | None = None,
        caveman_mode: bool = False,
        caveman_level: str = "full",
    ) -> list[BadgeSpec]:
        """Return a minimal badge list for DB-archived sessions.

        Archived sessions are no longer in memory, so only flat fields are
        available.  This produces the status and worker badges (always) plus
        the caveman badge when the session had it enabled.  The worker badge
        label is the raw ``backend_id``; the client replaces it with the
        user-configured friendly name during session-list processing.

        Args:
            status: Serialised session status string (e.g. ``"completed"``).
            worker_id: Backend identifier of the worker that ran the session.
            caveman_mode: Whether caveman mode was active for this session.
            caveman_level: Caveman mode level (default ``"full"``).
        """
        badges: list[BadgeSpec] = [
            BadgeSpec(
                type="status",
                label=status,
                priority=BadgePriority.STATUS,
                visible=True,
                interactive=False,
                payload={"activity_state": "idle"},
            ),
            BadgeSpec(
                type="worker",
                label=worker_id or "unknown",
                priority=BadgePriority.WORKER,
                visible=True,
                interactive=False,
                payload={"worker_id": worker_id or ""},
            ),
        ]
        if caveman_mode:
            badges.append(
                BadgeSpec(
                    type="caveman",
                    label="Caveman",
                    priority=BadgePriority.CAVEMAN,
                    visible=True,
                    interactive=False,
                    payload={"level": caveman_level},
                )
            )
        return badges
