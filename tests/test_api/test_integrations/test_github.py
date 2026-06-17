"""Tests for the GitHub integration sync persistence helper.

Covers `_persist_synced_prs`: archived-repo PRs are never cached, and any
previously-cached rows for a now-archived repo are pruned and reported for
deletion broadcasts.
"""

from __future__ import annotations

import subprocess
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.api.integrations import github as gh_mod
from src.api.integrations.github import (
    _build_token_status,
    _cached_repo_slugs,
    _draft_to_dict,
    _persist_synced_prs,
    _pr_to_dict,
    _upsert_prs,
)
from src.database.models import Base
from src.database.models import GitHubPR as GitHubPRModel
from src.database.models import GitHubReviewDraft as GitHubReviewDraftModel
from src.services.github_service import GitHubServiceError

_BACKEND_ID = "backend-1"


@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def _parsed(owner: str, name: str, number: int, *, archived: bool = False) -> dict:
    now = datetime.now(UTC)
    return {
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
        "archived": archived,
        "role": "created",
        "created_at": now,
        "updated_at": now,
    }


async def _count(db) -> int:
    return (await db.execute(select(func.count()).select_from(GitHubPRModel))).scalar_one()


@pytest.mark.asyncio
async def test_persist_skips_archived_repo_prs(db_session):
    parsed = [
        _parsed("acme", "web", 1),
        _parsed("acme", "old", 2, archived=True),
    ]
    upserted, deleted_ids = await _persist_synced_prs(db_session, _BACKEND_ID, parsed)

    assert {r.repo_name for r in upserted} == {"web"}
    assert deleted_ids == []
    assert await _count(db_session) == 1


@pytest.mark.asyncio
async def test_persist_prunes_previously_cached_archived(db_session):
    # Seed a cached PR for a repo that later becomes archived.
    stale = GitHubPRModel(
        id=uuid.uuid4(),
        backend_id=_BACKEND_ID,
        github_id="acme/old#2",
        synced_at=datetime.now(UTC),
        **{k: v for k, v in _parsed("acme", "old", 2).items() if k not in ("github_id", "archived")},
    )
    db_session.add(stale)
    await db_session.commit()
    stale_id = str(stale.id)

    # Next sync returns the repo's PR flagged archived (search still lists it).
    parsed = [
        _parsed("acme", "web", 1),
        _parsed("acme", "old", 2, archived=True),
    ]
    upserted, deleted_ids = await _persist_synced_prs(db_session, _BACKEND_ID, parsed)

    assert {r.repo_name for r in upserted} == {"web"}
    assert deleted_ids == [stale_id]
    # Only the fresh non-archived PR remains.
    rows = (await db_session.execute(select(GitHubPRModel))).scalars().all()
    assert [r.repo_name for r in rows] == ["web"]


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.mark.asyncio
async def test_persist_stamps_local_project(db_session, tmp_path):
    # A local clone of acme/web under a projects dir.
    projects = tmp_path / "Projects"
    clone = projects / "web"
    clone.mkdir(parents=True)
    _git(clone, "init", "-q")
    _git(clone, "remote", "add", "origin", "https://github.com/acme/web.git")

    parsed = [_parsed("acme", "web", 1), _parsed("acme", "other", 2)]
    upserted, _ = await _persist_synced_prs(db_session, _BACKEND_ID, parsed, [projects])

    by_repo = {r.repo_name: r for r in upserted}
    assert by_repo["web"].project_name == "web"
    assert by_repo["web"].project_path == str(clone)
    # No local clone for acme/other → project stays null.
    assert by_repo["other"].project_name is None


# ---------------------------------------------------------------------------
# _upsert_prs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_inserts_new_rows(db_session):
    rows = await _upsert_prs(db_session, _BACKEND_ID, [_parsed("acme", "web", 1)])

    assert len(rows) == 1
    assert rows[0].github_id == "acme/web#1"
    assert rows[0].synced_at is not None
    assert await _count(db_session) == 1


@pytest.mark.asyncio
async def test_upsert_updates_existing_row_in_place(db_session):
    first = await _upsert_prs(db_session, _BACKEND_ID, [_parsed("acme", "web", 1)])
    original_id = first[0].id

    updated = _parsed("acme", "web", 1)
    updated["title"] = "PR 1 (edited)"
    second = await _upsert_prs(db_session, _BACKEND_ID, [updated])

    # Same github_id → same row updated, not a duplicate.
    assert second[0].id == original_id
    assert second[0].title == "PR 1 (edited)"
    assert await _count(db_session) == 1


@pytest.mark.asyncio
async def test_upsert_all_bucket_does_not_downgrade_role(db_session):
    created = _parsed("acme", "web", 1)
    created["role"] = "created"
    await _upsert_prs(db_session, _BACKEND_ID, [created])

    # A later "all"-bucket sync must not clobber the created/for_me role.
    all_bucket = _parsed("acme", "web", 1)
    all_bucket["role"] = "all"
    rows = await _upsert_prs(db_session, _BACKEND_ID, [all_bucket])

    assert rows[0].role == "created"


@pytest.mark.asyncio
async def test_upsert_all_bucket_sets_role_on_new_row(db_session):
    all_bucket = _parsed("acme", "web", 1)
    all_bucket["role"] = "all"
    rows = await _upsert_prs(db_session, _BACKEND_ID, [all_bucket])

    assert rows[0].role == "all"


# ---------------------------------------------------------------------------
# _cached_repo_slugs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cached_repo_slugs_returns_distinct_slugs(db_session):
    await _upsert_prs(
        db_session,
        _BACKEND_ID,
        [_parsed("acme", "web", 1), _parsed("acme", "web", 2), _parsed("acme", "api", 3)],
    )

    slugs = await _cached_repo_slugs(db_session, _BACKEND_ID)

    assert sorted(slugs) == ["acme/api", "acme/web"]


@pytest.mark.asyncio
async def test_cached_repo_slugs_scoped_to_backend(db_session):
    await _upsert_prs(db_session, _BACKEND_ID, [_parsed("acme", "web", 1)])
    await _upsert_prs(db_session, "other-backend", [_parsed("acme", "api", 2)])

    assert await _cached_repo_slugs(db_session, _BACKEND_ID) == ["acme/web"]


# ---------------------------------------------------------------------------
# _pr_to_dict / _draft_to_dict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_to_dict_is_json_safe(db_session):
    [row] = await _upsert_prs(db_session, _BACKEND_ID, [_parsed("acme", "web", 7)])

    d = _pr_to_dict(row)

    assert d["github_id"] == "acme/web#7"
    assert d["repo_name"] == "web"
    assert d["number"] == 7
    assert isinstance(d["id"], str)
    # Datetimes are ISO strings, not datetime objects.
    assert isinstance(d["created_at"], str)
    assert isinstance(d["synced_at"], str)
    assert d["task_id"] is None


def test_draft_to_dict_parses_comment_json():
    now = datetime.now(UTC)
    draft = GitHubReviewDraftModel(
        id=uuid.uuid4(),
        backend_id=_BACKEND_ID,
        pr_id=uuid.uuid4(),
        event="REQUEST_CHANGES",
        body="needs work",
        comments='[{"path": "a.py", "line": 1, "side": "RIGHT", "body": "nit"}]',
        created_at=now,
        updated_at=now,
    )

    d = _draft_to_dict(draft)

    assert d["event"] == "REQUEST_CHANGES"
    assert d["body"] == "needs work"
    assert d["comments"] == [{"path": "a.py", "line": 1, "side": "RIGHT", "body": "nit"}]
    assert isinstance(d["id"], str)
    assert isinstance(d["pr_id"], str)


def test_draft_to_dict_defaults_empty_comments():
    now = datetime.now(UTC)
    draft = GitHubReviewDraftModel(
        id=uuid.uuid4(),
        backend_id=_BACKEND_ID,
        pr_id=uuid.uuid4(),
        event="COMMENT",
        body="",
        comments="",
        created_at=now,
        updated_at=now,
    )

    assert _draft_to_dict(draft)["comments"] == []


# ---------------------------------------------------------------------------
# _build_token_status
# ---------------------------------------------------------------------------


class _FakeService:
    """Stand-in for GitHubService used by _build_token_status tests."""

    def __init__(self, *, info: dict | None = None, error: Exception | None = None) -> None:
        self._info = info
        self._error = error
        self.closed = False

    async def token_info(self) -> dict:
        if self._error is not None:
            raise self._error
        assert self._info is not None
        return self._info

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_build_token_status_no_token():
    status = await _build_token_status(None)

    assert status == {
        "configured": False,
        "valid": False,
        "login": None,
        "fine_grained": False,
        "scopes": status["scopes"],
    }
    # Every required scope is reported unsatisfied when unconfigured.
    assert all(s["satisfied"] is False for s in status["scopes"])


@pytest.mark.asyncio
async def test_build_token_status_invalid_token(monkeypatch):
    fake = _FakeService(error=GitHubServiceError("bad credentials"))
    monkeypatch.setattr(gh_mod, "GitHubService", lambda token: fake)

    status = await _build_token_status("ghp_bad")

    assert status["configured"] is True
    assert status["valid"] is False
    assert status["error"] == "bad credentials"
    assert fake.closed  # service always closed in finally


@pytest.mark.asyncio
async def test_build_token_status_valid_classic_token(monkeypatch):
    fake = _FakeService(info={"fine_grained": False, "login": "alice", "scopes": ["repo", "read:org"]})
    monkeypatch.setattr(gh_mod, "GitHubService", lambda token: fake)

    status = await _build_token_status("ghp_good")

    assert status["valid"] is True
    assert status["login"] == "alice"
    assert status["fine_grained"] is False
    assert status["granted"] == ["repo", "read:org"]
    # repo scope granted → satisfied flag set true for it.
    assert any(s.get("satisfied") is True for s in status["scopes"])
    assert fake.closed


@pytest.mark.asyncio
async def test_build_token_status_fine_grained_token(monkeypatch):
    fake = _FakeService(info={"fine_grained": True, "login": "bob", "scopes": []})
    monkeypatch.setattr(gh_mod, "GitHubService", lambda token: fake)

    status = await _build_token_status("github_pat_xxx")

    assert status["valid"] is True
    assert status["fine_grained"] is True
    # Fine-grained tokens can't enumerate scopes → satisfied is null.
    assert all(s["satisfied"] is None for s in status["scopes"])
