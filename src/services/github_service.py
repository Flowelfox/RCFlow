"""GitHub REST + GraphQL API client.

Wraps the GitHub API using httpx.  All methods are async.  Authentication uses
a Personal Access Token (PAT) passed as the ``Authorization`` header.

The PR-review feature reads simple resources (pull-request lists, files, diffs)
and performs all writes (review comments, review submission, merge) over the
**REST v3** API, and reads review threads — the one place where resolved-state
and line anchoring are coherent — over the **GraphQL v4** API.  This module is
the single entry point for both; Phase 0 ships the transport + auth check only,
later phases add the PR/review methods on top of :meth:`_rest` / :meth:`_gql`.

Usage::

    async with GitHubService(token="ghp_...") as gh:
        user = await gh.test_token()  # -> {"login": "...", ...}
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import and_, func, or_, select

from src.database.models import GitHubPR as GitHubPRModel
from src.services import git_ops

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

GITHUB_API_URL = "https://api.github.com"
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
# Pin the REST media type + API version so responses are stable across GitHub's
# rolling API changes (https://docs.github.com/en/rest/about-the-rest-api/api-versions).
GITHUB_API_VERSION = "2022-11-28"

# Listing buckets — map to GitHub search qualifiers.
PR_ROLE_QUALIFIERS: dict[str, str] = {
    "for_me": "review-requested:@me",
    "created": "author:@me",
    # "all" applies no @me qualifier — every PR regardless of the current user.
    # It MUST be repo-scoped (see list_pull_requests) so the search stays bounded.
    "all": "",
}

# Classic-PAT scopes the PR-review feature needs, in display order. ``alt`` is a
# weaker scope that also satisfies the requirement (e.g. public-only access).
REQUIRED_SCOPES: list[dict[str, Any]] = [
    {
        "scope": "repo",
        "alt": "public_repo",
        "required": True,
        "description": "Read/write pull requests, merge, create, and push (use public_repo for public repos only)",
    },
    {
        "scope": "read:org",
        "alt": None,
        "required": False,
        "description": "Filter pull requests by review-requested and access org repositories",
    },
]


def evaluate_scopes(granted: list[str]) -> list[dict[str, Any]]:
    """Mark each required scope satisfied/unsatisfied against ``granted``."""
    granted_set = set(granted)
    result: list[dict[str, Any]] = []
    for spec in REQUIRED_SCOPES:
        ok = spec["scope"] in granted_set or (spec["alt"] is not None and spec["alt"] in granted_set)
        result.append(
            {
                "scope": spec["scope"],
                "description": spec["description"],
                "required": spec["required"],
                "satisfied": ok,
            }
        )
    return result


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)


def _parse_pull(pr: dict[str, Any]) -> dict[str, Any]:
    """Normalise a REST pull-request object to a github_prs-ready dict.

    ``role`` is left unset — the caller stamps the listing bucket.  ``additions``
    / ``deletions`` / ``changed_files`` are only present on the detail endpoint
    (``GET .../pulls/{n}``), not on search items.
    """
    base = pr.get("base") or {}
    head = pr.get("head") or {}
    base_repo = base.get("repo") or {}
    user = pr.get("user") or {}
    merged = bool(pr.get("merged") or pr.get("merged_at"))

    return {
        "github_id": pr["node_id"],
        "repo_owner": (base_repo.get("owner") or {}).get("login", ""),
        "repo_name": base_repo.get("name", ""),
        "number": pr["number"],
        "title": pr.get("title", ""),
        "body": pr.get("body"),
        "state": "merged" if merged else pr.get("state", "open"),
        "draft": bool(pr.get("draft", False)),
        "author": user.get("login", ""),
        "author_avatar_url": user.get("avatar_url"),
        "url": pr.get("html_url", ""),
        "base_ref": base.get("ref", ""),
        "head_ref": head.get("ref", ""),
        "head_sha": head.get("sha", ""),
        # True when the PR's repository is archived (read-only) — such PRs can't
        # be reviewed/merged, so the sync filters them out.
        "archived": bool(base_repo.get("archived", False)),
        # reviewDecision / mergeable come from GraphQL (not REST); the sync
        # enriches them via get_pr_status. Defaulted here so every parsed dict
        # carries the keys (callers that don't enrich just get None).
        "review_decision": None,
        "merge_status": None,
        # Filled by the sync from a memoized local-repo lookup (per worker).
        "project_name": None,
        "project_path": None,
        "additions": pr.get("additions", 0),
        "deletions": pr.get("deletions", 0),
        "changed_files": pr.get("changed_files", 0),
        # Only present on the detail endpoint; null while GitHub computes mergeability.
        "mergeable": pr.get("mergeable"),
        "mergeable_state": pr.get("mergeable_state"),
        "created_at": _parse_dt(pr.get("created_at")),
        "updated_at": _parse_dt(pr.get("updated_at")),
    }


def _parse_file(f: dict[str, Any]) -> dict[str, Any]:
    """Normalise a REST PR-file object.

    ``patch`` IS a unified diff (absent for binary or oversized files).
    """
    return {
        "filename": f.get("filename", ""),
        "previous_filename": f.get("previous_filename"),
        "status": f.get("status", ""),  # added|modified|removed|renamed|...
        "additions": f.get("additions", 0),
        "deletions": f.get("deletions", 0),
        "changes": f.get("changes", 0),
        "patch": f.get("patch"),
        "sha": f.get("sha", ""),
        "blob_url": f.get("blob_url"),
    }


_THREADS_QUERY = """
query Threads($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          diffSide
          comments(first: 50) {
            nodes {
              id
              databaseId
              author { login }
              body
              createdAt
            }
          }
        }
      }
    }
  }
}
"""

_PR_STATUS_QUERY = """
query PrStatus($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewDecision
      mergeable
    }
  }
}
"""

_RESOLVE_THREAD_MUTATION = """
mutation Resolve($threadId: ID!) {
  resolveReviewThread(input: { threadId: $threadId }) {
    thread { id isResolved }
  }
}
"""

_UNRESOLVE_THREAD_MUTATION = """
mutation Unresolve($threadId: ID!) {
  unresolveReviewThread(input: { threadId: $threadId }) {
    thread { id isResolved }
  }
}
"""


def _parse_thread(node: dict[str, Any]) -> dict[str, Any]:
    """Normalise a GraphQL reviewThread node.

    ``thread_id`` is the GraphQL node id (needed to resolve/unresolve); each
    comment carries both its node ``id`` and REST ``database_id`` because
    replies go through REST (databaseId) while thread resolution is GraphQL.
    """
    comments = (node.get("comments") or {}).get("nodes", [])
    return {
        "thread_id": node["id"],
        "is_resolved": bool(node.get("isResolved")),
        "is_outdated": bool(node.get("isOutdated")),
        "path": node.get("path"),
        "line": node.get("line"),
        "side": node.get("diffSide"),  # LEFT|RIGHT
        "comments": [
            {
                "id": c["id"],
                "database_id": c.get("databaseId"),
                "author": (c.get("author") or {}).get("login", ""),
                "body": c.get("body", ""),
                "created_at": c.get("createdAt"),
            }
            for c in comments
        ],
    }


def _parse_search_item(item: dict[str, Any], role: str, state: str) -> dict[str, Any]:
    """Light PR row from a /search/issues item — no per-PR detail/status fetch.

    Used for merged/closed listings (fetched on demand), which only need the
    basics for the list; additions/deletions/base/head and review/mergeable
    status are left empty (irrelevant for done PRs), keeping the refresh fast.
    """
    user = item.get("user") or {}
    owner, name, _ = _repo_ref_from_search_item(item)
    return {
        "github_id": item.get("node_id", ""),
        "repo_owner": owner,
        "repo_name": name,
        "number": item["number"],
        "title": item.get("title", ""),
        "body": item.get("body"),
        "state": state,  # we queried a single state
        "draft": bool(item.get("draft", False)),
        "author": user.get("login", ""),
        "author_avatar_url": user.get("avatar_url"),
        "url": item.get("html_url", ""),
        "base_ref": "",
        "head_ref": "",
        "head_sha": "",
        "archived": False,
        "review_decision": None,
        "merge_status": None,
        "project_name": None,
        "project_path": None,
        "additions": 0,
        "deletions": 0,
        "changed_files": 0,
        "mergeable": None,
        "mergeable_state": None,
        "created_at": _parse_dt(item.get("created_at")),
        "updated_at": _parse_dt(item.get("updated_at")),
        "role": role,
    }


def _repo_ref_from_search_item(item: dict[str, Any]) -> tuple[str, str, int]:
    """Extract (owner, repo, number) from a /search/issues PR item.

    ``repository_url`` looks like ``https://api.github.com/repos/<owner>/<name>``.
    """
    repo_url = item.get("repository_url", "")
    owner, name = "", ""
    marker = "/repos/"
    if marker in repo_url:
        tail = repo_url.split(marker, 1)[1]
        parts = tail.split("/")
        if len(parts) >= 2:
            owner, name = parts[0], parts[1]
    return owner, name, int(item["number"])


class GitHubServiceError(Exception):
    """Raised when the GitHub API returns an error response."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubService:
    """Async client for the GitHub REST + GraphQL APIs.

    Can be used as an async context manager or standalone (call :meth:`aclose`
    when done).  Authentication is a Personal Access Token; the same token is
    used for both the REST and GraphQL endpoints.
    """

    def __init__(self, token: str) -> None:
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
            },
            timeout=30.0,
        )

    async def _rest(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a REST request and return the decoded JSON body.

        ``path`` is either an absolute URL or a path relative to
        :data:`GITHUB_API_URL` (e.g. ``"/user"`` or ``"/repos/o/r/pulls"``).
        Raises :class:`GitHubServiceError` on transport or HTTP-status errors.
        """
        url = path if path.startswith("http") else f"{GITHUB_API_URL}{path}"
        try:
            resp = await self._client.request(method, url, params=params, json=json)
        except httpx.TimeoutException as exc:
            raise GitHubServiceError("GitHub API request timed out") from exc
        except httpx.RequestError as exc:
            raise GitHubServiceError(f"GitHub API request failed: {exc}") from exc

        self._raise_for_status(resp)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    async def _gql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a GraphQL request and return the ``data`` payload."""
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            resp = await self._client.post(GITHUB_GRAPHQL_URL, json=payload)
        except httpx.TimeoutException as exc:
            raise GitHubServiceError("GitHub API request timed out") from exc
        except httpx.RequestError as exc:
            raise GitHubServiceError(f"GitHub API request failed: {exc}") from exc

        self._raise_for_status(resp)
        body = resp.json()
        if body.get("errors"):
            msgs = "; ".join(e.get("message", "unknown") for e in body["errors"])
            raise GitHubServiceError(f"GitHub GraphQL error: {msgs}")
        return body.get("data", {})

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        """Translate non-2xx GitHub responses into :class:`GitHubServiceError`."""
        if resp.status_code == 401:
            raise GitHubServiceError("GitHub token is invalid or expired", status_code=401)
        if resp.status_code == 403:
            # 403 doubles as the rate-limit signal on GitHub.
            if resp.headers.get("X-RateLimit-Remaining") == "0":
                raise GitHubServiceError("GitHub API rate limit exceeded", status_code=403)
            raise GitHubServiceError("GitHub API access forbidden (check token scopes)", status_code=403)
        if resp.status_code >= 400:
            raise GitHubServiceError(f"GitHub API returned HTTP {resp.status_code}", status_code=resp.status_code)

    async def test_token(self) -> dict[str, Any]:
        """Verify the token and return the authenticated user.

        Returns the ``GET /user`` payload (``login``, ``id``, ``name``, …).
        Raises :class:`GitHubServiceError` if the token is missing scopes or
        invalid — used by the integration ``status`` endpoint as a preflight.
        """
        user = await self._rest("GET", "/user")
        logger.info("GitHub token validated for user %s", user.get("login"))
        return user

    async def token_info(self) -> dict[str, Any]:
        """Verify the token and report its login + granted classic-PAT scopes.

        Classic PATs return their granted scopes in the ``X-OAuth-Scopes``
        response header. Fine-grained PATs do not expose that header (their
        access is repository permissions, not scopes), so ``fine_grained`` is
        True and ``scopes`` is empty — the caller validates them functionally.
        """
        try:
            resp = await self._client.get(f"{GITHUB_API_URL}/user")
        except httpx.TimeoutException as exc:
            raise GitHubServiceError("GitHub API request timed out") from exc
        except httpx.RequestError as exc:
            raise GitHubServiceError(f"GitHub API request failed: {exc}") from exc
        self._raise_for_status(resp)

        raw = resp.headers.get("X-OAuth-Scopes")
        fine_grained = raw is None
        scopes = [s.strip() for s in (raw or "").split(",") if s.strip()]
        user = resp.json()
        return {"login": user.get("login"), "scopes": scopes, "fine_grained": fine_grained}

    async def list_pull_requests(self, role: str, repo: str | None = None, state: str = "open") -> list[dict[str, Any]]:
        """List pull requests for a listing bucket (most-recently-updated first).

        ``role`` is ``"for_me"`` (review-requested) or ``"created"`` (authored).
        ``state`` is ``open`` (default), ``closed`` or ``merged`` — the default
        sync fetches only open PRs (cheap); merged/closed are fetched on demand
        when the client filters for them. Each PR's detail fills additions/
        deletions/base/head; the GraphQL review/mergeable status is fetched only
        for **open** PRs (it's meaningless for closed/merged), keeping the common
        refresh fast. Optionally scoped to a single ``owner/name`` ``repo``.
        """
        qualifier = PR_ROLE_QUALIFIERS.get(role)
        if qualifier is None:
            raise GitHubServiceError(f"Unknown PR role: {role!r}")
        if state not in ("open", "closed", "merged"):
            raise GitHubServiceError(f"Unknown PR state: {state!r}")
        # The "all" bucket has no @me qualifier, so without a repo it would search
        # every PR on GitHub — require a repo scope to keep it bounded.
        if not qualifier and not repo:
            raise GitHubServiceError("the 'all' PR role requires a repo scope")
        query = f"is:pr is:{state} {qualifier}".strip()
        if repo:
            query += f" repo:{repo}"

        data = await self._rest(
            "GET", "/search/issues", params={"q": query, "sort": "updated", "order": "desc", "per_page": 50}
        )
        prs: list[dict[str, Any]] = []
        for item in data.get("items", []):
            owner, name, number = _repo_ref_from_search_item(item)
            if not owner or not name:
                continue
            if state != "open":
                # Merged/closed: list lightly (no per-PR detail/status) so the
                # on-demand fetch stays cheap.
                prs.append(_parse_search_item(item, role, state))
                continue
            full = await self.get_pull_request(owner, name, number)
            full["role"] = role
            full.update(await self.get_pr_status(owner, name, number))
            prs.append(full)
        logger.info("Fetched %d GitHub PRs for role=%s state=%s", len(prs), role, state)
        return prs

    async def get_pull_request(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        """Fetch a single pull request's full detail (normalised)."""
        pr = await self._rest("GET", f"/repos/{owner}/{repo}/pulls/{number}")
        return _parse_pull(pr)

    async def get_pr_status(self, owner: str, repo: str, number: int) -> dict[str, Any]:
        """Fetch a PR's review decision + mergeability via GraphQL.

        Returns ``{"review_decision": str|None, "merge_status": str|None}`` where
        ``review_decision`` is APPROVED / CHANGES_REQUESTED / REVIEW_REQUIRED and
        ``merge_status`` is MERGEABLE / CONFLICTING / UNKNOWN (GitHub computes
        the latter asynchronously, so UNKNOWN is common right after a push).
        """
        data = await self._gql(_PR_STATUS_QUERY, {"owner": owner, "repo": repo, "number": number})
        pr = ((data.get("repository") or {}).get("pullRequest")) or {}
        return {"review_decision": pr.get("reviewDecision"), "merge_status": pr.get("mergeable")}

    async def list_pr_files(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        """List the changed files of a pull request (paginated).

        Each entry's ``patch`` is the file's unified diff (absent for binary or
        oversized files), which feeds the client diff viewer directly.
        """
        files: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = await self._rest(
                "GET", f"/repos/{owner}/{repo}/pulls/{number}/files", params={"per_page": 100, "page": page}
            )
            if not batch:
                break
            files.extend(_parse_file(f) for f in batch)
            if len(batch) < 100:
                break
            page += 1
        return files

    async def get_pr_diff(self, owner: str, repo: str, number: int) -> str:
        """Fetch a pull request's whole-PR unified diff as raw text.

        Uses the ``application/vnd.github.diff`` media type rather than JSON.
        """
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls/{number}"
        try:
            resp = await self._client.get(url, headers={"Accept": "application/vnd.github.diff"})
        except httpx.TimeoutException as exc:
            raise GitHubServiceError("GitHub API request timed out") from exc
        except httpx.RequestError as exc:
            raise GitHubServiceError(f"GitHub API request failed: {exc}") from exc
        self._raise_for_status(resp)
        return resp.text

    # ------------------------------------------------------------------
    # Review threads (read = GraphQL, write = REST)
    # ------------------------------------------------------------------

    async def list_review_threads(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        """List a pull request's review threads (inline comment conversations).

        Read via GraphQL — the only API where resolved-state and line anchoring
        are coherent. Returns normalised thread dicts (see :func:`_parse_thread`).
        """
        data = await self._gql(_THREADS_QUERY, {"owner": owner, "repo": repo, "number": number})
        pr = ((data.get("repository") or {}).get("pullRequest")) or {}
        nodes = (pr.get("reviewThreads") or {}).get("nodes", [])
        return [_parse_thread(n) for n in nodes]

    async def list_issue_comments(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        """List a PR's general (issue-level) conversation comments.

        These are the "Conversation" tab comments not anchored to a diff line
        (``GET /repos/{o}/{r}/issues/{n}/comments``), paginated. Returns
        normalised dicts ``{id, author, author_avatar_url, body, created_at, url}``.
        """
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = await self._rest(
                "GET",
                f"/repos/{owner}/{repo}/issues/{number}/comments",
                params={"per_page": 100, "page": page},
            )
            if not batch:
                break
            for c in batch:
                user = c.get("user") or {}
                out.append(
                    {
                        "id": c.get("id"),
                        "author": user.get("login", ""),
                        "author_avatar_url": user.get("avatar_url"),
                        "body": c.get("body", ""),
                        "created_at": c.get("created_at"),
                        "url": c.get("html_url", ""),
                    }
                )
            if len(batch) < 100:
                break
            page += 1
        return out

    async def list_reviews(self, owner: str, repo: str, number: int) -> list[dict[str, Any]]:
        """List a PR's submitted reviews (verdict + summary body).

        ``GET /repos/{o}/{r}/pulls/{n}/reviews``. Returns normalised dicts
        ``{id, author, author_avatar_url, state, body, created_at, url}`` where
        ``state`` is APPROVED / CHANGES_REQUESTED / COMMENTED / DISMISSED.
        """
        data = await self._rest("GET", f"/repos/{owner}/{repo}/pulls/{number}/reviews")
        out: list[dict[str, Any]] = []
        for r in data or []:
            user = r.get("user") or {}
            out.append(
                {
                    "id": r.get("id"),
                    "author": user.get("login", ""),
                    "author_avatar_url": user.get("avatar_url"),
                    "state": r.get("state", ""),
                    "body": r.get("body") or "",
                    "created_at": r.get("submitted_at"),
                    "url": r.get("html_url", ""),
                }
            )
        return out

    async def create_issue_comment(self, owner: str, repo: str, number: int, body: str) -> dict[str, Any]:
        """Post a general (issue-level) comment on a PR's conversation.

        ``POST /repos/{o}/{r}/issues/{n}/comments``. Returns the created comment
        normalised like :meth:`list_issue_comments` entries.
        """
        c = await self._rest("POST", f"/repos/{owner}/{repo}/issues/{number}/comments", json={"body": body})
        user = c.get("user") or {}
        return {
            "id": c.get("id"),
            "author": user.get("login", ""),
            "author_avatar_url": user.get("avatar_url"),
            "body": c.get("body", ""),
            "created_at": c.get("created_at"),
            "url": c.get("html_url", ""),
        }

    async def create_review(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        event: str,
        body: str = "",
        comments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Submit a pull-request review.

        ``event`` is ``APPROVE``, ``REQUEST_CHANGES`` or ``COMMENT``. ``comments``
        are inline comments, each ``{path, line, side, body}`` (``side`` is
        ``LEFT``/``RIGHT``); they post against the PR's latest commit.
        """
        if event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
            raise GitHubServiceError(f"Invalid review event: {event!r}")
        payload: dict[str, Any] = {"event": event}
        if body:
            payload["body"] = body
        if comments:
            payload["comments"] = comments
        return await self._rest("POST", f"/repos/{owner}/{repo}/pulls/{number}/reviews", json=payload)

    async def reply_review_comment(
        self, owner: str, repo: str, number: int, comment_id: int, body: str
    ) -> dict[str, Any]:
        """Reply to an existing review-thread comment (by its REST database id)."""
        return await self._rest(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{number}/comments/{comment_id}/replies",
            json={"body": body},
        )

    async def delete_review_comment(self, owner: str, repo: str, comment_id: int) -> None:
        """Delete a pull-request review comment (only the author's own).

        ``comment_id`` is the REST ``database_id``. Raises
        :class:`GitHubServiceError` (403) if the token's user is not the author.
        """
        await self._rest("DELETE", f"/repos/{owner}/{repo}/pulls/comments/{comment_id}")

    async def resolve_thread(self, thread_id: str, *, resolved: bool = True) -> dict[str, Any]:
        """Resolve or unresolve a review thread (by its GraphQL node id)."""
        mutation = _RESOLVE_THREAD_MUTATION if resolved else _UNRESOLVE_THREAD_MUTATION
        return await self._gql(mutation, {"threadId": thread_id})

    async def merge_pull_request(
        self,
        owner: str,
        repo: str,
        number: int,
        *,
        method: str = "squash",
        commit_title: str | None = None,
        commit_message: str | None = None,
    ) -> dict[str, Any]:
        """Merge a pull request.

        ``method`` is ``merge``, ``squash`` or ``rebase``. Raises
        :class:`GitHubServiceError` (405) if the PR is not mergeable.
        """
        if method not in ("merge", "squash", "rebase"):
            raise GitHubServiceError(f"Invalid merge method: {method!r}")
        payload: dict[str, Any] = {"merge_method": method}
        if commit_title:
            payload["commit_title"] = commit_title
        if commit_message:
            payload["commit_message"] = commit_message
        return await self._rest("PUT", f"/repos/{owner}/{repo}/pulls/{number}/merge", json=payload)

    async def get_file_content(self, owner: str, repo: str, path: str, ref: str) -> str:
        """Fetch a file's full text at a ref (sha or branch) as raw content.

        Used to expand diff context beyond the patch hunks. Raises
        :class:`GitHubServiceError` (404) if the file does not exist at ``ref``.
        """
        url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/contents/{path}"
        try:
            resp = await self._client.get(url, params={"ref": ref}, headers={"Accept": "application/vnd.github.raw"})
        except httpx.TimeoutException as exc:
            raise GitHubServiceError("GitHub API request timed out") from exc
        except httpx.RequestError as exc:
            raise GitHubServiceError(f"GitHub API request failed: {exc}") from exc
        self._raise_for_status(resp)
        return resp.text

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
    ) -> dict[str, Any]:
        """Open a pull request and return the normalised PR dict.

        ``head`` is the branch with changes (``user:branch`` for a fork);
        ``base`` is the target branch. The backend opens the PR so the agent
        never needs the token.
        """
        payload: dict[str, Any] = {"title": title, "head": head, "base": base, "draft": draft}
        if body:
            payload["body"] = body
        created = await self._rest("POST", f"/repos/{owner}/{repo}/pulls", json=payload)
        return _parse_pull(created)

    async def aclose(self) -> None:
        """Close the underlying async client."""
        await self._client.aclose()

    async def __aenter__(self) -> GitHubService:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


# ---------------------------------------------------------------------------
# PR sync pipeline (domain logic — moved out of the api route module so core
# and the routes import it downward from services).
# ---------------------------------------------------------------------------


def serialize_pr(pr: GitHubPRModel) -> dict[str, Any]:
    """Serialise a GitHubPR ORM row to a JSON-safe dict."""
    return {
        "id": str(pr.id),
        "github_id": pr.github_id,
        "repo_owner": pr.repo_owner,
        "repo_name": pr.repo_name,
        "number": pr.number,
        "title": pr.title,
        "body": pr.body,
        "state": pr.state,
        "draft": pr.draft,
        "review_decision": pr.review_decision,
        "merge_status": pr.merge_status,
        "project_name": pr.project_name,
        "project_path": pr.project_path,
        "author": pr.author,
        "author_avatar_url": pr.author_avatar_url,
        "url": pr.url,
        "base_ref": pr.base_ref,
        "head_ref": pr.head_ref,
        "head_sha": pr.head_sha,
        "additions": pr.additions,
        "deletions": pr.deletions,
        "changed_files": pr.changed_files,
        "role": pr.role,
        "created_at": pr.created_at.isoformat(),
        "updated_at": pr.updated_at.isoformat(),
        "synced_at": pr.synced_at.isoformat(),
        "task_id": str(pr.task_id) if pr.task_id else None,
    }


_PR_SYNC_FIELDS = (
    "repo_owner",
    "repo_name",
    "number",
    "title",
    "body",
    "state",
    "draft",
    "review_decision",
    "merge_status",
    "project_name",
    "project_path",
    "author",
    "author_avatar_url",
    "url",
    "base_ref",
    "head_ref",
    "head_sha",
    "additions",
    "deletions",
    "changed_files",
    "role",
    "created_at",
    "updated_at",
)


async def upsert_prs(db: AsyncSession, backend_id: str, parsed_prs: list[dict[str, Any]]) -> list[GitHubPRModel]:
    """Insert or update cached pull requests. Returns the upserted rows."""
    results: list[GitHubPRModel] = []
    now = datetime.now(UTC)

    for data in parsed_prs:
        stmt = select(GitHubPRModel).where(
            GitHubPRModel.backend_id == backend_id,
            GitHubPRModel.github_id == data["github_id"],
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()

        if existing:
            for field in _PR_SYNC_FIELDS:
                # Don't let an "all"-bucket sync downgrade a PR that's already
                # tagged for_me/created — those drive the For me / Owned tabs,
                # while the All tab ignores role and shows everything anyway.
                if field == "role" and data["role"] == "all" and existing.role in ("for_me", "created"):
                    continue
                setattr(existing, field, data[field])
            existing.synced_at = now
            results.append(existing)
        else:
            row = GitHubPRModel(
                id=uuid.uuid4(),
                backend_id=backend_id,
                github_id=data["github_id"],
                synced_at=now,
                **{field: data[field] for field in _PR_SYNC_FIELDS},
            )
            db.add(row)
            results.append(row)

    await db.commit()
    for r in results:
        await db.refresh(r)
    return results


async def persist_synced_prs(
    db: AsyncSession,
    backend_id: str,
    parsed: list[dict[str, Any]],
    projects_dirs: list[Path] | None = None,
) -> tuple[list[GitHubPRModel], list[str]]:
    """Upsert fetched PRs, skipping (and pruning) archived-repo PRs.

    PRs whose repository is archived are read-only — they can't be reviewed or
    merged — so they are never cached, and any previously-cached rows for those
    repos are deleted. Each kept PR is stamped with the local checkout this
    worker maps its repo to (``project_name``/``project_path``, memoized per
    repo) so the client can show the worker/project badge and route writable
    actions. Returns ``(upserted_rows, deleted_pr_ids)``; callers broadcast the
    updates/deletions.
    """
    archived_repos = {(p["repo_owner"], p["repo_name"]) for p in parsed if p.get("archived")}
    fresh = [p for p in parsed if not p.get("archived")]

    # Resolve the local checkout per repo once (filesystem scan is not free).
    if projects_dirs:
        repo_project: dict[tuple[str, str], Path | None] = {}
        for p in fresh:
            key = (p["repo_owner"], p["repo_name"])
            if key not in repo_project:
                repo_project[key] = await git_ops.find_local_repo(projects_dirs, *key)
            match = repo_project[key]
            p["project_name"] = match.name if match else None
            p["project_path"] = str(match) if match else None

    upserted = await upsert_prs(db, backend_id, fresh)

    deleted_ids: list[str] = []
    if archived_repos:
        conds = [
            and_(GitHubPRModel.repo_owner == owner, GitHubPRModel.repo_name == name) for owner, name in archived_repos
        ]
        stmt = select(GitHubPRModel).where(GitHubPRModel.backend_id == backend_id, or_(*conds))
        for row in (await db.execute(stmt)).scalars().all():
            deleted_ids.append(str(row.id))
            await db.delete(row)
        await db.commit()

    return upserted, deleted_ids


async def attach_pr_badges(
    session_manager: Any,
    db_factory: Any,
    pr_dicts: list[dict[str, Any]],
    *,
    restrict_to_path: str | None = None,
) -> None:
    """Attach PR badges to live sessions on each PR's head branch, then persist.

    For every PR in ``pr_dicts`` this asks the session manager to stamp the PR
    descriptor onto any active session working on that PR's head branch (which
    broadcasts a ``session_update`` so the badge renders live), then writes the
    updated session metadata to the DB so the badge survives a restart. A
    no-op when no session matches. ``restrict_to_path`` scopes matching to a
    single worktree (the open-PR flow passes the pushed worktree).
    """
    affected: dict[str, Any] = {}
    for pr in pr_dicts:
        for session in await session_manager.attach_pr_to_sessions(pr, restrict_to_path=restrict_to_path):
            affected[session.id] = session
    if affected:
        async with db_factory() as db:
            for session in affected.values():
                await session_manager.persist_session_metadata(session, db)


async def sync_and_attach_prs(
    settings: Any,
    session_manager: Any,
    db_factory: Any,
    *,
    throttle_seconds: int = 60,
) -> int:
    """Fetch open PRs, persist/broadcast them, and attach PR badges to sessions.

    Used by background triggers when a session starts on / switches to a branch
    or worktree, so an existing open PR for that branch shows up as a session
    badge (see :meth:`SessionManager.attach_pr_to_sessions`). No-op without a
    GitHub token.

    A recency throttle skips the GitHub fetch when a sync ran within
    ``throttle_seconds`` — but the badge attach still runs against PRs already in
    the DB, so a session moving onto an already-synced branch gets its badge with
    no network round-trip. Returns the number of PRs upserted (0 when throttled
    or token-less). Never raises.
    """
    token = getattr(settings, "GITHUB_TOKEN", "")
    if not token:
        return 0
    backend_id = settings.RCFLOW_BACKEND_ID
    default_repo = settings.GITHUB_DEFAULT_REPO or None

    async with db_factory() as db:
        latest = (
            await db.execute(select(func.max(GitHubPRModel.synced_at)).where(GitHubPRModel.backend_id == backend_id))
        ).scalar_one_or_none()
    # SQLite returns naive datetimes; treat a naive value as UTC before diffing.
    if latest is not None and latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    stale = latest is None or (datetime.now(UTC) - latest).total_seconds() >= throttle_seconds

    upserted: list[GitHubPRModel] = []
    fetched = False
    if stale:
        try:
            svc = GitHubService(token=token)
            try:
                parsed: list[dict[str, Any]] = []
                for role in ("for_me", "created"):
                    parsed.extend(await svc.list_pull_requests(role, repo=default_repo))
            finally:
                await svc.aclose()
            async with db_factory() as db:
                upserted, deleted_ids = await persist_synced_prs(db, backend_id, parsed, list(settings.projects_dirs))
            fetched = True
            for row in upserted:
                session_manager.broadcast_github_pr_update(serialize_pr(row))
            for pr_id in deleted_ids:
                session_manager.broadcast_github_pr_deleted(pr_id)
        except (GitHubServiceError, Exception) as exc:
            logger.warning("Background PR sync failed: %s", exc)

    # Attach badges. When we fetched, match the fresh set; otherwise match open
    # PRs already in the DB (cheap path for throttled calls / already-synced PRs).
    if fetched:
        pr_dicts = [serialize_pr(row) for row in upserted]
    else:
        async with db_factory() as db:
            rows = (
                (
                    await db.execute(
                        select(GitHubPRModel).where(
                            GitHubPRModel.backend_id == backend_id, GitHubPRModel.state == "open"
                        )
                    )
                )
                .scalars()
                .all()
            )
        pr_dicts = [serialize_pr(row) for row in rows]
    await attach_pr_badges(session_manager, db_factory, pr_dicts)
    return len(upserted)
