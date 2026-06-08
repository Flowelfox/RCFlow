"""Build seeded prompts for on-demand PR-review AI assistance.

The PR-review feature is human-led; the agent only assists when asked. These
helpers turn a cached pull request into a seeded prompt for a one-shot agent
session:
- ``summary`` / ``explain`` — read-only analysis of the live diff (the caller
  seeds deny-all permission rules; the agent never mutates anything).
- ``fix`` — writable: the prompt asks the agent to address a review comment by
  editing the worktree the human selected; the agent is told NOT to push or
  open a PR (the human does that via the ``open-pr`` flow).
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

# summary/explain are read-only (analyse the diff). review/fix/resolve_conflicts
# are writable: they run a full-perms agent session in a local checkout. review
# inspects the code, presents a report, and (only on the human's approval) posts
# the review on GitHub via the gh CLI.
PR_ASSIST_KINDS = ("summary", "explain", "review", "fix", "resolve_conflicts")
READ_ONLY_KINDS = ("summary", "explain")
# Kinds that fetch the PR diff from GitHub to seed the prompt (need a token).
DIFF_KINDS = ("summary", "explain", "review")


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


def _review_prompt(pr: GitHubPRModel, diff: str) -> str:
    return (
        f"You are an AI code reviewer for GitHub pull request "
        f'#{pr.number} "{pr.title}" in {pr.repo_owner}/{pr.repo_name} '
        f"(base `{pr.base_ref}` ← head `{pr.head_ref}`, author @{pr.author}). You "
        "are working in a local checkout of this repository.\n\n"
        f"Pull request description:\n{pr.body or '(none)'}\n\n"
        "Review the change (the unified diff is below; you MAY read files in the "
        "checkout for context) and produce a **readable, mid-sized Markdown "
        "report** — thorough but not exhausting, easy for a human to skim:\n\n"
        "1. A short **summary** of the change and whether it meets its goal.\n"
        "2. A **findings table** with these columns:\n"
        "   `| Severity | Location | Issue | Recommended action |`\n"
        "   - **Severity**: Critical / High / Medium / Low / Nit.\n"
        "   - **Location**: `file:Lstart-Lend` (or `general`).\n"
        "   - **Issue**: one concise sentence.\n"
        "   - **Recommended action**: the concrete reviewer action, e.g. "
        '`Inline comment on src/foo.py:42-45: "<suggested comment text>"` or '
        '`Include in the global comment: "<text>"`.\n'
        "3. **Recommended review action** — exactly one of **Approve**, "
        "**Comment**, or **Request changes**, with a one-line rationale.\n\n"
        "Present the full report to the user and DO NOT take any GitHub action "
        "yet. Then explicitly ASK: \"Apply the recommended review actions on "
        "GitHub?\" and wait for a clear yes/no.\n\n"
        "⚠️ Warning to include in the report: any GitHub actions (inline "
        "comments, the global comment, submitting the review) are performed with "
        "the configured GitHub account — they will appear authored by **you**, "
        "the user.\n\n"
        "Only if the user approves: apply the recommended actions with the `gh` "
        "CLI — post the inline comments and the global comment, then submit the "
        "review with the recommended verdict. If the user declines, do nothing.\n\n"
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


def _fix_prompt(pr: GitHubPRModel, file_path: str | None, line: int | None, comment: str) -> str:
    loc = f" around `{file_path}`" + (f":{line}" if line else "") if file_path else ""
    return (
        f"You are helping address a review comment on GitHub pull request "
        f'#{pr.number} "{pr.title}" ({pr.repo_owner}/{pr.repo_name}). You are '
        "working in a local checkout of that branch.\n\n"
        f'Review comment{loc}:\n"""\n{comment}\n"""\n\n'
        "Read the relevant file(s) in the working directory, make the change "
        "that addresses this comment, and keep the edit minimal and focused. Do "
        "NOT push or open a pull request — the human reviews your change and "
        "pushes it. Explain briefly what you changed and why."
    )


def _resolve_conflicts_prompt(pr: GitHubPRModel, conflict_files: list[str]) -> str:
    files_hint = ""
    if conflict_files:
        listed = "\n".join(f"- {f}" for f in conflict_files)
        files_hint = f"\nA pre-check found these conflicting files:\n{listed}\n"
    return (
        f"You are resolving the merge conflicts on GitHub pull request "
        f'#{pr.number} "{pr.title}" ({pr.repo_owner}/{pr.repo_name}). The PR '
        f"merges head `{pr.head_ref}` into base `{pr.base_ref}`, and the two "
        "branches currently conflict.\n\n"
        "You are working in a local checkout of this repository. Resolve the "
        "conflicts so the PR can merge cleanly:\n"
        f"1. Make sure the PR head branch `{pr.head_ref}` is checked out "
        "(fetch/track it from `origin` if it isn't already).\n"
        f"2. Fetch the latest base and merge it into the head branch: "
        f"`git fetch origin {pr.base_ref}` then `git merge origin/{pr.base_ref}`. "
        "Conflicts will appear.\n"
        "3. For EACH conflicting file, open it, understand what BOTH sides "
        "intended, and produce a correct merged result that preserves both "
        "changes' intent — never blindly discard one side. Remove all conflict "
        "markers (`<<<<<<<`, `=======`, `>>>>>>>`).\n"
        "4. Where feasible, sanity-check the result (build/lint/tests) so you "
        "don't leave the tree broken.\n"
        f"{files_hint}\n"
        "Then STOP and present a clear REPORT before changing history:\n"
        "- The files that conflicted.\n"
        "- For EACH file: what conflicted, HOW you resolved it, and WHY you "
        "chose that resolution (the reasoning for the merged result).\n"
        "- Anything uncertain the human should double-check.\n\n"
        "After showing the report, explicitly ASK the human for permission to "
        "commit and push the resolution. Do NOT commit or push until they "
        "approve. Only once they say yes, commit the merge and push the head "
        f"branch `{pr.head_ref}` to `origin`. If they decline or ask for "
        "changes, leave the working tree as-is and wait."
    )


async def build_pr_assist_prompt(
    *,
    settings: Any,
    db_factory: Callable[[], AsyncSession],
    pr_id: str,
    kind: str,
    file_path: str | None = None,
    line: int | None = None,
    comment_body: str | None = None,
) -> tuple[dict[str, Any], str]:
    """Build an assist prompt for a cached PR.

    ``summary``/``explain`` are read-only and fetch the live diff/patch from
    GitHub. ``fix`` is writable: it builds a prompt from the review ``comment_body``
    and lets the agent read/edit the selected worktree itself (no GitHub fetch).

    Returns ``(pr_info, prompt)``.

    Raises:
        ValueError: unknown ``kind``, PR not found, missing token (read-only
            kinds), missing ``comment_body`` (fix), or (``explain``) the file is
            not part of the PR / has no textual patch.
    """
    if kind not in PR_ASSIST_KINDS:
        raise ValueError(f"Unknown assist kind: {kind!r}")
    if kind in DIFF_KINDS and not settings.GITHUB_TOKEN:
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

    if kind == "fix":
        if not comment_body:
            raise ValueError("comment_body is required for the fix assist")
        prompt = _fix_prompt(pr, file_path, line, comment_body)
    elif kind == "resolve_conflicts":
        # comment_body optionally carries the conflicting file list (newline- or
        # comma-separated) as a hint; the agent re-discovers them via the merge.
        raw = comment_body or ""
        conflict_files = [f.strip() for f in raw.replace(",", "\n").splitlines() if f.strip()]
        prompt = _resolve_conflicts_prompt(pr, conflict_files)
    else:
        svc = GitHubService(token=settings.GITHUB_TOKEN)
        try:
            if kind == "summary":
                diff = await svc.get_pr_diff(pr.repo_owner, pr.repo_name, pr.number)
                prompt = _summary_prompt(pr, diff)
            elif kind == "review":
                diff = await svc.get_pr_diff(pr.repo_owner, pr.repo_name, pr.number)
                prompt = _review_prompt(pr, diff)
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
