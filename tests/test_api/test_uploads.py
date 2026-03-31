"""Tests for the attachment upload endpoint and attachment content-block building."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from fastapi import FastAPI

from src.core.attachment_store import AttachmentStore, ResolvedAttachment
from src.core.prompt_router import PromptRouter
from src.core.session import SessionManager

API_KEY = "test-api-key"

_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
    b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
    b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_llm_mock(*, supports_vision: bool) -> MagicMock:
    """Return a mock LLMClient with the given vision capability."""
    m = MagicMock()
    m.supports_vision = supports_vision
    m.attachment_capabilities = {"images": supports_vision, "text_files": True}
    return m


@pytest.fixture
def client_with_store(test_app: FastAPI) -> TestClient:
    """TestClient with a live AttachmentStore on app.state."""
    test_app.state.attachment_store = AttachmentStore()
    return TestClient(test_app)


@pytest.fixture
def client_vision(test_app: FastAPI) -> TestClient:
    """TestClient with AttachmentStore + vision-capable LLM mock."""
    test_app.state.attachment_store = AttachmentStore()
    test_app.state.llm_client = _make_llm_mock(supports_vision=True)
    return TestClient(test_app)


@pytest.fixture
def client_no_vision(test_app: FastAPI) -> TestClient:
    """TestClient with AttachmentStore + non-vision LLM mock."""
    test_app.state.attachment_store = AttachmentStore()
    test_app.state.llm_client = _make_llm_mock(supports_vision=False)
    return TestClient(test_app)


def _auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


# ---------------------------------------------------------------------------
# Upload endpoint tests
# ---------------------------------------------------------------------------


class TestUploadEndpoint:
    def test_upload_image_returns_metadata(self, client_with_store: TestClient) -> None:
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
            b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
            b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
            b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N"
            b"\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        resp = client_with_store.post(
            "/api/uploads",
            headers=_auth(),
            files={"file": ("test.png", BytesIO(png_bytes), "image/png")},
        )
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert "attachment_id" in body
        assert body["file_name"] == "test.png"
        assert body["mime_type"] == "image/png"
        assert body["size"] == len(png_bytes)
        assert body["is_image"] is True

    def test_upload_text_file(self, client_with_store: TestClient) -> None:
        content = b"hello world"
        resp = client_with_store.post(
            "/api/uploads",
            headers=_auth(),
            files={"file": ("notes.txt", BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["is_image"] is False
        assert body["mime_type"] == "text/plain"

    def test_upload_requires_auth(self, client_with_store: TestClient) -> None:
        resp = client_with_store.post(
            "/api/uploads",
            files={"file": ("x.txt", BytesIO(b"x"), "text/plain")},
        )
        assert resp.status_code == 401

    def test_upload_empty_file_rejected(self, client_with_store: TestClient) -> None:
        resp = client_with_store.post(
            "/api/uploads",
            headers=_auth(),
            files={"file": ("empty.txt", BytesIO(b""), "text/plain")},
        )
        assert resp.status_code == 400

    def test_upload_oversized_file_rejected(self, client_with_store: TestClient) -> None:
        store = AttachmentStore(max_size_bytes=10)
        client_with_store.app.state.attachment_store = store
        resp = client_with_store.post(
            "/api/uploads",
            headers=_auth(),
            files={"file": ("big.bin", BytesIO(b"x" * 11), "application/octet-stream")},
        )
        assert resp.status_code == 413

    def test_stored_attachment_retrievable(self, client_with_store: TestClient) -> None:
        content = b"sample content"
        resp = client_with_store.post(
            "/api/uploads",
            headers=_auth(),
            files={"file": ("sample.txt", BytesIO(content), "text/plain")},
        )
        assert resp.status_code == 200
        att_id = resp.json()["attachment_id"]
        store: AttachmentStore = client_with_store.app.state.attachment_store
        stored = store.get(att_id)
        assert stored is not None
        assert stored.data == content
        assert stored.file_name == "sample.txt"


# ---------------------------------------------------------------------------
# Attachment capability gating
# ---------------------------------------------------------------------------


class TestUploadCapabilityGating:
    """Upload endpoint should enforce model-level attachment capabilities."""

    @pytest.mark.parametrize("mime", ["image/jpeg", "image/png", "image/gif", "image/webp"])
    def test_image_rejected_when_vision_not_supported(
        self, client_no_vision: TestClient, mime: str
    ) -> None:
        resp = client_no_vision.post(
            "/api/uploads",
            headers=_auth(),
            files={"file": ("photo.img", BytesIO(_PNG_BYTES), mime)},
        )
        assert resp.status_code == 415
        assert "image" in resp.json()["detail"].lower()

    @pytest.mark.parametrize("mime", ["image/jpeg", "image/png", "image/gif", "image/webp"])
    def test_image_accepted_when_vision_supported(
        self, client_vision: TestClient, mime: str
    ) -> None:
        resp = client_vision.post(
            "/api/uploads",
            headers=_auth(),
            files={"file": ("photo.img", BytesIO(_PNG_BYTES), mime)},
        )
        assert resp.status_code == 200
        assert resp.json()["is_image"] is True

    def test_text_file_always_accepted_regardless_of_vision(
        self, client_no_vision: TestClient
    ) -> None:
        resp = client_no_vision.post(
            "/api/uploads",
            headers=_auth(),
            files={"file": ("notes.txt", BytesIO(b"hello"), "text/plain")},
        )
        assert resp.status_code == 200

    def test_no_llm_client_skips_capability_check(
        self, client_with_store: TestClient
    ) -> None:
        """When llm_client is absent from app.state, capability check is skipped."""
        # client_with_store has no llm_client on app.state
        if hasattr(client_with_store.app.state, "llm_client"):
            del client_with_store.app.state.llm_client
        resp = client_with_store.post(
            "/api/uploads",
            headers=_auth(),
            files={"file": ("photo.png", BytesIO(_PNG_BYTES), "image/png")},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# AttachmentStore unit tests
# ---------------------------------------------------------------------------


class TestAttachmentStore:
    def test_store_and_get(self) -> None:
        store = AttachmentStore()
        att = store.store("a.png", "image/png", b"bytes")
        assert att.id
        retrieved = store.get(att.id)
        assert retrieved is not None
        assert retrieved.data == b"bytes"

    def test_pop_removes_entry(self) -> None:
        store = AttachmentStore()
        att = store.store("a.txt", "text/plain", b"hi")
        popped = store.pop(att.id)
        assert popped is not None
        assert store.get(att.id) is None

    def test_missing_id_returns_none(self) -> None:
        store = AttachmentStore()
        assert store.get("nonexistent") is None
        assert store.pop("nonexistent") is None

    def test_exceeds_max_size_raises(self) -> None:
        store = AttachmentStore(max_size_bytes=5)
        with pytest.raises(ValueError, match="exceeds limit"):
            store.store("f.bin", "application/octet-stream", b"toolong")

    def test_evict_expired(self) -> None:
        store = AttachmentStore(ttl_seconds=60)
        att = store.store("old.txt", "text/plain", b"data")
        # Manually expire it
        store._store[att.id].created_at = datetime.now(UTC) - timedelta(seconds=120)
        assert store.get(att.id) is None


# ---------------------------------------------------------------------------
# PromptRouter._build_attachment_blocks tests
# ---------------------------------------------------------------------------


def _make_router_with_provider(provider: str) -> PromptRouter:
    llm_mock = MagicMock()
    llm_mock.provider = provider
    tool_registry = MagicMock()
    session_manager = SessionManager("test")
    return PromptRouter(llm_mock, session_manager, tool_registry)


class TestBuildAttachmentBlocks:
    def test_image_anthropic_format(self) -> None:
        router = _make_router_with_provider("anthropic")
        png_1x1 = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )
        blocks = router._build_attachment_blocks(
            [ResolvedAttachment("photo.png", "image/png", png_1x1)]
        )
        assert len(blocks) == 1
        block = blocks[0]
        assert block["type"] == "image"
        assert block["source"]["type"] == "base64"
        assert block["source"]["media_type"] == "image/png"
        # Verify base64 content round-trips correctly
        decoded = base64.standard_b64decode(block["source"]["data"])
        assert decoded == png_1x1

    def test_image_openai_format(self) -> None:
        router = _make_router_with_provider("openai")
        data = b"fakepngbytes"
        blocks = router._build_attachment_blocks(
            [ResolvedAttachment("img.jpg", "image/jpeg", data)]
        )
        assert len(blocks) == 1
        block = blocks[0]
        assert block["type"] == "image_url"
        assert block["image_url"]["url"].startswith("data:image/jpeg;base64,")

    def test_text_file_inlined(self) -> None:
        router = _make_router_with_provider("anthropic")
        code = b"def hello():\n    return 'world'\n"
        blocks = router._build_attachment_blocks(
            [ResolvedAttachment("hello.py", "text/x-python", code)]
        )
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert "hello.py" in blocks[0]["text"]
        assert "def hello" in blocks[0]["text"]

    def test_markdown_file_inlined(self) -> None:
        router = _make_router_with_provider("anthropic")
        md = b"# Title\n\nSome text."
        blocks = router._build_attachment_blocks(
            [ResolvedAttachment("README.md", "text/markdown", md)]
        )
        assert blocks[0]["type"] == "text"
        assert "README.md" in blocks[0]["text"]
        assert "# Title" in blocks[0]["text"]

    def test_binary_file_placeholder(self) -> None:
        router = _make_router_with_provider("anthropic")
        blocks = router._build_attachment_blocks(
            [ResolvedAttachment("archive.zip", "application/zip", b"\x50\x4b\x03\x04")]
        )
        assert len(blocks) == 1
        assert blocks[0]["type"] == "text"
        assert "archive.zip" in blocks[0]["text"]
        assert "binary" in blocks[0]["text"]

    def test_multiple_attachments(self) -> None:
        router = _make_router_with_provider("anthropic")
        blocks = router._build_attachment_blocks(
            [
                ResolvedAttachment("note.txt", "text/plain", b"some text"),
                ResolvedAttachment("data.json", "application/json", b'{"key": "val"}'),
            ]
        )
        assert len(blocks) == 2
        assert all(b["type"] == "text" for b in blocks)

    def test_no_attachments_returns_empty(self) -> None:
        router = _make_router_with_provider("anthropic")
        assert router._build_attachment_blocks([]) == []

    def test_text_extension_detection(self) -> None:
        router = _make_router_with_provider("anthropic")
        # .dart has text extension, even with generic mime
        blocks = router._build_attachment_blocks(
            [ResolvedAttachment("main.dart", "application/octet-stream", b"void main() {}")]
        )
        assert blocks[0]["type"] == "text"
        assert "main.dart" in blocks[0]["text"]
