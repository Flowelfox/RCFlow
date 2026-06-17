"""Endpoint tests for the GitHub integration router.

Exercises the FastAPI endpoints under /api/integrations/github/ against an
in-memory sqlite cache, with the GitHubService and git_ops layers faked so no
real network or filesystem work happens. Covers happy paths plus the 404 /
422 / 502 / 503 error branches.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.api.integrations import github as gh_mod
from src.database.models import Base
from src.database.models import GitHubPR as GitHubPRModel
from src.services.github_service import GitHubServiceError

if TYPE_CHECKING:
    from fastapi import FastAPI

API_KEY = "test-api-key"


def _auth_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeService:
    """Configurable stand-in for GitHubService.

    Each method either returns a canned value or raises a configured error.
    Records calls so tests can assert delegation.
    """

    def __init__(self, **overrides: Any) -> None:
        self._overrides = overrides
        self.calls: list[tuple[str, tuple, dict]] = []
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True

    def _result(self, name: str, default: Any) -> Any:
        val = self._overrides.get(name, default)
        if isinstance(val, Exception):
            raise val
        return val

    async def token_info(self) -> dict[str, Any]:
        return self._result("token_info", {"fine_grained": False, "login": "alice", "scopes": ["repo"]})

    async def list_pull_requests(self, role: str, repo: str | None = None, state: str = "open") -> list[dict]:
        self.calls.append(("list_pull_requests", (role, repo, state), {}))
        return self._result("list_pull_requests", [])

    async def get_pull_request(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        self.calls.append(("get_pull_request", (owner, repo, number), {}))
        return self._result("get_pull_request", {})

    async def list_pr_files(self, owner: str, repo: str, number: int) -> list[dict]:
        return self._result("list_pr_files", [{"filename": "a.py", "patch": "@@"}])

    async def get_pr_diff(self, owner: str, repo: str, number: int) -> str:
        return self._result("get_pr_diff", "diff --git a/a b/a")

    async def get_file_content(self, owner: str, repo: str, path: str, ref: str) -> str:
        return self._result("get_file_content", "file contents")

    async def list_review_threads(self, owner: str, repo: str, number: int) -> list[dict]:
        return self._result("list_review_threads", [{"id": "T1", "comments": []}])

    async def list_issue_comments(self, owner: str, repo: str, number: int) -> list[dict]:
        return self._result("list_issue_comments", [{"author": "bob", "body": "hi", "created_at": "2024-01-01"}])

    async def list_reviews(self, owner: str, repo: str, number: int) -> list[dict]:
        return self._result(
            "list_reviews",
            [
                {"author": "carol", "body": "looks good", "state": "APPROVED", "created_at": "2024-01-02"},
                {"author": "dan", "body": "", "state": "COMMENTED", "created_at": "2024-01-03"},
            ],
        )

    async def create_issue_comment(self, owner: str, repo: str, number: int, body: str) -> dict[str, Any]:
        return self._result("create_issue_comment", {"id": 99, "body": body, "author": "alice"})

    async def create_review(self, owner: str, repo: str, number: int, **kw: Any) -> dict[str, Any]:
        self.calls.append(("create_review", (owner, repo, number), kw))
        return self._result("create_review", {"id": 5, "state": "APPROVED"})

    async def reply_review_comment(self, owner: str, repo: str, number: int, comment_id: int, body: str) -> dict:
        return self._result("reply_review_comment", {"id": 7, "body": body})

    async def delete_review_comment(self, owner: str, repo: str, comment_id: int) -> None:
        return self._result("delete_review_comment", None)

    async def resolve_thread(self, thread_id: str, *, resolved: bool = True) -> dict[str, Any]:
        self.calls.append(("resolve_thread", (thread_id,), {"resolved": resolved}))
        return self._result("resolve_thread", {})

    async def merge_pull_request(self, owner: str, repo: str, number: int, **kw: Any) -> dict[str, Any]:
        self.calls.append(("merge_pull_request", (owner, repo, number), kw))
        return self._result("merge_pull_request", {"merged": True, "message": "Merged"})

    async def create_pull_request(self, owner: str, repo: str, **kw: Any) -> dict[str, Any]:
        return self._result("create_pull_request", {})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def db_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def client(test_app: FastAPI, db_factory) -> TestClient:
    test_app.state.db_session_factory = db_factory
    test_app.state.settings.GITHUB_TOKEN = "ghp_dummy"
    return TestClient(test_app)


def _make_pr_dict(owner: str, name: str, number: int, **extra: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    data = {
        "github_id": f"{owner}/{name}#{number}",
        "repo_owner": owner,
        "repo_name": name,
        "number": number,
        "title": f"PR {number}",
        "body": None,
        "state": "open",
        "draft": False,
        "review_decision": None,
        "merge_status": None,
        "project_name": None,
        "project_path": None,
        "author": "alice",
        "author_avatar_url": None,
        "url": f"https://github.com/{owner}/{name}/pull/{number}",
        "base_ref": "main",
        "head_ref": f"feature-{number}",
        "head_sha": "abc123",
        "additions": 1,
        "deletions": 0,
        "changed_files": 1,
        "role": "created",
        "created_at": now,
        "updated_at": now,
    }
    data.update(extra)
    return data


async def _seed_pr(db_factory, backend_id: str, owner="acme", name="web", number=1, **extra) -> str:
    now = datetime.now(UTC)
    row = GitHubPRModel(
        id=uuid.uuid4(),
        backend_id=backend_id,
        synced_at=now,
        **_make_pr_dict(owner, name, number, **extra),
    )
    async with db_factory() as db:
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return str(row.id)


def _backend_id(client: TestClient) -> str:
    return client.app.state.settings.RCFLOW_BACKEND_ID


def _patch_service(monkeypatch, svc: _FakeService) -> None:
    monkeypatch.setattr(gh_mod, "GitHubService", lambda token: svc)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_requires_api_key(client: TestClient):
    assert client.get("/api/integrations/github/prs").status_code == 401


# ---------------------------------------------------------------------------
# /status and /status/check
# ---------------------------------------------------------------------------


def test_status_no_token(client: TestClient):
    client.app.state.settings.GITHUB_TOKEN = ""
    resp = client.get("/api/integrations/github/status", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["configured"] is False


def test_status_valid_token(client: TestClient, monkeypatch):
    _patch_service(monkeypatch, _FakeService())
    resp = client.get("/api/integrations/github/status", headers=_auth_headers())
    body = resp.json()
    assert body["valid"] is True
    assert body["login"] == "alice"


def test_status_check_endpoint(client: TestClient, monkeypatch):
    _patch_service(monkeypatch, _FakeService())
    resp = client.post(
        "/api/integrations/github/status/check",
        json={"token": "ghp_typed"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["valid"] is True


def test_status_check_empty_token(client: TestClient):
    resp = client.post("/api/integrations/github/status/check", json={"token": "  "}, headers=_auth_headers())
    assert resp.json()["configured"] is False


# ---------------------------------------------------------------------------
# repo-defaults
# ---------------------------------------------------------------------------


def test_repo_defaults_empty(client: TestClient):
    resp = client.get("/api/integrations/github/repo-defaults", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json() == {"defaults": []}


def test_set_repo_default_roundtrip(client: TestClient):
    resp = client.put(
        "/api/integrations/github/repo-defaults",
        json={"owner": "acme", "repo": "web", "is_default": True},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json() == {"owner": "acme", "repo": "web", "is_default": True}

    listed = client.get("/api/integrations/github/repo-defaults", headers=_auth_headers()).json()
    assert listed["defaults"] == [{"owner": "acme", "repo": "web"}]

    # Idempotent: setting true again does not duplicate.
    client.put(
        "/api/integrations/github/repo-defaults",
        json={"owner": "acme", "repo": "web", "is_default": True},
        headers=_auth_headers(),
    )
    assert len(client.get("/api/integrations/github/repo-defaults", headers=_auth_headers()).json()["defaults"]) == 1

    # Clearing removes it.
    client.put(
        "/api/integrations/github/repo-defaults",
        json={"owner": "acme", "repo": "web", "is_default": False},
        headers=_auth_headers(),
    )
    assert client.get("/api/integrations/github/repo-defaults", headers=_auth_headers()).json()["defaults"] == []


def test_set_repo_default_clear_missing_is_noop(client: TestClient):
    # Clearing a non-existent default is a harmless no-op.
    resp = client.put(
        "/api/integrations/github/repo-defaults",
        json={"owner": "x", "repo": "y", "is_default": False},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200


def test_set_repo_default_validation(client: TestClient):
    resp = client.put(
        "/api/integrations/github/repo-defaults",
        json={"owner": "  ", "repo": "web", "is_default": True},
        headers=_auth_headers(),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# /sync
# ---------------------------------------------------------------------------


def test_sync_no_token(client: TestClient):
    client.app.state.settings.GITHUB_TOKEN = ""
    resp = client.post("/api/integrations/github/sync", headers=_auth_headers())
    assert resp.json() == {"synced": 0, "archived_pruned": 0, "configured": False}


def test_sync_happy_path(client: TestClient, monkeypatch):
    svc = _FakeService(list_pull_requests=[_make_pr_dict("acme", "web", 1)])
    _patch_service(monkeypatch, svc)
    resp = client.post("/api/integrations/github/sync?force=true&role=created", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["synced"] == 1
    assert svc.closed


def test_sync_throttled(client: TestClient, db_factory, monkeypatch):

    async def _seed():
        await _seed_pr(db_factory, _backend_id(client))

    asyncio.get_event_loop().run_until_complete(_seed())
    svc = _FakeService(list_pull_requests=[_make_pr_dict("acme", "web", 2)])
    _patch_service(monkeypatch, svc)
    # Without force, a recent sync (<60s) is skipped.
    resp = client.post("/api/integrations/github/sync", headers=_auth_headers())
    assert resp.json()["skipped"] is True


def test_sync_all_bucket(client: TestClient, monkeypatch):
    svc = _FakeService(list_pull_requests=[_make_pr_dict("acme", "web", 3, role="all")])
    _patch_service(monkeypatch, svc)
    client.app.state.settings.GITHUB_DEFAULT_REPO = "acme/web"
    resp = client.post("/api/integrations/github/sync?force=true&role=all", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["configured"] is True


def test_sync_service_error(client: TestClient, monkeypatch):
    svc = _FakeService(list_pull_requests=GitHubServiceError("boom"))
    _patch_service(monkeypatch, svc)
    resp = client.post("/api/integrations/github/sync?force=true&role=created", headers=_auth_headers())
    assert resp.status_code == 502
    assert svc.closed


# ---------------------------------------------------------------------------
# /prs listing + single
# ---------------------------------------------------------------------------


def test_list_prs_with_filters(client: TestClient, db_factory):

    async def _seed():
        await _seed_pr(db_factory, _backend_id(client), name="web", number=1, role="created", title="Alpha")
        await _seed_pr(db_factory, _backend_id(client), name="api", number=2, role="for_me", title="Beta")

    asyncio.get_event_loop().run_until_complete(_seed())

    all_resp = client.get("/api/integrations/github/prs", headers=_auth_headers()).json()
    assert all_resp["total"] == 2

    role_resp = client.get("/api/integrations/github/prs?role=for_me", headers=_auth_headers()).json()
    assert role_resp["total"] == 1
    assert role_resp["prs"][0]["title"] == "Beta"

    q_resp = client.get("/api/integrations/github/prs?q=alph", headers=_auth_headers()).json()
    assert q_resp["total"] == 1
    assert q_resp["prs"][0]["title"] == "Alpha"

    state_resp = client.get("/api/integrations/github/prs?state=open", headers=_auth_headers()).json()
    assert state_resp["total"] == 2


def test_get_pr_found(client: TestClient, db_factory):

    pr_id = asyncio.get_event_loop().run_until_complete(_seed_pr(db_factory, _backend_id(client)))
    resp = client.get(f"/api/integrations/github/prs/{pr_id}", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["id"] == pr_id


def test_get_pr_not_found(client: TestClient):
    resp = client.get(f"/api/integrations/github/prs/{uuid.uuid4()}", headers=_auth_headers())
    assert resp.status_code == 404


def test_get_pr_invalid_uuid(client: TestClient):
    resp = client.get("/api/integrations/github/prs/not-a-uuid", headers=_auth_headers())
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Live PR sub-resources (files / diff / file / threads / conversation)
# ---------------------------------------------------------------------------


@pytest.fixture
def seeded_pr(client: TestClient, db_factory) -> str:

    return asyncio.get_event_loop().run_until_complete(_seed_pr(db_factory, _backend_id(client)))


def test_pr_files(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService())
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/files", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["files"][0]["filename"] == "a.py"


def test_pr_files_no_token(client: TestClient, seeded_pr):
    client.app.state.settings.GITHUB_TOKEN = ""
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/files", headers=_auth_headers())
    assert resp.status_code == 503


def test_pr_files_service_error(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService(list_pr_files=GitHubServiceError("nope")))
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/files", headers=_auth_headers())
    assert resp.status_code == 502


def test_pr_diff(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService())
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/diff", headers=_auth_headers())
    assert resp.status_code == 200
    assert "diff --git" in resp.json()["diff"]


def test_pr_file_content(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService())
    resp = client.get(
        f"/api/integrations/github/prs/{seeded_pr}/file?path=a.py&side=base",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["side"] == "base"
    assert resp.json()["content"] == "file contents"


def test_pr_file_bad_side(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService())
    resp = client.get(
        f"/api/integrations/github/prs/{seeded_pr}/file?path=a.py&side=sideways",
        headers=_auth_headers(),
    )
    assert resp.status_code == 422


def test_pr_threads(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService())
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/threads", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_pr_conversation(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService())
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/conversation", headers=_auth_headers())
    assert resp.status_code == 200
    body = resp.json()
    # 1 comment + 1 reviewed (APPROVED) item; the empty COMMENTED review is skipped.
    assert body["total"] == 2
    kinds = {i["kind"] for i in body["items"]}
    assert kinds == {"comment", "review"}


def test_pr_conversation_service_error(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService(list_issue_comments=GitHubServiceError("x")))
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/conversation", headers=_auth_headers())
    assert resp.status_code == 502


def test_post_conversation(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService())
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/conversation",
        json={"body": "thanks!"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["comment"]["body"] == "thanks!"


def test_post_conversation_empty(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService())
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/conversation",
        json={"body": "   "},
        headers=_auth_headers(),
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Draft lifecycle
# ---------------------------------------------------------------------------


def test_draft_empty_default(client: TestClient, seeded_pr):
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/draft", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["event"] == "COMMENT"
    assert resp.json()["comments"] == []


def test_patch_draft(client: TestClient, seeded_pr):
    resp = client.patch(
        f"/api/integrations/github/prs/{seeded_pr}/draft",
        json={"event": "APPROVE", "body": "LGTM"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["event"] == "APPROVE"
    assert resp.json()["body"] == "LGTM"
    # Now GET returns the persisted draft.
    got = client.get(f"/api/integrations/github/prs/{seeded_pr}/draft", headers=_auth_headers()).json()
    assert got["event"] == "APPROVE"


def test_patch_draft_invalid_event(client: TestClient, seeded_pr):
    resp = client.patch(
        f"/api/integrations/github/prs/{seeded_pr}/draft",
        json={"event": "NUKE"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 422


def test_add_and_delete_draft_comment(client: TestClient, seeded_pr):
    add = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/draft/comments",
        json={"path": "a.py", "line": 10, "side": "RIGHT", "body": "nit"},
        headers=_auth_headers(),
    )
    assert add.status_code == 200
    assert len(add.json()["comments"]) == 1

    # Multi-line comment.
    add2 = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/draft/comments",
        json={"path": "a.py", "line": 12, "side": "RIGHT", "body": "range", "start_line": 10},
        headers=_auth_headers(),
    )
    assert len(add2.json()["comments"]) == 2
    assert add2.json()["comments"][1]["start_line"] == 10

    deleted = client.delete(
        f"/api/integrations/github/prs/{seeded_pr}/draft/comments/0",
        headers=_auth_headers(),
    )
    assert deleted.status_code == 200
    assert len(deleted.json()["comments"]) == 1


def test_add_draft_comment_bad_side(client: TestClient, seeded_pr):
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/draft/comments",
        json={"path": "a.py", "line": 1, "side": "UP", "body": "x"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 422


def test_add_draft_comment_start_after_line(client: TestClient, seeded_pr):
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/draft/comments",
        json={"path": "a.py", "line": 5, "side": "RIGHT", "body": "x", "start_line": 9},
        headers=_auth_headers(),
    )
    assert resp.status_code == 422


def test_add_draft_comment_bad_start_side(client: TestClient, seeded_pr):
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/draft/comments",
        json={"path": "a.py", "line": 5, "side": "RIGHT", "body": "x", "start_line": 1, "start_side": "DOWN"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 422


def test_delete_draft_comment_no_draft(client: TestClient, seeded_pr):
    resp = client.delete(
        f"/api/integrations/github/prs/{seeded_pr}/draft/comments/0",
        headers=_auth_headers(),
    )
    assert resp.status_code == 404


def test_delete_draft_comment_out_of_range(client: TestClient, seeded_pr):
    client.post(
        f"/api/integrations/github/prs/{seeded_pr}/draft/comments",
        json={"path": "a.py", "line": 1, "side": "RIGHT", "body": "x"},
        headers=_auth_headers(),
    )
    resp = client.delete(
        f"/api/integrations/github/prs/{seeded_pr}/draft/comments/9",
        headers=_auth_headers(),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Submit review
# ---------------------------------------------------------------------------


def test_submit_review(client: TestClient, seeded_pr, monkeypatch):
    # Queue a comment first so the queued-comments path is exercised.
    client.post(
        f"/api/integrations/github/prs/{seeded_pr}/draft/comments",
        json={"path": "a.py", "line": 1, "side": "RIGHT", "body": "x"},
        headers=_auth_headers(),
    )
    svc = _FakeService(get_pull_request=_make_pr_dict("acme", "web", 1))
    _patch_service(monkeypatch, svc)
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/review",
        json={"event": "APPROVE", "body": "ship it"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["review"]["state"] == "APPROVED"
    # Draft was cleared after submit.
    got = client.get(f"/api/integrations/github/prs/{seeded_pr}/draft", headers=_auth_headers()).json()
    assert got["comments"] == []


def test_submit_review_invalid_event(client: TestClient, seeded_pr):
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/review",
        json={"event": "MAYBE"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 422


def test_submit_review_service_error(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService(create_review=GitHubServiceError("rejected")))
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/review",
        json={"event": "COMMENT", "body": ""},
        headers=_auth_headers(),
    )
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Reply / delete comment / resolve thread
# ---------------------------------------------------------------------------


def test_reply_comment(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService())
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/comments/123/reply",
        json={"body": "agreed"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["body"] == "agreed"


def test_reply_comment_service_error(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService(reply_review_comment=GitHubServiceError("x")))
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/comments/123/reply",
        json={"body": "agreed"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 502


def test_delete_comment(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService())
    resp = client.delete(
        f"/api/integrations/github/prs/{seeded_pr}/comments/55",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json() == {"deleted": True, "comment_id": 55}


def test_delete_comment_service_error(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService(delete_review_comment=GitHubServiceError("forbidden")))
    resp = client.delete(
        f"/api/integrations/github/prs/{seeded_pr}/comments/55",
        headers=_auth_headers(),
    )
    assert resp.status_code == 502


def test_resolve_thread_default(client: TestClient, seeded_pr, monkeypatch):
    svc = _FakeService()
    _patch_service(monkeypatch, svc)
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/threads/THREAD123/resolve",
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json() == {"thread_id": "THREAD123", "resolved": True}


def test_resolve_thread_unresolve(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService())
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/threads/T9/resolve",
        json={"resolved": False},
        headers=_auth_headers(),
    )
    assert resp.json()["resolved"] is False


def test_resolve_thread_service_error(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService(resolve_thread=GitHubServiceError("x")))
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/threads/T9/resolve",
        headers=_auth_headers(),
    )
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# project resolution
# ---------------------------------------------------------------------------


def test_pr_project_no_clone(client: TestClient, seeded_pr, monkeypatch):
    async def _none(*a, **k):
        return None

    monkeypatch.setattr(gh_mod.git_ops, "find_local_repo", _none)
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/project", headers=_auth_headers())
    assert resp.status_code == 200
    assert resp.json()["project_name"] is None


def test_pr_project_found(client: TestClient, seeded_pr, monkeypatch, tmp_path):
    match = tmp_path / "web"
    match.mkdir()

    async def _found(*a, **k):
        return match

    monkeypatch.setattr(gh_mod.git_ops, "find_local_repo", _found)
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/project", headers=_auth_headers())
    assert resp.json()["project_name"] == "web"
    assert resp.json()["project_path"] == str(match)


# ---------------------------------------------------------------------------
# conflicts
# ---------------------------------------------------------------------------


def test_conflicts_clean(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService(get_pull_request={"mergeable": True, "mergeable_state": "clean"}))
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/conflicts", headers=_auth_headers())
    assert resp.json()["reason"] == "clean"
    assert resp.json()["mergeable"] is True


def test_conflicts_blocked(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService(get_pull_request={"mergeable": False, "mergeable_state": "blocked"}))
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/conflicts", headers=_auth_headers())
    assert resp.json()["reason"] == "blocked"


def test_conflicts_computing(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService(get_pull_request={"mergeable": None, "mergeable_state": "unknown"}))
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/conflicts", headers=_auth_headers())
    assert resp.json()["reason"] == "computing"
    assert resp.json()["conflicted"] is None


def test_conflicts_no_local_clone(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService(get_pull_request={"mergeable": False, "mergeable_state": "dirty"}))

    async def _none(*a, **k):
        return None

    monkeypatch.setattr(gh_mod.git_ops, "find_local_repo", _none)
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/conflicts", headers=_auth_headers())
    assert resp.json()["reason"] == "no_local_clone"
    assert resp.json()["conflicted"] is True


def test_conflicts_with_files(client: TestClient, seeded_pr, monkeypatch, tmp_path):
    _patch_service(monkeypatch, _FakeService(get_pull_request={"mergeable": False, "mergeable_state": "dirty"}))
    clone = tmp_path / "web"
    clone.mkdir()

    async def _found(*a, **k):
        return clone

    async def _files(*a, **k):
        return ["conflict.py"]

    monkeypatch.setattr(gh_mod.git_ops, "find_local_repo", _found)
    monkeypatch.setattr(gh_mod.git_ops, "merge_conflict_files", _files)
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/conflicts", headers=_auth_headers())
    assert resp.json()["reason"] == "conflicting"
    assert resp.json()["files"] == ["conflict.py"]


def test_conflicts_clean_after_local_merge(client: TestClient, seeded_pr, monkeypatch, tmp_path):
    _patch_service(monkeypatch, _FakeService(get_pull_request={"mergeable": False, "mergeable_state": "dirty"}))
    clone = tmp_path / "web"
    clone.mkdir()

    async def _found(*a, **k):
        return clone

    async def _no_files(*a, **k):
        return []

    monkeypatch.setattr(gh_mod.git_ops, "find_local_repo", _found)
    monkeypatch.setattr(gh_mod.git_ops, "merge_conflict_files", _no_files)
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/conflicts", headers=_auth_headers())
    assert resp.json()["reason"] == "clean"


def test_conflicts_merge_tool_unavailable(client: TestClient, seeded_pr, monkeypatch, tmp_path):
    _patch_service(monkeypatch, _FakeService(get_pull_request={"mergeable": False, "mergeable_state": "dirty"}))
    clone = tmp_path / "web"
    clone.mkdir()

    async def _found(*a, **k):
        return clone

    async def _raise(*a, **k):
        raise gh_mod.git_ops.MergeToolUnavailableError("too old")

    monkeypatch.setattr(gh_mod.git_ops, "find_local_repo", _found)
    monkeypatch.setattr(gh_mod.git_ops, "merge_conflict_files", _raise)
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/conflicts", headers=_auth_headers())
    assert resp.json()["reason"] == "no_local_clone"


def test_conflicts_git_ops_error(client: TestClient, seeded_pr, monkeypatch, tmp_path):
    _patch_service(monkeypatch, _FakeService(get_pull_request={"mergeable": False, "mergeable_state": "dirty"}))
    clone = tmp_path / "web"
    clone.mkdir()

    async def _found(*a, **k):
        return clone

    async def _raise(*a, **k):
        raise gh_mod.git_ops.GitOpsError("merge failed")

    monkeypatch.setattr(gh_mod.git_ops, "find_local_repo", _found)
    monkeypatch.setattr(gh_mod.git_ops, "merge_conflict_files", _raise)
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/conflicts", headers=_auth_headers())
    assert resp.json()["reason"] == "no_local_clone"


def test_conflicts_service_error(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService(get_pull_request=GitHubServiceError("x")))
    resp = client.get(f"/api/integrations/github/prs/{seeded_pr}/conflicts", headers=_auth_headers())
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# merge
# ---------------------------------------------------------------------------


def test_merge_happy(client: TestClient, seeded_pr, monkeypatch):
    svc = _FakeService(get_pull_request=_make_pr_dict("acme", "web", 1))
    _patch_service(monkeypatch, svc)
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/merge",
        json={"method": "squash"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    assert resp.json()["merged"] is True


def test_merge_default_body(client: TestClient, seeded_pr, monkeypatch):
    svc = _FakeService(get_pull_request=_make_pr_dict("acme", "web", 1))
    _patch_service(monkeypatch, svc)
    resp = client.post(f"/api/integrations/github/prs/{seeded_pr}/merge", headers=_auth_headers())
    assert resp.status_code == 200


def test_merge_invalid_method(client: TestClient, seeded_pr):
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/merge",
        json={"method": "fastforward"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 422


def test_merge_conflict_405(client: TestClient, seeded_pr, monkeypatch):
    err = GitHubServiceError("HTTP 405", status_code=405)
    _patch_service(monkeypatch, _FakeService(merge_pull_request=err))
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/merge",
        json={"method": "merge"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 409


def test_merge_other_error(client: TestClient, seeded_pr, monkeypatch):
    _patch_service(monkeypatch, _FakeService(merge_pull_request=GitHubServiceError("boom")))
    resp = client.post(
        f"/api/integrations/github/prs/{seeded_pr}/merge",
        json={"method": "merge"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# open-pr
# ---------------------------------------------------------------------------


def test_open_pr_no_token(client: TestClient):
    client.app.state.settings.GITHUB_TOKEN = ""
    resp = client.post(
        "/api/integrations/github/open-pr",
        json={"selected_worktree_path": "/tmp/x", "title": "T"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 503


def test_open_pr_no_path_or_project(client: TestClient):
    resp = client.post(
        "/api/integrations/github/open-pr",
        json={"title": "T"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 422


def test_open_pr_no_remote(client: TestClient, monkeypatch):
    async def _no_remote(path, remote="origin"):
        return None

    monkeypatch.setattr(gh_mod.git_ops, "parse_github_remote", _no_remote)
    resp = client.post(
        "/api/integrations/github/open-pr",
        json={"selected_worktree_path": "/tmp/x", "title": "T"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 422


def test_open_pr_push_error(client: TestClient, monkeypatch):
    async def _remote(path, remote="origin"):
        return ("acme", "web")

    async def _push_fail(*a, **k):
        raise gh_mod.git_ops.GitOpsError("push rejected")

    monkeypatch.setattr(gh_mod.git_ops, "parse_github_remote", _remote)
    monkeypatch.setattr(gh_mod.git_ops, "push_branch", _push_fail)
    resp = client.post(
        "/api/integrations/github/open-pr",
        json={"selected_worktree_path": "/tmp/x", "title": "T"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 502


def test_open_pr_happy(client: TestClient, monkeypatch):
    async def _remote(path, remote="origin"):
        return ("acme", "web")

    async def _commit(path, msg):
        return True

    async def _push(*a, **k):
        return "feature-1"

    monkeypatch.setattr(gh_mod.git_ops, "parse_github_remote", _remote)
    monkeypatch.setattr(gh_mod.git_ops, "commit_all", _commit)
    monkeypatch.setattr(gh_mod.git_ops, "push_branch", _push)
    _patch_service(monkeypatch, _FakeService(create_pull_request=_make_pr_dict("acme", "web", 10)))
    resp = client.post(
        "/api/integrations/github/open-pr",
        json={
            "selected_worktree_path": "/tmp/x",
            "title": "New PR",
            "commit_message": "wip",
            "draft": True,
        },
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["pr"]["number"] == 10
    assert body["url"]


def test_open_pr_create_error(client: TestClient, monkeypatch):
    async def _remote(path, remote="origin"):
        return ("acme", "web")

    async def _push(*a, **k):
        return "feature-1"

    monkeypatch.setattr(gh_mod.git_ops, "parse_github_remote", _remote)
    monkeypatch.setattr(gh_mod.git_ops, "push_branch", _push)
    _patch_service(monkeypatch, _FakeService(create_pull_request=GitHubServiceError("422 from github")))
    resp = client.post(
        "/api/integrations/github/open-pr",
        json={"selected_worktree_path": "/tmp/x", "title": "T"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 502


def test_open_pr_resolve_by_project_name(client: TestClient, monkeypatch, tmp_path):
    # project_name resolved under projects_dirs.
    projects = tmp_path / "Projects"
    (projects / "web").mkdir(parents=True)
    client.app.state.settings.PROJECTS_DIR = str(projects)

    # _resolve_worktree_path iterates settings.projects_dirs.
    async def _remote(path, remote="origin"):
        assert path == str(projects / "web")
        return ("acme", "web")

    async def _push(*a, **k):
        return "feature-1"

    monkeypatch.setattr(gh_mod.git_ops, "parse_github_remote", _remote)
    monkeypatch.setattr(gh_mod.git_ops, "push_branch", _push)
    _patch_service(monkeypatch, _FakeService(create_pull_request=_make_pr_dict("acme", "web", 11)))

    # Point projects_dirs at our tmp dir via monkeypatching the settings property
    # is not trivial; instead rely on settings.projects_dirs already returning it.
    monkeypatch.setattr(type(client.app.state.settings), "projects_dirs", property(lambda self: [projects]))
    resp = client.post(
        "/api/integrations/github/open-pr",
        json={"project_name": "web", "title": "T"},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
