"""Tests for the async ``build_pr_assist_prompt`` orchestration in pr_assist.

Loads a cached ``GitHubPR`` row from an in-memory SQLite database and exercises
every assist ``kind`` (summary / explain / review / fix / resolve_conflicts),
mocking ``GitHubService`` so no real network calls happen.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import src.services.pr_assist as pr_assist
from src.database.models import Base
from src.database.models import GitHubPR as GitHubPRModel
from src.services.github_service import GitHubServiceError

_BACKEND_ID = "backend-1"


@pytest.fixture
async def db_factory():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_pr(db_factory: async_sessionmaker, **overrides) -> str:
    now = datetime.now(UTC)
    pr = GitHubPRModel(
        id=uuid.uuid4(),
        backend_id=_BACKEND_ID,
        github_id=f"PR_{uuid.uuid4().hex[:8]}",
        repo_owner="acme",
        repo_name="web",
        number=42,
        title="Fix SSO",
        body="desc",
        state="open",
        author="alice",
        url="https://github.com/acme/web/pull/42",
        base_ref="main",
        head_ref="fix-sso",
        head_sha="abc123",
        created_at=now,
        updated_at=now,
    )
    for k, v in overrides.items():
        setattr(pr, k, v)
    async with db_factory() as db:
        db.add(pr)
        await db.commit()
        return str(pr.id)


def _settings(token: str = "ghp_x") -> SimpleNamespace:  # noqa: S107
    return SimpleNamespace(GITHUB_TOKEN=token, RCFLOW_BACKEND_ID=_BACKEND_ID)


def _patch_service(monkeypatch: pytest.MonkeyPatch, svc: AsyncMock) -> None:
    monkeypatch.setattr(pr_assist, "GitHubService", lambda *a, **k: svc)


# ---------------------------------------------------------------------------
# PR not found
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pr_not_found_raises(db_factory):
    with pytest.raises(ValueError, match="Pull request not found"):
        await pr_assist.build_pr_assist_prompt(
            settings=_settings(),
            db_factory=db_factory,
            pr_id=str(uuid.uuid4()),
            kind="summary",
        )


@pytest.mark.asyncio
async def test_pr_wrong_backend_not_found(db_factory):
    pr_id = await _seed_pr(db_factory, backend_id="other-backend")
    with pytest.raises(ValueError, match="Pull request not found"):
        await pr_assist.build_pr_assist_prompt(settings=_settings(), db_factory=db_factory, pr_id=pr_id, kind="summary")


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_summary_fetches_diff_and_builds_prompt(db_factory, monkeypatch):
    pr_id = await _seed_pr(db_factory)
    svc = AsyncMock()
    svc.get_pr_diff = AsyncMock(return_value="@@ -1 +1 @@\n-a\n+b")
    svc.aclose = AsyncMock()
    _patch_service(monkeypatch, svc)

    pr_info, prompt = await pr_assist.build_pr_assist_prompt(
        settings=_settings(), db_factory=db_factory, pr_id=pr_id, kind="summary"
    )
    svc.get_pr_diff.assert_awaited_once_with("acme", "web", 42)
    svc.aclose.assert_awaited_once()
    assert "+b" in prompt and "#42" in prompt
    assert pr_info["kind"] == "summary" and pr_info["number"] == 42
    assert pr_info["id"] == pr_id


# ---------------------------------------------------------------------------
# explain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_explain_builds_file_prompt(db_factory, monkeypatch):
    pr_id = await _seed_pr(db_factory)
    svc = AsyncMock()
    svc.list_pr_files = AsyncMock(return_value=[{"filename": "app/main.py", "patch": "@@ -1 +1 @@\n-x\n+y"}])
    svc.aclose = AsyncMock()
    _patch_service(monkeypatch, svc)

    _info, prompt = await pr_assist.build_pr_assist_prompt(
        settings=_settings(),
        db_factory=db_factory,
        pr_id=pr_id,
        kind="explain",
        file_path="app/main.py",
    )
    assert "app/main.py" in prompt and "+y" in prompt
    svc.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_explain_requires_file_path(db_factory, monkeypatch):
    pr_id = await _seed_pr(db_factory)
    svc = AsyncMock()
    svc.aclose = AsyncMock()
    _patch_service(monkeypatch, svc)
    with pytest.raises(ValueError, match="file_path is required"):
        await pr_assist.build_pr_assist_prompt(settings=_settings(), db_factory=db_factory, pr_id=pr_id, kind="explain")
    svc.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_explain_file_not_in_pr(db_factory, monkeypatch):
    pr_id = await _seed_pr(db_factory)
    svc = AsyncMock()
    svc.list_pr_files = AsyncMock(return_value=[{"filename": "other.py", "patch": "@@"}])
    svc.aclose = AsyncMock()
    _patch_service(monkeypatch, svc)
    with pytest.raises(ValueError, match="File not part of this PR"):
        await pr_assist.build_pr_assist_prompt(
            settings=_settings(),
            db_factory=db_factory,
            pr_id=pr_id,
            kind="explain",
            file_path="missing.py",
        )
    svc.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_explain_file_without_patch(db_factory, monkeypatch):
    pr_id = await _seed_pr(db_factory)
    svc = AsyncMock()
    svc.list_pr_files = AsyncMock(return_value=[{"filename": "logo.png", "patch": None}])
    svc.aclose = AsyncMock()
    _patch_service(monkeypatch, svc)
    with pytest.raises(ValueError, match="no textual diff"):
        await pr_assist.build_pr_assist_prompt(
            settings=_settings(),
            db_factory=db_factory,
            pr_id=pr_id,
            kind="explain",
            file_path="logo.png",
        )
    svc.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fix_builds_prompt_no_token_needed(db_factory):
    pr_id = await _seed_pr(db_factory)
    # No GitHubService is used for fix (no monkeypatch needed); also no token.
    _info, prompt = await pr_assist.build_pr_assist_prompt(
        settings=_settings(token=""),
        db_factory=db_factory,
        pr_id=pr_id,
        kind="fix",
        file_path="app/main.py",
        line=12,
        comment_body="use <= not <",
    )
    assert "use <= not <" in prompt and "app/main.py" in prompt


@pytest.mark.asyncio
async def test_fix_requires_comment_body(db_factory):
    pr_id = await _seed_pr(db_factory)
    with pytest.raises(ValueError, match="comment_body is required"):
        await pr_assist.build_pr_assist_prompt(
            settings=_settings(token=""),
            db_factory=db_factory,
            pr_id=pr_id,
            kind="fix",
        )


# ---------------------------------------------------------------------------
# resolve_conflicts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_conflicts_parses_file_hint(db_factory):
    pr_id = await _seed_pr(db_factory)
    _info, prompt = await pr_assist.build_pr_assist_prompt(
        settings=_settings(token=""),
        db_factory=db_factory,
        pr_id=pr_id,
        kind="resolve_conflicts",
        comment_body="a.txt, src/b.py\nc.md",
    )
    assert "a.txt" in prompt and "src/b.py" in prompt and "c.md" in prompt


@pytest.mark.asyncio
async def test_resolve_conflicts_without_hint(db_factory):
    pr_id = await _seed_pr(db_factory)
    _info, prompt = await pr_assist.build_pr_assist_prompt(
        settings=_settings(token=""),
        db_factory=db_factory,
        pr_id=pr_id,
        kind="resolve_conflicts",
    )
    assert "conflict" in prompt.lower() and "pre-check found" not in prompt


# ---------------------------------------------------------------------------
# review
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_with_token_prefills_existing_comments(db_factory, monkeypatch):
    pr_id = await _seed_pr(db_factory)
    svc = AsyncMock()
    svc.list_review_threads = AsyncMock(
        return_value=[{"path": "app/main.py", "line": 10, "comments": [{"author": "bob", "body": "nit"}]}]
    )
    svc.list_issue_comments = AsyncMock(return_value=[{"author": "carol", "body": "looks good"}])
    svc.list_reviews = AsyncMock(return_value=[{"author": "dave", "state": "APPROVED", "body": "ok"}])
    svc.aclose = AsyncMock()
    _patch_service(monkeypatch, svc)

    _info, prompt = await pr_assist.build_pr_assist_prompt(
        settings=_settings(), db_factory=db_factory, pr_id=pr_id, kind="review"
    )
    # The existing comments were fetched and embedded; the diff is NOT embedded.
    assert "app/main.py:10" in prompt
    assert "nit" in prompt
    assert "```diff" not in prompt
    svc.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_swallows_github_error(db_factory, monkeypatch):
    pr_id = await _seed_pr(db_factory)
    svc = AsyncMock()
    svc.list_review_threads = AsyncMock(side_effect=GitHubServiceError("boom"))
    svc.list_issue_comments = AsyncMock(return_value=[])
    svc.list_reviews = AsyncMock(return_value=[])
    svc.aclose = AsyncMock()
    _patch_service(monkeypatch, svc)

    _info, prompt = await pr_assist.build_pr_assist_prompt(
        settings=_settings(), db_factory=db_factory, pr_id=pr_id, kind="review"
    )
    # Best-effort: the prompt is still built even when the fetch failed.
    assert "#42" in prompt and "findings table" in prompt.lower()
    svc.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_review_without_token_skips_fetch(db_factory, monkeypatch):
    pr_id = await _seed_pr(db_factory)

    # No token -> GitHubService must never be constructed.
    def _boom(*_a, **_k):
        raise AssertionError("GitHubService should not be created without a token")

    monkeypatch.setattr(pr_assist, "GitHubService", _boom)
    _info, prompt = await pr_assist.build_pr_assist_prompt(
        settings=_settings(token=""), db_factory=db_factory, pr_id=pr_id, kind="review"
    )
    assert "#42" in prompt
