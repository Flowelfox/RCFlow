"""Timer registry that fires Claude Code's ``ScheduleWakeup`` callbacks.

One :class:`WakeupScheduler` instance is owned by the
:class:`~src.core.prompt_router.PromptRouter`.  For each pending wake
it owns a single ``asyncio.create_task`` that sleeps until
``fire_at`` and then routes the wake's prompt back into the agent via
:meth:`PromptRouter.fire_pending_wakeup`.

Cancelling a wake or ending the session removes the timer.  The
scheduler is purely in-memory — persistence and restart-recovery
live on :class:`~src.core.wakeup_store.SessionScheduledWakeStore`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.session import ScheduledWake

logger = logging.getLogger(__name__)


# Type alias for the "fire this wake" callback the scheduler hands the
# wake off to when its timer expires.
FireCallback = Callable[[str, "ScheduledWake"], Coroutine[None, None, None]]


class WakeupScheduler:
    """Per-wake asyncio timer registry."""

    def __init__(self, on_fire: FireCallback) -> None:
        # ``on_fire`` receives ``(session_id, wake)`` when the timer
        # expires.  The callback is responsible for routing the wake's
        # prompt back into the agent and marking the wake fired in the
        # store.
        self._on_fire = on_fire
        self._tasks: dict[str, asyncio.Task[None]] = {}

    # ------------------------------------------------------------------
    # Public API

    def arm(self, session_id: str, wake: ScheduledWake) -> None:
        """Start the timer for *wake* on behalf of *session_id*.

        Replaces any existing timer for the same ``wake_id`` so
        re-arming after a restart is idempotent.  Past-due wakes
        ``asyncio.sleep(0)`` and fire on the next event loop tick.
        """
        existing = self._tasks.pop(wake.wake_id, None)
        if existing is not None and not existing.done():
            existing.cancel()

        delay = max(0.0, (wake.fire_at - datetime.now(UTC)).total_seconds())
        task = asyncio.create_task(self._sleep_then_fire(session_id, wake, delay))
        self._tasks[wake.wake_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(wake.wake_id, None))

    def cancel(self, wake_id: str) -> None:
        """Cancel the timer for *wake_id* (no-op if not armed)."""
        task = self._tasks.pop(wake_id, None)
        if task is not None and not task.done():
            task.cancel()

    def cancel_all_for_session(self, session_id: str, wake_ids: list[str]) -> None:
        """Bulk cancel — used by ``cancel_session`` / ``end_session``."""
        for wake_id in wake_ids:
            self.cancel(wake_id)
        # ``session_id`` is captured purely for symmetry / future
        # debug logging; the scheduler doesn't index by session today.
        _ = session_id

    def pending_count(self) -> int:
        """Return the pending count."""
        return len(self._tasks)

    # ------------------------------------------------------------------
    # Internals

    async def _sleep_then_fire(
        self,
        session_id: str,
        wake: ScheduledWake,
        delay: float,
    ) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        try:
            await self._on_fire(session_id, wake)
        except Exception:
            logger.exception(
                "Wakeup fire callback failed for session=%s wake=%s",
                session_id,
                wake.wake_id,
            )
