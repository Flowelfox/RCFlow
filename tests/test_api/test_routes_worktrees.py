"""Tests for src/api/routes/worktrees.py.

Covers:
- ``GET /api/worktrees`` — list success, non-git path → 400, non-existent path → 404
- ``POST /api/worktrees`` — create 201 success, WorktreeExists → 409,
  InvalidBranchType → 422, NotInGitRepository → 400, non-existent path → 404
- ``POST /api/worktrees/{name}/merge`` — success, WorktreeNotFound → 404,
  MergeError → 500, UncommittedChanges → 409, GitOperationError → 500
- ``DELETE /api/worktrees/{name}`` — success, WorktreeNotFound → 404
- ``_manager`` — non-existent path raises HTTP 404 before touching git
- ``_map_exception`` — all exception types produce the correct HTTP codes
- Auth enforcement on all endpoints
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException as FastHTTPException
from fastapi.testclient import TestClient
from wtpython import (
    GitOperationError,
    InvalidBranchType,
    MergeError,
    NotInGitRepository,
    UncommittedChanges,
    WorktreeExists,
    WorktreeNotFound,
)

from src.api.routes.worktrees import _manager, _map_exception

if TYPE_CHECKING:
    from fastapi import FastAPI

API_KEY = "test-api-key"


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


def _auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def _mock_worktree(name: str = "feature/ABC-1/my-feature") -> MagicMock:
    wt = MagicMock()
    wt.name = name
    wt.branch = name
    wt.base = "main"
    wt.path = f"/repo/.wt/{name}"
    wt.meta = MagicMock()
    wt.meta.created.isoformat.return_value = "2026-01-01T00:00:00"
    return wt


def _mock_manager(worktrees=None, *, side_effect=None) -> MagicMock:
    """Return a MagicMock WorktreeManager."""
    mgr = MagicMock()
    if side_effect is not None:
        mgr.list.side_effect = side_effect
        mgr.new.side_effect = side_effect
        mgr.merge.side_effect = side_effect
        mgr.rm.side_effect = side_effect
    elif worktrees is not None:
        mgr.list.return_value = worktrees
    return mgr


# ---------------------------------------------------------------------------
# GET /api/worktrees
# ---------------------------------------------------------------------------


class TestListWorktrees:
    def test_returns_200_with_worktrees(self, client: TestClient) -> None:
        wt = _mock_worktree()
        mgr = _mock_manager(worktrees=[wt])
        with patch("src.api.routes.worktrees._manager", return_value=mgr):
            resp = client.get("/api/worktrees?repo_path=/repo", headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert "worktrees" in body
        assert len(body["worktrees"]) == 1
        assert body["worktrees"][0]["name"] == wt.name

    def test_returns_empty_list_when_no_worktrees(self, client: TestClient) -> None:
        mgr = _mock_manager(worktrees=[])
        with patch("src.api.routes.worktrees._manager", return_value=mgr):
            resp = client.get("/api/worktrees?repo_path=/repo", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["worktrees"] == []

    def test_non_git_repo_returns_400(self, client: TestClient) -> None:
        with patch(
            "src.api.routes.worktrees._manager",
            side_effect=FastHTTPException(status_code=400, detail="Not a git repository"),
        ):
            resp = client.get("/api/worktrees?repo_path=/not/a/repo", headers=_auth())
        assert resp.status_code == 400

    def test_nonexistent_path_returns_404(self, client: TestClient) -> None:
        with patch(
            "src.api.routes.worktrees._manager",
            side_effect=FastHTTPException(status_code=404, detail="Path not found"),
        ):
            resp = client.get(
                "/api/worktrees?repo_path=/Users/vpohribnichenko/Projects/espa",
                headers=_auth(),
            )
        assert resp.status_code == 404

    def test_missing_repo_path_returns_422(self, client: TestClient) -> None:
        resp = client.get("/api/worktrees", headers=_auth())
        assert resp.status_code == 422

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/worktrees?repo_path=/repo")
        assert resp.status_code in (401, 403, 422)

    def test_worktree_fields_present(self, client: TestClient) -> None:
        wt = _mock_worktree("feature/T-1/my-feature")
        mgr = _mock_manager(worktrees=[wt])
        with patch("src.api.routes.worktrees._manager", return_value=mgr):
            resp = client.get("/api/worktrees?repo_path=/repo", headers=_auth())
        data = resp.json()["worktrees"][0]
        assert "name" in data
        assert "branch" in data
        assert "base" in data
        assert "path" in data


# ---------------------------------------------------------------------------
# POST /api/worktrees
# ---------------------------------------------------------------------------


class TestCreateWorktree:
    def test_creates_worktree_returns_201(self, client: TestClient) -> None:
        wt = _mock_worktree("feature/ABC-1/add-auth")
        mgr = MagicMock()
        mgr.new.return_value = wt
        with patch("src.api.routes.worktrees._manager", return_value=mgr):
            resp = client.post(
                "/api/worktrees",
                json={"branch": "feature/ABC-1/add-auth", "repo_path": "/repo"},
                headers=_auth(),
            )
        assert resp.status_code == 201
        assert resp.json()["worktree"]["name"] == wt.name

    def test_worktree_exists_returns_409(self, client: TestClient) -> None:
        mgr = MagicMock()
        mgr.new.side_effect = WorktreeExists("already exists")
        with patch("src.api.routes.worktrees._manager", return_value=mgr):
            resp = client.post(
                "/api/worktrees",
                json={"branch": "feature/ABC-1/add-auth", "repo_path": "/repo"},
                headers=_auth(),
            )
        assert resp.status_code == 409

    def test_invalid_branch_type_returns_422(self, client: TestClient) -> None:
        mgr = MagicMock()
        mgr.new.side_effect = InvalidBranchType("bad branch")
        with patch("src.api.routes.worktrees._manager", return_value=mgr):
            resp = client.post(
                "/api/worktrees",
                json={"branch": "bad-branch", "repo_path": "/repo"},
                headers=_auth(),
            )
        assert resp.status_code == 422

    def test_not_git_repo_returns_400(self, client: TestClient) -> None:
        with patch(
            "src.api.routes.worktrees._manager",
            side_effect=FastHTTPException(status_code=400, detail="Not a git repository"),
        ):
            resp = client.post(
                "/api/worktrees",
                json={"branch": "feature/X-1/test", "repo_path": "/not/git"},
                headers=_auth(),
            )
        assert resp.status_code == 400

    def test_nonexistent_path_returns_404(self, client: TestClient) -> None:
        with patch(
            "src.api.routes.worktrees._manager",
            side_effect=FastHTTPException(status_code=404, detail="Path not found"),
        ):
            resp = client.post(
                "/api/worktrees",
                json={
                    "branch": "feature/X-1/test",
                    "repo_path": "/Users/vpohribnichenko/Projects/espa",
                },
                headers=_auth(),
            )
        assert resp.status_code == 404

    def test_missing_body_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/worktrees", json={}, headers=_auth())
        assert resp.status_code == 422

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.post(
            "/api/worktrees",
            json={"branch": "feature/X-1/test", "repo_path": "/repo"},
        )
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# POST /api/worktrees/{name}/merge
# ---------------------------------------------------------------------------


class TestMergeWorktree:
    def test_merge_success_returns_200(self, client: TestClient) -> None:
        mgr = MagicMock()
        mgr.merge.return_value = None
        with patch("src.api.routes.worktrees._manager", return_value=mgr):
            resp = client.post(
                "/api/worktrees/my-feature/merge",
                json={"message": "squash merge", "repo_path": "/repo"},
                headers=_auth(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["merged"] is True
        assert body["name"] == "my-feature"

    def test_worktree_not_found_returns_404(self, client: TestClient) -> None:
        mgr = MagicMock()
        mgr.merge.side_effect = WorktreeNotFound("not found")
        with patch("src.api.routes.worktrees._manager", return_value=mgr):
            resp = client.post(
                "/api/worktrees/nonexistent/merge",
                json={"message": "merge", "repo_path": "/repo"},
                headers=_auth(),
            )
        assert resp.status_code == 404

    def test_merge_error_returns_500(self, client: TestClient) -> None:
        mgr = MagicMock()
        mgr.merge.side_effect = MergeError("conflict")
        with patch("src.api.routes.worktrees._manager", return_value=mgr):
            resp = client.post(
                "/api/worktrees/my-feature/merge",
                json={"message": "merge", "repo_path": "/repo"},
                headers=_auth(),
            )
        assert resp.status_code == 500

    def test_uncommitted_changes_returns_409(self, client: TestClient) -> None:
        mgr = MagicMock()
        mgr.merge.side_effect = UncommittedChanges("dirty")
        with patch("src.api.routes.worktrees._manager", return_value=mgr):
            resp = client.post(
                "/api/worktrees/my-feature/merge",
                json={"message": "merge", "repo_path": "/repo"},
                headers=_auth(),
            )
        assert resp.status_code == 409

    def test_git_operation_error_returns_500(self, client: TestClient) -> None:
        mgr = MagicMock()
        mgr.merge.side_effect = GitOperationError("git failed")
        with patch("src.api.routes.worktrees._manager", return_value=mgr):
            resp = client.post(
                "/api/worktrees/my-feature/merge",
                json={"message": "merge", "repo_path": "/repo"},
                headers=_auth(),
            )
        assert resp.status_code == 500

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.post(
            "/api/worktrees/foo/merge",
            json={"message": "merge", "repo_path": "/repo"},
        )
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# DELETE /api/worktrees/{name}
# ---------------------------------------------------------------------------


class TestRemoveWorktree:
    def test_remove_success_returns_200(self, client: TestClient) -> None:
        mgr = MagicMock()
        mgr.rm.return_value = None
        with patch("src.api.routes.worktrees._manager", return_value=mgr):
            resp = client.delete(
                "/api/worktrees/my-old-feature?repo_path=/repo",
                headers=_auth(),
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["removed"] is True
        assert body["name"] == "my-old-feature"

    def test_worktree_not_found_returns_404(self, client: TestClient) -> None:
        mgr = MagicMock()
        mgr.rm.side_effect = WorktreeNotFound("not found")
        with patch("src.api.routes.worktrees._manager", return_value=mgr):
            resp = client.delete(
                "/api/worktrees/nonexistent?repo_path=/repo",
                headers=_auth(),
            )
        assert resp.status_code == 404

    def test_missing_repo_path_returns_422(self, client: TestClient) -> None:
        resp = client.delete("/api/worktrees/foo", headers=_auth())
        assert resp.status_code == 422

    def test_git_operation_error_returns_500(self, client: TestClient) -> None:
        mgr = MagicMock()
        mgr.rm.side_effect = GitOperationError("failed")
        with patch("src.api.routes.worktrees._manager", return_value=mgr):
            resp = client.delete(
                "/api/worktrees/foo?repo_path=/repo",
                headers=_auth(),
            )
        assert resp.status_code == 500

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.delete("/api/worktrees/foo?repo_path=/repo")
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# _manager — direct unit tests
# ---------------------------------------------------------------------------


class TestManager:
    def test_nonexistent_path_raises_404(self) -> None:
        """A path that doesn't exist on disk must return 404 before hitting git."""
        with pytest.raises(FastHTTPException) as exc_info:
            _manager("/Users/vpohribnichenko/Projects/espa")
        assert exc_info.value.status_code == 404

    def test_existing_non_git_dir_raises_400(self, tmp_path) -> None:
        """An existing directory that is not a git repo must return 400."""
        with pytest.raises(FastHTTPException) as exc_info:
            _manager(str(tmp_path))
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# _map_exception — direct unit tests
# ---------------------------------------------------------------------------


class TestMapException:
    def _map(self, exc: Exception) -> int:
        return _map_exception(exc).status_code

    def test_worktree_not_found_is_404(self) -> None:
        assert self._map(WorktreeNotFound("x")) == 404

    def test_worktree_exists_is_409(self) -> None:
        assert self._map(WorktreeExists("x")) == 409

    def test_invalid_branch_type_is_422(self) -> None:
        assert self._map(InvalidBranchType("x")) == 422

    def test_uncommitted_changes_is_409(self) -> None:
        assert self._map(UncommittedChanges("x")) == 409

    def test_merge_error_is_500(self) -> None:
        assert self._map(MergeError("x")) == 500

    def test_git_operation_error_is_500(self) -> None:
        assert self._map(GitOperationError("x")) == 500

    def test_not_in_git_repository_is_400(self) -> None:
        assert self._map(NotInGitRepository("x")) == 400

    def test_unknown_exception_is_500(self) -> None:
        assert self._map(RuntimeError("oops")) == 500
