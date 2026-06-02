"""Tests for src/api/routes/projects.py.

Covers:
- ``GET /api/projects/{project_name}/artifacts`` returns 404 for unknown projects
- Auth enforcement
- No-DB fast path returns empty list for a resolvable project
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from fastapi import FastAPI

API_KEY = "test-api-key"


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


def _auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


class TestListProjectArtifacts:
    def test_unknown_project_returns_404(self, client: TestClient) -> None:
        resp = client.get(
            "/api/projects/definitely-not-a-real-project-xyz/artifacts",
            headers=_auth(),
        )
        assert resp.status_code == 404

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/projects/anything/artifacts")
        assert resp.status_code in (401, 403, 422)

    def test_known_project_no_db_returns_empty_artifacts(
        self,
        client: TestClient,
        tmp_path,
    ) -> None:
        # Point the configured projects_dirs at a tmp dir containing one
        # subfolder so the route resolves the project name to an existing path.
        (tmp_path / "demo").mkdir()
        app = client.app
        app.state.settings.PROJECTS_DIR = str(tmp_path)
        app.state.db_session_factory = None

        resp = client.get("/api/projects/demo/artifacts", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["project_name"] == "demo"
        assert body["artifacts"] == []
