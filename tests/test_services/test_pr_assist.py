"""Tests for the read-only PR-assist prompt builders in src/services/pr_assist.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.services.pr_assist import (
    MAX_DIFF_CHARS,
    PR_ASSIST_KINDS,
    READ_ONLY_KINDS,
    _explain_prompt,
    _fix_prompt,
    _resolve_conflicts_prompt,
    _review_prompt,
    _summary_prompt,
    _truncate,
    build_pr_assist_prompt,
)


class _PR:
    number = 42
    title = "Fix SSO"
    repo_owner = "acme"
    repo_name = "web"
    base_ref = "main"
    head_ref = "fix-sso"
    author = "alice"
    body = "desc"


def test_truncate_under_limit_unchanged():
    assert _truncate("short") == "short"


def test_truncate_over_limit_appends_notice():
    big = "x" * (MAX_DIFF_CHARS + 100)
    out = _truncate(big)
    assert out.startswith("x" * 10)
    assert "truncated" in out and len(out) < len(big) + 100


def test_summary_prompt_mentions_pr_and_diff():
    p = _summary_prompt(_PR(), "@@ -1 +1 @@\n-a\n+b")
    assert "#42" in p and "acme/web" in p and "read-only" in p.lower()
    assert "```diff" in p and "+b" in p


def test_explain_prompt_scopes_to_file():
    p = _explain_prompt(_PR(), "app/main.py", "@@ -1 +1 @@")
    assert "app/main.py" in p and "read-only" in p.lower()


def test_fix_prompt_includes_comment_and_no_push():
    p = _fix_prompt(_PR(), "app/main.py", 12, "use <= not <")
    assert "use <= not <" in p and "app/main.py" in p
    assert "do not push" in p.lower() or "do NOT push" in p


def test_resolve_conflicts_prompt_reports_then_asks_permission():
    p = _resolve_conflicts_prompt(_PR(), ["a.txt", "src/b.py"])
    assert "#42" in p and "acme/web" in p
    assert "main" in p and "fix-sso" in p
    assert "a.txt" in p and "src/b.py" in p
    assert "report" in p.lower()
    # Reports first, then asks before committing/pushing.
    assert "permission" in p.lower() or "ask" in p.lower()
    assert "until they approve" in p.lower()


def test_resolve_conflicts_prompt_without_file_hint():
    p = _resolve_conflicts_prompt(_PR(), [])
    assert "conflict" in p.lower() and "pre-check found" not in p


def test_review_prompt_uses_wt_worktree_and_gh_no_embedded_diff():
    p = _review_prompt(_PR(), "- Inline foo.py:10:\n    @alice: nit")
    assert "#42" in p and "acme/web" in p
    # Worktree via the wt CLI, pull latest, gh for context, local-git fallback.
    assert "`wt`" in p and "worktree" in p.lower()
    assert "git pull" in p or "fetch origin" in p
    assert "gh pr" in p and "warning" in p.lower()
    assert "git diff" in p
    # Description quoted verbatim in a fenced block; existing comments included;
    # the (possibly huge) diff is NOT embedded.
    assert "verbatim" in p.lower() and "```" in p
    assert "Inline foo.py:10" in p
    assert "```diff" not in p
    # Structured report + ask + authorship warning.
    assert "findings table" in p.lower()
    assert "Approve" in p and "Request changes" in p
    assert "authored" in p.lower() and "approv" in p.lower()
    assert "Assisted by AI, approved by human" in p


def test_kind_sets():
    assert "fix" in PR_ASSIST_KINDS and "fix" not in READ_ONLY_KINDS
    assert "resolve_conflicts" in PR_ASSIST_KINDS and "resolve_conflicts" not in READ_ONLY_KINDS
    # review is writable now (runs in a checkout to post via gh on approval).
    assert "review" in PR_ASSIST_KINDS and "review" not in READ_ONLY_KINDS
    assert set(READ_ONLY_KINDS) == {"summary", "explain"}


@pytest.mark.asyncio
async def test_fix_skips_token_check():
    # fix does not need a token; it should get PAST the token gate and fail on
    # the (bad) PR id instead of complaining about the token.
    settings = SimpleNamespace(GITHUB_TOKEN="", RCFLOW_BACKEND_ID="b")
    with pytest.raises(ValueError, match="Invalid PR id"):
        await build_pr_assist_prompt(
            settings=settings, db_factory=lambda: None, pr_id="bad", kind="fix", comment_body="x"
        )


@pytest.mark.asyncio
async def test_build_rejects_unknown_kind():
    settings = SimpleNamespace(GITHUB_TOKEN="x", RCFLOW_BACKEND_ID="b")
    with pytest.raises(ValueError, match="Unknown assist kind"):
        await build_pr_assist_prompt(settings=settings, db_factory=lambda: None, pr_id="x", kind="rewrite")


@pytest.mark.asyncio
async def test_build_requires_token():
    settings = SimpleNamespace(GITHUB_TOKEN="", RCFLOW_BACKEND_ID="b")
    with pytest.raises(ValueError, match="token is not configured"):
        await build_pr_assist_prompt(settings=settings, db_factory=lambda: None, pr_id="x", kind="summary")


@pytest.mark.asyncio
async def test_build_rejects_bad_uuid():
    settings = SimpleNamespace(GITHUB_TOKEN="x", RCFLOW_BACKEND_ID="b")
    with pytest.raises(ValueError, match="Invalid PR id"):
        await build_pr_assist_prompt(settings=settings, db_factory=lambda: None, pr_id="not-a-uuid", kind="summary")
