"""Attachment upload endpoint for chat prompts.

Clients upload files here via HTTP multipart POST and receive an
``attachment_id`` that can be included in a subsequent WebSocket ``prompt``
message via the ``attachments`` field.  Uploaded files are stored in
:class:`~src.core.attachment_store.AttachmentStore` and expire automatically
after 10 minutes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile

from src.api.deps import verify_http_api_key
from src.core.attachment_store import AttachmentStore

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Uploads"])

# MIME types treated as image content blocks by the LLM
_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


@router.post(
    "/uploads",
    summary="Upload a file attachment for use in a prompt",
    description=(
        "Accepts a single multipart file upload and stores it temporarily. "
        "Returns an ``attachment_id`` that can be passed in the ``attachments`` "
        "field of a WebSocket ``prompt`` message. "
        "Accepted: images (JPEG, PNG, GIF, WEBP), text files, PDFs, and other binary files. "
        "Maximum size: 20 MB. Attachments expire after 10 minutes."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def upload_attachment(
    file: UploadFile,
    request: Request,
) -> dict[str, Any]:
    """Upload a single file and return a short-lived attachment ID.

    The returned ``attachment_id`` should be included in the ``attachments``
    list of the next WebSocket ``prompt`` message within 10 minutes.
    """
    attachment_store: AttachmentStore | None = getattr(
        request.app.state, "attachment_store", None
    )
    if attachment_store is None:
        raise HTTPException(status_code=503, detail="Attachment store not available")

    data = await file.read()

    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {len(data):,} bytes (max {_MAX_UPLOAD_BYTES:,})",
        )

    file_name = file.filename or "attachment"
    mime_type = file.content_type or "application/octet-stream"

    try:
        attachment = attachment_store.store(file_name, mime_type, data)
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    logger.info(
        "Stored attachment %s (%s, %d bytes) as %s",
        file_name,
        mime_type,
        len(data),
        attachment.id,
    )

    return {
        "attachment_id": attachment.id,
        "file_name": file_name,
        "mime_type": mime_type,
        "size": len(data),
        "is_image": mime_type in _IMAGE_MIME_TYPES,
    }
