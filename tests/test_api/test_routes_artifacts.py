"""Tests for src/api/routes/artifacts.py.

Covers:
- ``GET /api/artifacts/settings`` — reads from app.state.settings (no DB needed)
- ``PATCH /api/artifacts/settings`` — updates settings file (mocked)
- ``GET /api/artifacts`` — no-DB fast path returns empty list
- ``GET /api/artifacts/search`` — no-DB fast path returns empty list
- ``GET /api/artifacts/{id}`` — no-DB returns 404 for valid UUID; invalid UUID
  returns 400 when DB is present (checked after DB guard)
- ``GET /api/artifacts/{id}/content`` — no-DB returns 404
- ``DELETE /api/artifacts/{id}`` — no-DB returns 500; invalid UUID returns 400
  when DB is present
- Auth enforcement on all endpoints
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


API_KEY = "test-api-key"
_VALID_UUID = "00000000-0000-0000-0000-000000000001"
_INVALID_UUID = "not-a-uuid"


@asynccontextmanager
async def _empty_db():
    """Mock DB session that finds no artifact."""
    db = AsyncMock()
    db.get.return_value = None
    yield db


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


def _auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


# ---------------------------------------------------------------------------
# GET /api/artifacts/settings
# ---------------------------------------------------------------------------


class TestGetArtifactSettings:
    def test_returns_200_with_required_fields(self, client: TestClient) -> None:
        resp = client.get("/api/artifacts/settings", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert "include_pattern" in body
        assert "exclude_pattern" in body
        assert "auto_scan" in body
        assert "max_file_size" in body

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/artifacts/settings")
        assert resp.status_code in (401, 403, 422)

    def test_auto_scan_is_bool(self, client: TestClient) -> None:
        resp = client.get("/api/artifacts/settings", headers=_auth())
        assert isinstance(resp.json()["auto_scan"], bool)

    def test_max_file_size_is_int(self, client: TestClient) -> None:
        resp = client.get("/api/artifacts/settings", headers=_auth())
        assert isinstance(resp.json()["max_file_size"], int)


# ---------------------------------------------------------------------------
# PATCH /api/artifacts/settings
# ---------------------------------------------------------------------------


class TestUpdateArtifactSettings:
    def test_empty_body_returns_200(self, client: TestClient) -> None:
        with patch("src.api.routes.artifacts.update_settings_file"):
            resp = client.patch("/api/artifacts/settings", json={}, headers=_auth())
        assert resp.status_code == 200

    def test_update_auto_scan_returns_settings(self, client: TestClient) -> None:
        with patch("src.api.routes.artifacts.update_settings_file") as mock_update:
            with patch("src.api.routes.artifacts.Settings") as mock_settings_cls:
                mock_settings_cls.return_value = client.app.state.settings
                resp = client.patch(
                    "/api/artifacts/settings",
                    json={"auto_scan": False},
                    headers=_auth(),
                )
        assert resp.status_code == 200
        # update_settings_file should have been called with ARTIFACT_AUTO_SCAN
        mock_update.assert_called_once()
        call_args = mock_update.call_args[0][0]
        assert "ARTIFACT_AUTO_SCAN" in call_args

    def test_update_max_file_size(self, client: TestClient) -> None:
        with patch("src.api.routes.artifacts.update_settings_file") as mock_update:
            with patch("src.api.routes.artifacts.Settings") as mock_settings_cls:
                mock_settings_cls.return_value = client.app.state.settings
                resp = client.patch(
                    "/api/artifacts/settings",
                    json={"max_file_size": 2048},
                    headers=_auth(),
                )
        assert resp.status_code == 200
        call_args = mock_update.call_args[0][0]
        assert "ARTIFACT_MAX_FILE_SIZE" in call_args
        assert call_args["ARTIFACT_MAX_FILE_SIZE"] == "2048"

    def test_update_include_pattern(self, client: TestClient) -> None:
        with patch("src.api.routes.artifacts.update_settings_file") as mock_update:
            with patch("src.api.routes.artifacts.Settings") as mock_settings_cls:
                mock_settings_cls.return_value = client.app.state.settings
                resp = client.patch(
                    "/api/artifacts/settings",
                    json={"include_pattern": "**/*.py"},
                    headers=_auth(),
                )
        assert resp.status_code == 200
        call_args = mock_update.call_args[0][0]
        assert call_args["ARTIFACT_INCLUDE_PATTERN"] == "**/*.py"

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.patch("/api/artifacts/settings", json={})
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# GET /api/artifacts  (no-DB fast path)
# ---------------------------------------------------------------------------


class TestListArtifacts:
    def test_returns_empty_list_when_no_db(self, client: TestClient) -> None:
        resp = client.get("/api/artifacts", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"artifacts": []}

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/artifacts")
        assert resp.status_code in (401, 403, 422)

    def test_accepts_search_param(self, client: TestClient) -> None:
        resp = client.get("/api/artifacts?search=foo", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"artifacts": []}

    def test_accepts_limit_and_offset(self, client: TestClient) -> None:
        resp = client.get("/api/artifacts?limit=10&offset=5", headers=_auth())
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/artifacts/search  (no-DB fast path)
# ---------------------------------------------------------------------------


class TestSearchArtifacts:
    def test_returns_empty_list_when_no_db(self, client: TestClient) -> None:
        resp = client.get("/api/artifacts/search", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"artifacts": []}

    def test_accepts_q_param(self, client: TestClient) -> None:
        resp = client.get("/api/artifacts/search?q=main", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"artifacts": []}

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/artifacts/search")
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# GET /api/artifacts/{artifact_id}  (no-DB)
# ---------------------------------------------------------------------------


class TestGetArtifact:
    def test_no_db_returns_404_for_valid_uuid(self, client: TestClient) -> None:
        resp = client.get(f"/api/artifacts/{_VALID_UUID}", headers=_auth())
        assert resp.status_code == 404

    def test_invalid_uuid_returns_400_when_db_present(self, client: TestClient, test_app: FastAPI) -> None:
        test_app.state.db_session_factory = _empty_db
        try:
            resp = client.get(f"/api/artifacts/{_INVALID_UUID}", headers=_auth())
            assert resp.status_code == 400
            assert "Invalid artifact ID" in resp.json()["detail"]
        finally:
            test_app.state.db_session_factory = None

    def test_artifact_not_found_when_db_present(self, client: TestClient, test_app: FastAPI) -> None:
        test_app.state.db_session_factory = _empty_db
        try:
            resp = client.get(f"/api/artifacts/{_VALID_UUID}", headers=_auth())
            assert resp.status_code == 404
        finally:
            test_app.state.db_session_factory = None

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get(f"/api/artifacts/{_VALID_UUID}")
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# GET /api/artifacts/{artifact_id}/content  (no-DB)
# ---------------------------------------------------------------------------


class TestGetArtifactContent:
    def test_no_db_returns_404(self, client: TestClient) -> None:
        resp = client.get(f"/api/artifacts/{_VALID_UUID}/content", headers=_auth())
        assert resp.status_code == 404

    def test_invalid_uuid_returns_400_when_db_present(self, client: TestClient, test_app: FastAPI) -> None:
        test_app.state.db_session_factory = _empty_db
        try:
            resp = client.get(f"/api/artifacts/{_INVALID_UUID}/content", headers=_auth())
            assert resp.status_code == 400
            assert "Invalid artifact ID" in resp.json()["detail"]
        finally:
            test_app.state.db_session_factory = None

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get(f"/api/artifacts/{_VALID_UUID}/content")
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# DELETE /api/artifacts/{artifact_id}  (no-DB)
# ---------------------------------------------------------------------------


class TestDeleteArtifact:
    def test_no_db_returns_500(self, client: TestClient) -> None:
        resp = client.delete(f"/api/artifacts/{_VALID_UUID}", headers=_auth())
        assert resp.status_code == 500

    def test_invalid_uuid_returns_400_when_db_present(self, client: TestClient, test_app: FastAPI) -> None:
        test_app.state.db_session_factory = _empty_db
        try:
            resp = client.delete(f"/api/artifacts/{_INVALID_UUID}", headers=_auth())
            assert resp.status_code == 400
            assert "Invalid artifact ID" in resp.json()["detail"]
        finally:
            test_app.state.db_session_factory = None

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.delete(f"/api/artifacts/{_VALID_UUID}")
        assert resp.status_code in (401, 403, 422)
