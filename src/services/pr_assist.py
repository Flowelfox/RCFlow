"""Build seeded prompts for on-demand, read-only AI review assistance.

The PR-review feature is human-led; the agent only assists when asked. These
helpers turn a cached pull request plus its live diff into a prompt for a
read-only one-shot agent session (summarise the PR, or walk through one file).
The agent never mutates anything — the caller seeds deny-all permission rules.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from src.database.models import GitHubPR as GitHubPRModel
from src.services.github_service import GitHubService

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

# Cap diff/patch text fed to the model so a huge PR cannot blow the context.
MAX_DIFF_CHARS = 40_000

PR_ASSIST_KINDS = ("summary", "explain")


def _truncate(text: str, limit: int = MAX_DIFF_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n… [diff truncated — {len(text) - limit} more characters omitted]"


def _summary_prompt(pr: GitHubPRModel, diff: str) -> str:
    return (
        f"You are assisting a human reviewer of GitHub pull request "
        f'#{pr.number} "{pr.title}" in {pr.repo_owner}/{pr.repo_name} '
        f"(base `{pr.base_ref}` ← head `{pr.head_ref}`, author @{pr.author}).\n\n"
        f"Pull request description:\n{pr.body or '(none)'}\n\n"
        "Read the unified diff below and produce, concisely:\n"
        "1. A one-paragraph summary of the change's intent.\n"
        "2. A per-file walkthrough of what changed and why it matters.\n"
        "3. Risks, edge cases, or things the reviewer should look at closely.\n\n"
        "This is read-only review assistance — do NOT attempt to edit, run, or "
        "modify anything; just analyse the diff.\n\n"
        f"```diff\n{_truncate(diff)}\n```"
    )


def _explain_prompt(pr: GitHubPRModel, file_path: str, patch: str) -> str:
    return (
        f"You are assisting a human reviewer of GitHub pull request "
        f'#{pr.number} "{pr.title}" in {pr.repo_owner}/{pr.repo_name}.\n\n'
        f"Explain the changes to the single file `{file_path}`: what changed, "
        "why it likely changed, how it affects behaviour, and anything the "
        "reviewer should scrutinise. Be concise and specific.\n\n"
        "This is read-only review assistance — do NOT attempt to edit, run, or "
        "modify anything.\n\n"
        f"```diff\n{_truncate(patch)}\n```"
    )


async def build_pr_assist_prompt(
    *,
    settings: Any,
    db_factory: Callable[[], AsyncSession],
    pr_id: str,
    kind: str,
    file_path: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Build a read-only assist prompt for a cached PR.

    Returns ``(pr_info, prompt)`` where ``pr_info`` is a small dict describing
    the PR (for display/labels). Fetches the live diff/patch from GitHub.

    Raises:
        ValueError: unknown ``kind``, PR not found, missing token, or (for
            ``explain``) the file is not part of the PR / has no textual patch.
    """
    if kind not in PR_ASSIST_KINDS:
        raise ValueError(f"Unknown assist kind: {kind!r}")
    if not settings.GITHUB_TOKEN:
        raise ValueError("GitHub token is not configured.")

    try:
        pr_uuid = uuid.UUID(pr_id)
    except ValueError:
        raise ValueError("Invalid PR id") from None

    async with db_factory() as db:
        stmt = select(GitHubPRModel).where(
            GitHubPRModel.id == pr_uuid,
            GitHubPRModel.backend_id == settings.RCFLOW_BACKEND_ID,
        )
        pr = (await db.execute(stmt)).scalar_one_or_none()
    if pr is None:
        raise ValueError("Pull request not found")

    svc = GitHubService(token=settings.GITHUB_TOKEN)
    try:
        if kind == "summary":
            diff = await svc.get_pr_diff(pr.repo_owner, pr.repo_name, pr.number)
            prompt = _summary_prompt(pr, diff)
        else:  # explain
            if not file_path:
                raise ValueError("file_path is required for the explain assist")
            files = await svc.list_pr_files(pr.repo_owner, pr.repo_name, pr.number)
            match = next((f for f in files if f["filename"] == file_path), None)
            if match is None:
                raise ValueError(f"File not part of this PR: {file_path}")
            if not match.get("patch"):
                raise ValueError("That file has no textual diff to explain (binary or too large).")
            prompt = _explain_prompt(pr, file_path, match["patch"])
    finally:
        await svc.aclose()

    pr_info = {
        "id": str(pr.id),
        "number": pr.number,
        "repo_owner": pr.repo_owner,
        "repo_name": pr.repo_name,
        "title": pr.title,
        "kind": kind,
        "file_path": file_path,
    }
    return pr_info, prompt
