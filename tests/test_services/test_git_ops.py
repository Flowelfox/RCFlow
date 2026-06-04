"""Tests for src/services/git_ops.py against real local git repositories.

Exercises the git mechanics end-to-end (branch detection, status, commit, push
to a local bare remote). The GitHub-authenticated push URL path is not hit here
— that needs a live remote + token — but the push plumbing (ref, commit) is.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from src.services import git_ops

if TYPE_CHECKING:
    from pathlib import Path


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def _bare_has_ref(bare: Path, ref: str) -> bool:
    out = subprocess.run(["git", "--git-dir", str(bare), "rev-parse", ref], capture_output=True, text=True)
    return out.returncode == 0 and bool(out.stdout.strip())


def _init_work_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Test")
    _git(path, "checkout", "-q", "-b", "feature")
    (path / "a.txt").write_text("one\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")


@pytest.mark.asyncio
async def test_parse_github_remote_https(tmp_path):
    work = tmp_path / "w"
    _init_work_repo(work)
    _git(work, "remote", "add", "gh", "https://github.com/acme/web.git")
    assert await git_ops.parse_github_remote(work, "gh") == ("acme", "web")


@pytest.mark.asyncio
async def test_parse_github_remote_ssh(tmp_path):
    work = tmp_path / "w"
    _init_work_repo(work)
    _git(work, "remote", "add", "gh", "git@github.com:acme/web.git")
    assert await git_ops.parse_github_remote(work, "gh") == ("acme", "web")


@pytest.mark.asyncio
async def test_parse_github_remote_non_github(tmp_path):
    work = tmp_path / "w"
    _init_work_repo(work)
    _git(work, "remote", "add", "gl", "https://gitlab.com/acme/web.git")
    assert await git_ops.parse_github_remote(work, "gl") is None


@pytest.mark.asyncio
async def test_current_branch(tmp_path):
    work = tmp_path / "w"
    _init_work_repo(work)
    assert await git_ops.current_branch(work) == "feature"


@pytest.mark.asyncio
async def test_has_uncommitted_and_commit_all(tmp_path):
    work = tmp_path / "w"
    _init_work_repo(work)
    assert await git_ops.has_uncommitted_changes(work) is False
    (work / "b.txt").write_text("two\n")
    assert await git_ops.has_uncommitted_changes(work) is True

    committed = await git_ops.commit_all(work, "add b")
    assert committed is True
    assert await git_ops.has_uncommitted_changes(work) is False
    # Nothing left to commit → returns False.
    assert await git_ops.commit_all(work, "noop") is False


@pytest.mark.asyncio
async def test_push_branch_to_local_remote(tmp_path):
    bare = tmp_path / "remote.git"
    bare.mkdir()
    _git(bare, "init", "-q", "--bare")

    work = tmp_path / "w"
    _init_work_repo(work)

    pushed = await git_ops.push_branch(work, "dummy-token", remote_url=str(bare))
    assert pushed == "feature"
    # The branch now exists in the bare remote.
    assert _bare_has_ref(bare, "refs/heads/feature")


@pytest.mark.asyncio
async def test_push_branch_no_remote_raises(tmp_path):
    work = tmp_path / "w"
    _init_work_repo(work)  # no github origin
    with pytest.raises(git_ops.GitOpsError, match="No GitHub"):
        await git_ops.push_branch(work, "tok")


@pytest.mark.asyncio
async def test_find_local_repo_matches_by_remote(tmp_path):
    projects = tmp_path / "Projects"
    projects.mkdir()
    # A matching clone of acme/web.
    match = projects / "web"
    _init_work_repo(match)
    _git(match, "remote", "add", "origin", "https://github.com/Acme/Web.git")
    # A non-matching repo + a plain (non-git) dir.
    other = projects / "other"
    _init_work_repo(other)
    _git(other, "remote", "add", "origin", "git@github.com:someone/else.git")
    (projects / "notarepo").mkdir()

    found = await git_ops.find_local_repo([projects], "acme", "web")  # case-insensitive
    assert found == match
    assert await git_ops.find_local_repo([projects], "no", "match") is None
