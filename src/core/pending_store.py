"""Persistent queue for user messages sent while the agent is busy.

Owns the ``session_pending_messages`` DB table, the disk-spilled attachment
bytes under ``data/pending_attachments/<session_id>/<queued_id>/``, and the
in-memory mirror on :class:`~src.core.session.ActiveSession`.

Mutation order for every public method is:

1. Write (or delete) the DB row in a single transaction.
2. Touch the on-disk attachment directory.
3. Update the ``ActiveSession.pending_user_messages`` mirror.
4. Push the corresponding ephemeral event (``message_queued`` /
   ``message_dequeued`` / ``message_queued_updated``) to the session
   buffer so subscribed clients update their pinned queue.

The DB is the source of truth — the mirror exists only to avoid round-trips
when building ``session_update.queued_messages`` snapshots.

See ``Queued User Messages`` in ``Design.md`` for the full lifecycle.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select, update

from src.core.buffer import MessageType
from src.core.session import PendingMessage
from src.database.models import SessionPendingMessage as SessionPendingMessageModel

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from src.core.attachment_store import ResolvedAttachment
    from src.core.session import ActiveSession

logger = logging.getLogger(__name__)


_FILENAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize_filename(name: str) -> str:
    """Return a filesystem-safe rendition of *name* (keeps ASCII, underscores others)."""
    safe = _FILENAME_SANITIZE_RE.sub("_", name)
    return safe or "file"


@dataclass
class _AttachmentManifestEntry:
    name: str
    mime_type: str
    size: int
    path: str  # absolute path to the spilled byte file


class SessionPendingMessageStore:
    """CRUD + attachment spill for queued user messages."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        attachments_root: Path,
    ) -> None:
        self._session_factory = session_factory
        self._root = attachments_root
        self._root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API

    async def enqueue(
        self,
        session: ActiveSession,
        *,
        content: str,
        display_content: str,
        attachments: list[ResolvedAttachment] | None,
        project_name: str | None,
        selected_worktree_path: str | None,
        task_id: str | None,
    ) -> PendingMessage:
        """Append a new queued message for *session* and broadcast ``message_queued``."""
        queued_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        attachments_path: str | None = None
        manifest: list[_AttachmentManifestEntry] = []
        if attachments:
            attachments_path = str(self._attachments_dir(session.id, queued_id))
            manifest = await asyncio.to_thread(self._spill_attachments, session.id, queued_id, attachments)

        async with self._session_factory() as db:
            position = await self._next_position(db, session.id)
            row = SessionPendingMessageModel(
                id=uuid.uuid4(),
                session_id=uuid.UUID(session.id),
                queued_id=queued_id,
                position=position,
                content=content,
                display_content=display_content,
                attachments_path=attachments_path,
                project_name=project_name,
                selected_worktree_path=selected_worktree_path,
                task_id=task_id,
                submitted_at=now,
                updated_at=now,
            )
            db.add(row)
            await db.commit()

        entry = PendingMessage(
            queued_id=queued_id,
            position=position,
            content=content,
            display_content=display_content,
            attachments_path=attachments_path,
            project_name=project_name,
            selected_worktree_path=selected_worktree_path,
            task_id=task_id,
            submitted_at=now,
            updated_at=now,
        )
        session.mirror_add_pending(entry)

        session.buffer.push_ephemeral(
            MessageType.MESSAGE_QUEUED,
            {
                "session_id": session.id,
                "queued_id": queued_id,
                "position": position,
                "content": content,
                "display_content": display_content,
                "attachments": [{"name": m.name, "mime_type": m.mime_type, "size": m.size} for m in manifest],
                "submitted_at": now.isoformat(),
            },
        )
        return entry

    async def edit(
        self,
        session: ActiveSession,
        *,
        queued_id: str,
        content: str,
        display_content: str,
    ) -> PendingMessage | None:
        """Update text fields of a queued message. Returns None if not found."""
        now = datetime.now(UTC)
        async with self._session_factory() as db:
            result = await db.execute(
                update(SessionPendingMessageModel)
                .where(SessionPendingMessageModel.queued_id == queued_id)
                .values(content=content, display_content=display_content, updated_at=now)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 0:  # ty:ignore[unresolved-attribute]
                return None
            await db.commit()

        session.mirror_update_pending(queued_id, content, display_content, now)
        session.buffer.push_ephemeral(
            MessageType.MESSAGE_QUEUED_UPDATED,
            {
                "session_id": session.id,
                "queued_id": queued_id,
                "content": content,
                "display_content": display_content,
                "updated_at": now.isoformat(),
            },
        )
        idx = session._find_pending_index(queued_id)
        if idx is None:
            return None
        return session.pending_user_messages[idx]

    async def cancel(self, session: ActiveSession, *, queued_id: str) -> PendingMessage | None:
        """Remove a queued message by id. Returns the popped entry or None."""
        removed = await self._delete_one(session, queued_id=queued_id, reason="cancelled")
        return removed

    async def pop_head(self, session: ActiveSession) -> PendingMessage | None:
        """Remove and return the oldest queued message; broadcast ``message_dequeued``."""
        if not session.pending_user_messages:
            return None
        head = session.pending_user_messages[0]
        return await self._delete_one(session, queued_id=head.queued_id, reason="delivered")

    async def clear_session(self, session: ActiveSession, *, reason: str) -> list[PendingMessage]:
        """Drop every queued message for *session*. Used on session end/cancel.

        Emits one ``message_dequeued`` event per entry with the given *reason*.
        """
        async with self._session_factory() as db:
            await db.execute(
                delete(SessionPendingMessageModel).where(SessionPendingMessageModel.session_id == uuid.UUID(session.id))
            )
            await db.commit()

        dropped = session.mirror_clear_pending()
        for entry in dropped:
            self._rm_attachments(session.id, entry.queued_id)
            session.buffer.push_ephemeral(
                MessageType.MESSAGE_DEQUEUED,
                {"session_id": session.id, "queued_id": entry.queued_id, "reason": reason},
            )
        return dropped

    async def load_for_session(self, session: ActiveSession) -> None:
        """Hydrate *session*'s in-memory mirror from the DB.

        Called when an active session is (re)attached to the in-memory manager
        — e.g. after a backend restart that promoted the session to
        ``INTERRUPTED`` and the client has re-subscribed.
        """
        async with self._session_factory() as db:
            result = await db.execute(
                select(SessionPendingMessageModel)
                .where(SessionPendingMessageModel.session_id == uuid.UUID(session.id))
                .order_by(SessionPendingMessageModel.position.asc())
            )
            rows = list(result.scalars())

        session.pending_user_messages.clear()
        for row in rows:
            session.pending_user_messages.append(
                PendingMessage(
                    queued_id=row.queued_id,
                    position=row.position,
                    content=row.content,
                    display_content=row.display_content,
                    attachments_path=row.attachments_path,
                    project_name=row.project_name,
                    selected_worktree_path=row.selected_worktree_path,
                    task_id=row.task_id,
                    submitted_at=row.submitted_at,
                    updated_at=row.updated_at,
                )
            )

    async def sweep_orphans(self) -> int:
        """Delete attachment directories whose matching DB row no longer exists.

        Returns the number of orphaned directories removed.  Safe to call at
        startup before any session has been restored.
        """
        if not self._root.exists():
            return 0

        # Collect every queued_id currently in the DB.
        async with self._session_factory() as db:
            result = await db.execute(select(SessionPendingMessageModel.queued_id))
            live_ids = set(result.scalars())

        removed = 0
        for session_dir in self._root.iterdir():
            if not session_dir.is_dir():
                continue
            for queued_dir in session_dir.iterdir():
                if not queued_dir.is_dir():
                    continue
                if queued_dir.name not in live_ids:
                    await asyncio.to_thread(shutil.rmtree, queued_dir, ignore_errors=True)
                    removed += 1
            # Best-effort: drop empty session dirs.
            with _suppress_oserror():
                session_dir.rmdir()
        if removed:
            logger.info("Swept %d orphaned pending-attachment directories", removed)
        return removed

    # ------------------------------------------------------------------
    # Internal helpers

    async def _delete_one(
        self,
        session: ActiveSession,
        *,
        queued_id: str,
        reason: str,
    ) -> PendingMessage | None:
        async with self._session_factory() as db:
            result = await db.execute(
                delete(SessionPendingMessageModel)
                .where(SessionPendingMessageModel.queued_id == queued_id)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 0:  # ty:ignore[unresolved-attribute]
                return None
            # Densely renumber remaining rows in one UPDATE pass.
            remaining = await db.execute(
                select(SessionPendingMessageModel)
                .where(SessionPendingMessageModel.session_id == uuid.UUID(session.id))
                .order_by(SessionPendingMessageModel.position.asc())
            )
            for new_pos, row in enumerate(remaining.scalars()):
                row.position = new_pos
            await db.commit()

        removed = session.mirror_remove_pending(queued_id)
        self._rm_attachments(session.id, queued_id)

        session.buffer.push_ephemeral(
            MessageType.MESSAGE_DEQUEUED,
            {"session_id": session.id, "queued_id": queued_id, "reason": reason},
        )
        return removed

    async def _next_position(self, db: AsyncSession, session_id: str) -> int:
        result = await db.execute(
            select(SessionPendingMessageModel.position)
            .where(SessionPendingMessageModel.session_id == uuid.UUID(session_id))
            .order_by(SessionPendingMessageModel.position.desc())
            .limit(1)
        )
        top = result.scalar_one_or_none()
        return 0 if top is None else top + 1

    def _attachments_dir(self, session_id: str, queued_id: str) -> Path:
        return self._root / session_id / queued_id

    def _spill_attachments(
        self,
        session_id: str,
        queued_id: str,
        attachments: list[ResolvedAttachment],
    ) -> list[_AttachmentManifestEntry]:
        """Write every attachment to disk and persist a manifest.  Blocking; runs in thread."""
        target = self._attachments_dir(session_id, queued_id)
        target.mkdir(parents=True, exist_ok=True)
        manifest: list[_AttachmentManifestEntry] = []
        for idx, att in enumerate(attachments):
            safe = _sanitize_filename(att.file_name)
            fname = f"{idx}_{safe}"
            fpath = target / fname
            fpath.write_bytes(att.data)
            manifest.append(
                _AttachmentManifestEntry(
                    name=att.file_name,
                    mime_type=att.mime_type,
                    size=len(att.data),
                    path=str(fpath),
                )
            )
        manifest_path = target / "meta.json"
        manifest_path.write_text(
            json.dumps([{"name": m.name, "mime_type": m.mime_type, "size": m.size, "path": m.path} for m in manifest])
        )
        return manifest

    def _rm_attachments(self, session_id: str, queued_id: str) -> None:
        target = self._attachments_dir(session_id, queued_id)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

    @staticmethod
    def load_manifest(attachments_path: str | None) -> list[dict[str, Any]]:
        """Return the manifest entries previously written by :meth:`_spill_attachments`."""
        if not attachments_path:
            return []
        manifest_file = Path(attachments_path) / "meta.json"
        if not manifest_file.exists():
            return []
        return json.loads(manifest_file.read_text())

    @classmethod
    def rehydrate_attachments(cls, entry: PendingMessage) -> list[ResolvedAttachment]:
        """Read attachment bytes back from disk so a drained message can be delivered.

        Must be called **before** :meth:`pop_head` / :meth:`cancel`, since those
        delete the on-disk directory.
        """
        # Local import avoids a circular dependency with ``attachment_store``.
        from src.core.attachment_store import ResolvedAttachment as ResolvedAttachmentCls  # noqa: PLC0415

        result: list[ResolvedAttachmentCls] = []
        for item in cls.load_manifest(entry.attachments_path):
            path = item.get("path")
            if not path:
                continue
            data = Path(path).read_bytes()
            result.append(
                ResolvedAttachmentCls(
                    file_name=item.get("name", "attachment"),
                    mime_type=item.get("mime_type", "application/octet-stream"),
                    data=data,
                )
            )
        return result


# ---------------------------------------------------------------------------
# Tiny utility — avoids importing ``contextlib`` twice for a single-line guard.


class _SuppressOSError:
    def __enter__(self) -> None:  # pragma: no cover - trivial
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


def _suppress_oserror() -> _SuppressOSError:
    return _SuppressOSError()
