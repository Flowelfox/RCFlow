"""Persistent store for Claude Code ``ScheduleWakeup`` calls.

Owns the ``session_scheduled_wakes`` DB table and the in-memory
mirror on :class:`~src.core.session.ActiveSession.scheduled_wakes`.

Mutation order for every public method is:

1. Write (or update) the DB row in a single transaction.
2. Update the ``ActiveSession.scheduled_wakes`` mirror.
3. Push the corresponding event (``WAKEUP_SCHEDULED`` /
   ``WAKEUP_FIRED`` / ``WAKEUP_CANCELLED``) to the session buffer
   so subscribed clients update their badge + inline card.

The DB is the source of truth; the mirror exists to avoid a query
each time a session_update broadcast goes out.  Survival across
backend restart is handled by :meth:`restore_all_pending`.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update

from src.core.buffer import MessageType
from src.core.session import ScheduledWake
from src.database.models import SessionScheduledWake as SessionScheduledWakeModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.core.session import ActiveSession, SessionManager

logger = logging.getLogger(__name__)


class SessionScheduledWakeStore:
    """CRUD + mirror updates for ``ScheduleWakeup`` wakes."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ------------------------------------------------------------------
    # Public API

    async def enqueue(
        self,
        session: ActiveSession,
        *,
        prompt: str,
        reason: str,
        fire_at: datetime,
    ) -> ScheduledWake:
        """Persist a new wake and add it to the session's mirror.

        Returns the freshly-created :class:`ScheduledWake` (with a
        backend-generated ``wake_id``).  Emits ``WAKEUP_SCHEDULED`` so
        the client renders the badge + inline card immediately.
        """
        now = datetime.now(UTC)
        wake_id = str(uuid.uuid4())
        row = SessionScheduledWakeModel(
            id=uuid.uuid4(),
            session_id=uuid.UUID(session.id),
            wake_id=wake_id,
            prompt=prompt,
            reason=reason,
            fire_at=fire_at,
            created_at=now,
        )
        async with self._session_factory() as db:
            db.add(row)
            await db.commit()

        entry = ScheduledWake(
            wake_id=wake_id,
            prompt=prompt,
            reason=reason,
            fire_at=fire_at,
            created_at=now,
        )
        session.mirror_add_wake(entry)
        session.buffer.push_text(
            MessageType.WAKEUP_SCHEDULED,
            {
                "session_id": session.id,
                "wake_id": wake_id,
                "prompt": prompt,
                "reason": reason,
                "fire_at": fire_at.isoformat(),
                "created_at": now.isoformat(),
            },
        )
        return entry

    async def mark_fired(self, session: ActiveSession, wake_id: str) -> ScheduledWake | None:
        """Mark *wake_id* as fired in DB + drop from mirror, emit ``WAKEUP_FIRED``."""
        now = datetime.now(UTC)
        async with self._session_factory() as db:
            await db.execute(
                update(SessionScheduledWakeModel)
                .where(SessionScheduledWakeModel.wake_id == wake_id)
                .values(fired_at=now),
            )
            await db.commit()
        removed = session.mirror_remove_wake(wake_id)
        if removed is None:
            return None
        session.buffer.push_text(
            MessageType.WAKEUP_FIRED,
            {
                "session_id": session.id,
                "wake_id": wake_id,
                "prompt": removed.prompt,
                "reason": removed.reason,
                "fired_at": now.isoformat(),
            },
        )
        return removed

    async def cancel(
        self,
        session: ActiveSession,
        wake_id: str,
        *,
        reason: str = "user_cancelled",
    ) -> ScheduledWake | None:
        """Mark *wake_id* cancelled in DB + drop from mirror, emit ``WAKEUP_CANCELLED``."""
        now = datetime.now(UTC)
        async with self._session_factory() as db:
            await db.execute(
                update(SessionScheduledWakeModel)
                .where(SessionScheduledWakeModel.wake_id == wake_id)
                .values(cancelled_at=now),
            )
            await db.commit()
        removed = session.mirror_remove_wake(wake_id)
        if removed is None:
            return None
        session.buffer.push_text(
            MessageType.WAKEUP_CANCELLED,
            {
                "session_id": session.id,
                "wake_id": wake_id,
                "prompt": removed.prompt,
                "reason": removed.reason,
                "cancelled_at": now.isoformat(),
                "cancel_reason": reason,
            },
        )
        return removed

    async def cancel_all_for_session(
        self,
        session: ActiveSession,
        *,
        reason: str,
    ) -> list[ScheduledWake]:
        """Cancel every pending wake for *session* (e.g. on session end)."""
        cancelled: list[ScheduledWake] = []
        for entry in list(session.scheduled_wakes):
            removed = await self.cancel(session, entry.wake_id, reason=reason)
            if removed is not None:
                cancelled.append(removed)
        return cancelled

    async def load_for_session(self, session: ActiveSession) -> list[ScheduledWake]:
        """Hydrate *session*'s wake mirror from the DB.

        Used at backend startup after sessions are restored from the
        archive.  Returns the list of restored wakes (in fire-order)
        so the scheduler can re-arm them.
        """
        async with self._session_factory() as db:
            stmt = (
                select(SessionScheduledWakeModel)
                .where(SessionScheduledWakeModel.session_id == uuid.UUID(session.id))
                .where(SessionScheduledWakeModel.fired_at.is_(None))
                .where(SessionScheduledWakeModel.cancelled_at.is_(None))
                .order_by(SessionScheduledWakeModel.fire_at.asc())
            )
            rows = list((await db.execute(stmt)).scalars())
        restored: list[ScheduledWake] = []
        for row in rows:
            entry = ScheduledWake(
                wake_id=row.wake_id,
                prompt=row.prompt,
                reason=row.reason,
                fire_at=row.fire_at,
                created_at=row.created_at,
            )
            session.mirror_add_wake(entry)
            restored.append(entry)
        return restored

    async def restore_all_pending(
        self,
        session_manager: SessionManager,
    ) -> list[tuple[str, ScheduledWake]]:
        """Hydrate every active session with its pending wakes.

        Returns a flat ``(session_id, wake)`` list so the caller (the
        startup hook in :mod:`src.main`) can hand each one to the
        :class:`WakeupScheduler`.  Sessions whose ID isn't currently
        active in memory are skipped — their wakes still survive in
        the DB and will be re-hydrated when the session is restored.
        """
        out: list[tuple[str, ScheduledWake]] = []
        for session in session_manager.list_all_sessions():
            wakes = await self.load_for_session(session)
            for w in wakes:
                out.append((session.id, w))
        return out

    async def get(self, wake_id: str) -> dict[str, Any] | None:
        """Return the raw DB row dict for *wake_id*, or None if missing.

        Lightweight inspection helper used by tests and the cancel HTTP
        route.  Not on a hot path.
        """
        async with self._session_factory() as db:
            row = (
                await db.execute(
                    select(SessionScheduledWakeModel).where(
                        SessionScheduledWakeModel.wake_id == wake_id,
                    ),
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return {
            "wake_id": row.wake_id,
            "session_id": str(row.session_id),
            "prompt": row.prompt,
            "reason": row.reason,
            "fire_at": row.fire_at,
            "fired_at": row.fired_at,
            "cancelled_at": row.cancelled_at,
            "created_at": row.created_at,
        }
