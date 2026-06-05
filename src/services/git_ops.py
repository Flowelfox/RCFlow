"""Local git operations for the PR-review author/push flow.

Thin async wrappers over the ``git`` CLI used by Phase 4b: inspect a worktree's
branch/remote, commit pending changes, and push a branch to GitHub authenticated
with the configured PAT.

Token handling: the PAT is **never** placed on the command line or in git config.
``push_branch`` supplies it through a temporary ``GIT_ASKPASS`` helper that reads
it from the environment, so it does not appear in argv, logs, or the repo's
stored remote. The temp helper is removed immediately after the push.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import stat
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# owner/repo from an https or ssh GitHub remote URL.
_GITHUB_REMOTE_RE = re.compile(r"github\.com[:/]+([^/]+)/(.+?)(?:\.git)?/?$")


class GitOpsError(Exception):
    """Raised when a local git operation fails."""


async def _run_git(
    args: list[str],
    cwd: str | Path,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Run ``git <args>`` in ``cwd``; return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    out, err = await proc.communicate()
    return proc.returncode or 0, out.decode(errors="replace").strip(), err.decode(errors="replace").strip()


async def parse_github_remote(repo_path: str | Path, remote: str = "origin") -> tuple[str, str] | None:
    """Return ``(owner, repo)`` parsed from ``remote``'s URL, or None."""
    code, url, _ = await _run_git(["remote", "get-url", remote], repo_path)
    if code != 0 or not url:
        return None
    match = _GITHUB_REMOTE_RE.search(url)
    if not match:
        return None
    return match.group(1), match.group(2)


async def find_local_repo(search_dirs: list[Path], owner: str, repo: str) -> Path | None:
    """Find the local checkout of ``owner/repo`` among ``search_dirs``' subdirs.

    Scans each search dir's immediate subdirectories, reads their ``origin``
    GitHub remote, and returns the first whose owner/repo matches (case-
    insensitive). Returns None when no local clone is found.
    """
    target = (owner.lower(), repo.lower())
    for base in search_dirs:
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if not (child / ".git").exists():
                continue
            parsed = await parse_github_remote(child)
            if parsed and (parsed[0].lower(), parsed[1].lower()) == target:
                return child
    return None


class MergeToolUnavailableError(GitOpsError):
    """Raised when ``git merge-tree --write-tree`` is unsupported (git < 2.38)."""


async def merge_conflict_files(
    repo_path: str | Path,
    base_ref: str,
    pr_number: int,
) -> list[str]:
    """Return the paths that conflict when merging a PR's head into its base.

    Performs a 3-way merge entirely in the object store via ``git merge-tree
    --write-tree`` — nothing is written to the working tree or any branch. Both
    sides are fetched first (adding objects only): the base branch from
    ``origin`` and the PR head via the ``pull/<n>/head`` ref (which works for
    fork PRs too, so no fork remote is needed).

    Returns an empty list when the merge is clean. Raises
    :class:`MergeToolUnavailableError` when the local git is too old for
    ``--write-tree`` (git < 2.38), and :class:`GitOpsError` on other failures.
    """
    # Fetch the base branch tip into FETCH_HEAD, then resolve its commit.
    code, _, err = await _run_git(["fetch", "origin", base_ref], repo_path)
    if code != 0:
        raise GitOpsError(f"git fetch base '{base_ref}' failed: {err}")
    code, base_sha, err = await _run_git(["rev-parse", "FETCH_HEAD"], repo_path)
    if code != 0 or not base_sha:
        raise GitOpsError(f"Could not resolve base ref '{base_ref}': {err}")

    # Fetch the PR head via the GitHub-provided pull/<n>/head ref.
    head_ref = f"pull/{pr_number}/head"
    code, _, err = await _run_git(["fetch", "origin", head_ref], repo_path)
    if code != 0:
        raise GitOpsError(f"git fetch head '{head_ref}' failed: {err}")
    code, head_sha, err = await _run_git(["rev-parse", "FETCH_HEAD"], repo_path)
    if code != 0 or not head_sha:
        raise GitOpsError(f"Could not resolve head ref '{head_ref}': {err}")

    code, out, err = await _run_git(
        ["merge-tree", "--write-tree", "--name-only", "--no-messages", base_sha, head_sha],
        repo_path,
    )
    if code == 0:
        return []  # clean merge, no conflicts
    if code != 1:
        # Old git lacks --write-tree and exits with usage/130 errors.
        if "write-tree" in err or "usage" in err.lower():
            raise MergeToolUnavailableError(f"git merge-tree --write-tree unsupported: {err}")
        raise GitOpsError(f"git merge-tree failed: {err}")
    # Exit 1 = conflicts. Output is the tree OID on line 1, then the conflicting
    # paths (from --name-only) up to the first blank line.
    lines = out.splitlines()
    files: list[str] = []
    for line in lines[1:]:
        if not line.strip():
            break
        files.append(line.strip())
    return files


async def current_branch(repo_path: str | Path) -> str:
    """Return the current branch name. Raises if detached / not a repo."""
    code, name, err = await _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path)
    if code != 0 or not name or name == "HEAD":
        raise GitOpsError(f"Could not determine current branch: {err or name}")
    return name


async def has_uncommitted_changes(repo_path: str | Path) -> bool:
    """Return True if the worktree has staged or unstaged changes."""
    code, out, err = await _run_git(["status", "--porcelain"], repo_path)
    if code != 0:
        raise GitOpsError(f"git status failed: {err}")
    return bool(out.strip())


async def commit_all(repo_path: str | Path, message: str) -> bool:
    """Stage and commit all changes. Returns False when there was nothing to commit."""
    if not await has_uncommitted_changes(repo_path):
        return False
    code, _, err = await _run_git(["add", "-A"], repo_path)
    if code != 0:
        raise GitOpsError(f"git add failed: {err}")
    code, _, err = await _run_git(["commit", "-m", message], repo_path)
    if code != 0:
        raise GitOpsError(f"git commit failed: {err}")
    return True


async def push_branch(
    repo_path: str | Path,
    token: str,
    *,
    branch: str | None = None,
    owner_repo: tuple[str, str] | None = None,
    remote_url: str | None = None,
) -> str:
    """Push ``branch`` (default: current) to GitHub over an authenticated HTTPS URL.

    The token is passed via a temporary ``GIT_ASKPASS`` helper (read from the
    environment), never via argv or git config. Returns the pushed branch name.
    ``remote_url`` overrides the derived GitHub URL (used by tests against a
    local remote).

    Raises:
        GitOpsError: if the remote can't be resolved or the push fails.
    """
    branch = branch or await current_branch(repo_path)
    if remote_url is not None:
        url = remote_url
    else:
        owner_repo = owner_repo or await parse_github_remote(repo_path)
        if owner_repo is None:
            raise GitOpsError("No GitHub 'origin' remote found for this worktree.")
        owner, repo = owner_repo
        # Username in the URL is a placeholder; the PAT is the password supplied
        # by the askpass helper, so the secret never touches argv or the remote
        # config.
        url = f"https://x-access-token@github.com/{owner}/{repo}.git"

    askpass_dir = tempfile.mkdtemp(prefix="rcflow-gitpush-")
    askpass_path = Path(askpass_dir) / "askpass.sh"
    askpass_path.write_text('#!/bin/sh\nexec printf "%s" "$RCFLOW_GIT_TOKEN"\n')
    askpass_path.chmod(stat.S_IRWXU)  # 0700 — owner only

    env = {
        **os.environ,
        "GIT_ASKPASS": str(askpass_path),
        "RCFLOW_GIT_TOKEN": token,
        "GIT_TERMINAL_PROMPT": "0",
    }
    try:
        code, _, err = await _run_git(["push", url, f"HEAD:refs/heads/{branch}"], repo_path, env=env)
    finally:
        # Remove the helper + token-bearing file promptly.
        try:
            askpass_path.unlink(missing_ok=True)
            Path(askpass_dir).rmdir()
        except OSError:
            pass

    if code != 0:
        # ``err`` may name the repo URL (which carries no secret) but never the token.
        raise GitOpsError(f"git push failed: {err}")
    return branch
