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
from datetime import UTC, datetime
from typing import Any

import httpx

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

    async def list_pull_requests(self, role: str, repo: str | None = None) -> list[dict[str, Any]]:
        """List pull requests for a listing bucket (most-recently-updated first).

        ``role`` is ``"for_me"`` (review-requested) or ``"created"`` (authored).
        Returns the 50 most recently updated PRs across **all** states (open,
        merged, closed) so the client can filter by state; the per-PR detail
        fetch fills additions/deletions/base/head. Optionally scoped to a single
        ``owner/name`` ``repo``.
        """
        qualifier = PR_ROLE_QUALIFIERS.get(role)
        if qualifier is None:
            raise GitHubServiceError(f"Unknown PR role: {role!r}")
        # All states (no is:open) so merged/closed PRs are cached for filtering;
        # sorted by recency and capped at 50 to bound history.
        query = f"is:pr {qualifier}"
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
            full = await self.get_pull_request(owner, name, number)
            full["role"] = role
            # Enrich with reviewDecision + mergeable (GraphQL only) so the cached
            # list can show an Approved / Review-required / Can't-merge status.
            status = await self.get_pr_status(owner, name, number)
            full.update(status)
            prs.append(full)
        logger.info("Fetched %d GitHub PRs for role=%s", len(prs), role)
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
        c = await self._rest(
            "POST", f"/repos/{owner}/{repo}/issues/{number}/comments", json={"body": body}
        )
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
