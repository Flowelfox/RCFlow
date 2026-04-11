"""In-memory store for user-uploaded chat attachments.

Attachments are stored by UUID, keyed between the HTTP upload and WebSocket
prompt submission. Each entry auto-expires after a configurable TTL (default
10 minutes) so memory is reclaimed without requiring explicit cleanup.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class AttachmentData:
    """Metadata and raw bytes for a single uploaded attachment."""

    id: str
    file_name: str
    mime_type: str
    data: bytes
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class ResolvedAttachment:
    """Attachment data passed from the WebSocket handler to the prompt router."""

    file_name: str
    mime_type: str
    data: bytes


_DEFAULT_MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB
_DEFAULT_TTL_SECONDS = 600  # 10 minutes


class AttachmentStore:
    """Temporary in-memory store for user-uploaded attachments.

    Files are stored by UUID and consumed when a prompt is handled, or
    automatically evicted after *ttl_seconds*.
    """

    def __init__(
        self,
        max_size_bytes: int = _DEFAULT_MAX_FILE_BYTES,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    ) -> None:
        self._store: dict[str, AttachmentData] = {}
        self._max_size = max_size_bytes
        self._ttl = ttl_seconds

    def store(self, file_name: str, mime_type: str, data: bytes) -> AttachmentData:
        """Store an attachment and return its metadata.

        Raises:
            ValueError: If *data* exceeds the configured size limit.
        """
        if len(data) > self._max_size:
            raise ValueError(f"File size {len(data):,} bytes exceeds limit of {self._max_size:,} bytes")
        attachment_id = str(uuid.uuid4())
        attachment = AttachmentData(
            id=attachment_id,
            file_name=file_name,
            mime_type=mime_type,
            data=data,
        )
        self._store[attachment_id] = attachment
        self._evict_expired()
        return attachment

    def get(self, attachment_id: str) -> AttachmentData | None:
        """Retrieve an attachment without removing it. Returns None if missing or expired."""
        att = self._store.get(attachment_id)
        if att is None:
            return None
        if self._is_expired(att):
            del self._store[attachment_id]
            return None
        return att

    def pop(self, attachment_id: str) -> AttachmentData | None:
        """Retrieve and remove an attachment. Returns None if missing or expired."""
        att = self._store.pop(attachment_id, None)
        if att is None:
            return None
        if self._is_expired(att):
            return None
        return att

    def _is_expired(self, att: AttachmentData) -> bool:
        return (datetime.now(UTC) - att.created_at).total_seconds() > self._ttl

    def _evict_expired(self) -> None:
        expired = [k for k, v in self._store.items() if self._is_expired(v)]
        for k in expired:
            del self._store[k]
