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

from src.api.integrations.github import _persist_synced_prs
from src.database.models import Base
from src.database.models import GitHubPR as GitHubPRModel

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
