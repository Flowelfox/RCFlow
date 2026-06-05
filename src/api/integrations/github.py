"""HTTP endpoints for the GitHub integration (PR reviews).

All endpoints are under /api/integrations/github/ and require X-API-Key authentication.

Endpoints
---------
GET    /status                                    Token configured + validity preflight
POST   /sync                                      Re-sync pull requests from GitHub
GET    /prs                                        List cached pull requests
GET    /prs/{id}                                   Single cached pull request
GET    /prs/{id}/files                             Live changed files (per-file unified diff)
GET    /prs/{id}/diff                              Live whole-PR unified diff (raw text)
GET    /prs/{id}/threads                           Live inline review threads (GraphQL read)
GET    /prs/{id}/conversation                      Global comments + review summaries (timeline)
POST   /prs/{id}/conversation                      Post a global (issue-level) comment
GET    /prs/{id}/draft                             Get the local pending review
PATCH  /prs/{id}/draft                             Update the pending review verdict/body
POST   /prs/{id}/draft/comments                    Queue an inline comment on the draft
DELETE /prs/{id}/draft/comments/{index}            Remove a queued inline comment
POST   /prs/{id}/review                            Submit the review (+ queued comments)
POST   /prs/{id}/comments/{comment_id}/reply       Reply to a review-thread comment
POST   /prs/{id}/threads/{thread_id}/resolve       Resolve / unresolve a thread
GET    /prs/{id}/conflicts                          Merge-conflict status + conflicting files
POST   /prs/{id}/merge                             Merge the pull request

All under /api/integrations/github/ and require X-API-Key authentication.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from src.api.deps import verify_http_api_key
from src.database.models import GitHubPR as GitHubPRModel
from src.database.models import GitHubReviewDraft as GitHubReviewDraftModel
from src.services import git_ops
from src.services.github_service import GitHubService, GitHubServiceError, evaluate_scopes

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import and_, or_, select

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/integrations/github",
    tags=["GitHub Integration"],
    dependencies=[Depends(verify_http_api_key)],
)

# Sync these listing buckets when no explicit role is given.
_DEFAULT_SYNC_ROLES = ("for_me", "created")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pr_to_dict(pr: GitHubPRModel) -> dict[str, Any]:
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


def _get_github_service(request: Request) -> GitHubService:
    """Build a GitHubService from the request's app settings.

    Raises HTTP 503 if the token is not configured.
    """
    settings = request.app.state.settings
    if not settings.GITHUB_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="GitHub token is not configured. Set GITHUB_TOKEN in Settings → GitHub.",
        )
    return GitHubService(token=settings.GITHUB_TOKEN)


async def _upsert_prs(
    db: AsyncSession,
    backend_id: str,
    parsed_prs: list[dict[str, Any]],
) -> list[GitHubPRModel]:
    """Insert or update cached pull requests.  Returns the upserted rows."""
    results: list[GitHubPRModel] = []
    now = datetime.now(UTC)

    _fields = (
        "repo_owner",
        "repo_name",
        "number",
        "title",
        "body",
        "state",
        "draft",
        "review_decision",
        "merge_status",
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

    for data in parsed_prs:
        stmt = select(GitHubPRModel).where(
            GitHubPRModel.backend_id == backend_id,
            GitHubPRModel.github_id == data["github_id"],
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()

        if existing:
            for field in _fields:
                setattr(existing, field, data[field])
            existing.synced_at = now
            results.append(existing)
        else:
            row = GitHubPRModel(
                id=uuid.uuid4(),
                backend_id=backend_id,
                github_id=data["github_id"],
                synced_at=now,
                **{field: data[field] for field in _fields},
            )
            db.add(row)
            results.append(row)

    await db.commit()
    for r in results:
        await db.refresh(r)
    return results


async def _persist_synced_prs(
    db: AsyncSession,
    backend_id: str,
    parsed: list[dict[str, Any]],
) -> tuple[list[GitHubPRModel], list[str]]:
    """Upsert fetched PRs, skipping (and pruning) archived-repo PRs.

    PRs whose repository is archived are read-only — they can't be reviewed or
    merged — so they are never cached, and any previously-cached rows for those
    repos are deleted. Returns ``(upserted_rows, deleted_pr_ids)``; callers
    broadcast the updates/deletions.
    """
    archived_repos = {(p["repo_owner"], p["repo_name"]) for p in parsed if p.get("archived")}
    fresh = [p for p in parsed if not p.get("archived")]

    upserted = await _upsert_prs(db, backend_id, fresh)

    deleted_ids: list[str] = []
    if archived_repos:
        conds = [
            and_(GitHubPRModel.repo_owner == owner, GitHubPRModel.repo_name == name)
            for owner, name in archived_repos
        ]
        stmt = select(GitHubPRModel).where(GitHubPRModel.backend_id == backend_id, or_(*conds))
        for row in (await db.execute(stmt)).scalars().all():
            deleted_ids.append(str(row.id))
            await db.delete(row)
        await db.commit()

    return upserted, deleted_ids


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/status",
    summary="GitHub integration status",
    description=(
        "Reports whether a GitHub token is configured and, if so, validates it "
        "against the GitHub API and returns the authenticated user's login. Used "
        "by the client to decide whether to surface the PR-review UI."
    ),
)
async def github_status(request: Request) -> dict[str, Any]:
    """Return GitHub token configuration, validity, and a scope checklist.

    ``scopes`` lists every scope the PR-review feature needs with a per-scope
    ``satisfied`` flag (checked against the token's granted scopes). For
    fine-grained tokens scopes are not enumerable, so ``fine_grained`` is True
    and ``satisfied`` is null (validity is confirmed by the live API check).
    """
    settings = request.app.state.settings
    base_scopes = evaluate_scopes([])  # required list with satisfied=False as the unconfigured shape
    if not settings.GITHUB_TOKEN:
        return {"configured": False, "valid": False, "login": None, "fine_grained": False, "scopes": base_scopes}

    svc = GitHubService(token=settings.GITHUB_TOKEN)
    try:
        info = await svc.token_info()
    except GitHubServiceError as exc:
        return {
            "configured": True,
            "valid": False,
            "login": None,
            "fine_grained": False,
            "scopes": base_scopes,
            "error": str(exc),
        }
    finally:
        await svc.aclose()

    fine_grained = info["fine_grained"]
    # Fine-grained tokens don't report scopes, so the boxes can't be ticked.
    scopes = [{**s, "satisfied": None} for s in base_scopes] if fine_grained else evaluate_scopes(info["scopes"])
    return {
        "configured": True,
        "valid": True,
        "login": info["login"],
        "fine_grained": fine_grained,
        "granted": info["scopes"],
        "scopes": scopes,
    }


@router.post(
    "/sync",
    summary="Sync GitHub pull requests",
    description=(
        "Fetches open pull requests from GitHub and updates the local cache. "
        "By default syncs both the 'for me' (review-requested) and 'created' "
        "(authored) buckets; pass ?role= to sync just one. Scoped to "
        "GITHUB_DEFAULT_REPO when set. A worker with no GITHUB_TOKEN is a no-op "
        "(returns synced=0, configured=false) rather than an error, so a client "
        "can sweep every connected worker without special-casing token-less ones."
    ),
)
async def sync_github_prs(
    request: Request,
    role: str | None = Query(None, description="Limit sync to a single bucket: for_me or created"),
) -> dict[str, Any]:
    """Trigger a sync of GitHub pull requests from the API."""
    settings = request.app.state.settings
    session_manager = request.app.state.session_manager
    db_factory = request.app.state.db_session_factory

    # No token configured on this worker → no-op so a multi-worker sync sweep
    # doesn't fail on token-less workers (the PR tab syncs every worker).
    if not settings.GITHUB_TOKEN:
        return {"synced": 0, "archived_pruned": 0, "configured": False}

    roles = (role,) if role else _DEFAULT_SYNC_ROLES
    repo = settings.GITHUB_DEFAULT_REPO or None

    svc = _get_github_service(request)
    parsed: list[dict[str, Any]] = []
    try:
        for r in roles:
            parsed.extend(await svc.list_pull_requests(r, repo=repo))
    except GitHubServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()

    async with db_factory() as db:
        upserted, deleted_ids = await _persist_synced_prs(db, settings.RCFLOW_BACKEND_ID, parsed)

    for row in upserted:
        session_manager.broadcast_github_pr_update(_pr_to_dict(row))
    for pr_id in deleted_ids:
        session_manager.broadcast_github_pr_deleted(pr_id)

    logger.info(
        "GitHub sync complete: %d PRs upserted, %d archived-repo PRs pruned",
        len(upserted),
        len(deleted_ids),
    )
    return {"synced": len(upserted), "archived_pruned": len(deleted_ids), "configured": True}


@router.get(
    "/prs",
    summary="List cached pull requests",
    description=(
        "Returns all locally-cached GitHub pull requests for this backend. Use "
        "POST /sync to refresh from GitHub first. Filter by ?role= (for_me / "
        "created) and search title with ?q=."
    ),
)
async def list_github_prs(
    request: Request,
    role: str | None = Query(None, description="Filter by listing bucket: for_me or created"),
    state: str | None = Query(None, description="Filter by state: open, closed, merged"),
    q: str | None = Query(None, description="Search title"),
) -> dict[str, Any]:
    """List cached pull requests with optional filters."""
    settings = request.app.state.settings
    db_factory = request.app.state.db_session_factory

    async with db_factory() as db:
        stmt = select(GitHubPRModel).where(GitHubPRModel.backend_id == settings.RCFLOW_BACKEND_ID)
        if role:
            stmt = stmt.where(GitHubPRModel.role == role)
        if state:
            stmt = stmt.where(GitHubPRModel.state == state)
        rows = (await db.execute(stmt)).scalars().all()

    prs = [_pr_to_dict(r) for r in rows]
    if q:
        ql = q.lower()
        prs = [p for p in prs if ql in p["title"].lower()]
    prs.sort(key=lambda p: p["updated_at"], reverse=True)
    return {"prs": prs, "total": len(prs)}


async def _load_pr(request: Request, pr_id: str) -> GitHubPRModel:
    """Load a cached PR by local UUID or raise 404/422."""
    settings = request.app.state.settings
    db_factory = request.app.state.db_session_factory
    try:
        uid = uuid.UUID(pr_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid UUID format") from None

    async with db_factory() as db:
        stmt = select(GitHubPRModel).where(
            GitHubPRModel.id == uid,
            GitHubPRModel.backend_id == settings.RCFLOW_BACKEND_ID,
        )
        row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Pull request not found")
    return row


@router.get(
    "/prs/{pr_id}",
    summary="Get a cached pull request",
    description="Returns a cached GitHub pull request by its local UUID.",
)
async def get_github_pr(pr_id: str, request: Request) -> dict[str, Any]:
    """Get a single cached pull request by local UUID."""
    return _pr_to_dict(await _load_pr(request, pr_id))


@router.get(
    "/prs/{pr_id}/files",
    summary="List a pull request's changed files",
    description=(
        "Fetches the changed files of a pull request live from GitHub. Each "
        "entry carries the file's unified-diff patch for rendering. Requires "
        "GITHUB_TOKEN."
    ),
)
async def get_github_pr_files(pr_id: str, request: Request) -> dict[str, Any]:
    """Return the live changed files (with per-file unified diff) of a PR."""
    pr = await _load_pr(request, pr_id)
    svc = _get_github_service(request)
    try:
        files = await svc.list_pr_files(pr.repo_owner, pr.repo_name, pr.number)
    except GitHubServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()
    return {"pr_id": str(pr.id), "files": files, "total": len(files)}


@router.get(
    "/prs/{pr_id}/diff",
    summary="Get a pull request's whole-PR diff",
    description="Fetches the entire pull request's unified diff as raw text live from GitHub. Requires GITHUB_TOKEN.",
)
async def get_github_pr_diff(pr_id: str, request: Request) -> dict[str, Any]:
    """Return the live whole-PR unified diff (raw text) of a PR."""
    pr = await _load_pr(request, pr_id)
    svc = _get_github_service(request)
    try:
        diff = await svc.get_pr_diff(pr.repo_owner, pr.repo_name, pr.number)
    except GitHubServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()
    return {"pr_id": str(pr.id), "diff": diff}


@router.get(
    "/prs/{pr_id}/file",
    summary="Get a file's full content at the PR head or base",
    description=(
        "Fetches the full text of a file at the PR's head (default) or base, so "
        "the client can expand diff context beyond the patch hunks. Requires "
        "GITHUB_TOKEN."
    ),
)
async def get_github_pr_file(
    pr_id: str,
    request: Request,
    path: str = Query(..., description="File path within the repo"),
    side: str = Query("head", description="Which version to fetch: head or base"),
) -> dict[str, Any]:
    """Return a file's full content at the PR head or base ref."""
    pr = await _load_pr(request, pr_id)
    if side not in ("head", "base"):
        raise HTTPException(status_code=422, detail="side must be head or base")
    ref = pr.head_sha if side == "head" else pr.base_ref
    svc = _get_github_service(request)
    try:
        content = await svc.get_file_content(pr.repo_owner, pr.repo_name, path, ref)
    except GitHubServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()
    return {"pr_id": str(pr.id), "path": path, "side": side, "ref": ref, "content": content}


# ---------------------------------------------------------------------------
# Review threads, drafts, submission, merge
# ---------------------------------------------------------------------------


def _draft_to_dict(draft: GitHubReviewDraftModel) -> dict[str, Any]:
    """Serialise a review draft to a JSON-safe dict."""
    return {
        "id": str(draft.id),
        "pr_id": str(draft.pr_id),
        "event": draft.event,
        "body": draft.body,
        "comments": json.loads(draft.comments or "[]"),
        "created_at": draft.created_at.isoformat(),
        "updated_at": draft.updated_at.isoformat(),
    }


async def _resync_pr(request: Request, pr: GitHubPRModel) -> dict[str, Any]:
    """Re-fetch a PR's detail from GitHub, upsert the cache, broadcast, return it.

    Preserves the existing listing ``role`` (the detail endpoint has no role).
    Best-effort: a sync failure does not fail the surrounding action.
    """
    settings = request.app.state.settings
    session_manager = request.app.state.session_manager
    db_factory = request.app.state.db_session_factory

    svc = GitHubService(token=settings.GITHUB_TOKEN)
    try:
        parsed = await svc.get_pull_request(pr.repo_owner, pr.repo_name, pr.number)
    except GitHubServiceError:
        return _pr_to_dict(pr)
    finally:
        await svc.aclose()

    parsed["role"] = pr.role
    async with db_factory() as db:
        rows = await _upsert_prs(db, settings.RCFLOW_BACKEND_ID, [parsed])
        row = rows[0]
    result = _pr_to_dict(row)
    session_manager.broadcast_github_pr_update(result)
    return result


class AddDraftCommentRequest(BaseModel):
    """Queue an inline comment on the pending review.

    ``line``/``side`` are the end (or only) line. For a multi-line comment also
    set ``start_line`` (and optionally ``start_side``) — GitHub anchors the
    comment to the ``start_line``..``line`` range.
    """

    path: str
    line: int
    side: str = "RIGHT"  # LEFT|RIGHT
    body: str
    start_line: int | None = None
    start_side: str | None = None  # LEFT|RIGHT; defaults to side


class PatchDraftRequest(BaseModel):
    """Update the pending review's verdict and/or summary body."""

    event: str | None = None  # APPROVE|REQUEST_CHANGES|COMMENT
    body: str | None = None


class SubmitReviewRequest(BaseModel):
    """Submit the pending review to GitHub."""

    event: str  # APPROVE|REQUEST_CHANGES|COMMENT
    body: str = ""


class ReplyRequest(BaseModel):
    """Reply to an existing review-thread comment."""

    body: str


class IssueCommentRequest(BaseModel):
    """Post a general (issue-level) comment on a PR's conversation."""

    body: str


class ResolveThreadRequest(BaseModel):
    """Resolve or unresolve a review thread."""

    resolved: bool = True


class MergeRequest(BaseModel):
    """Merge a pull request."""

    method: str = "squash"  # merge|squash|rebase
    commit_title: str | None = None
    commit_message: str | None = None


@router.get(
    "/prs/{pr_id}/threads",
    summary="List a pull request's review threads",
    description=(
        "Fetches the inline review-comment threads of a pull request live from "
        "GitHub (path, line, side, resolved/outdated state, and comments). "
        "Requires GITHUB_TOKEN."
    ),
)
async def get_github_pr_threads(pr_id: str, request: Request) -> dict[str, Any]:
    """Return the live review threads of a PR."""
    pr = await _load_pr(request, pr_id)
    svc = _get_github_service(request)
    try:
        threads = await svc.list_review_threads(pr.repo_owner, pr.repo_name, pr.number)
    except GitHubServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()
    return {"pr_id": str(pr.id), "threads": threads, "total": len(threads)}


@router.get(
    "/prs/{pr_id}/conversation",
    summary="Get a pull request's conversation",
    description=(
        "Fetches the PR's general (issue-level) conversation live from GitHub — "
        "the comments not anchored to a diff line, merged with submitted review "
        "summaries (approve / request-changes / comment notes) — as a single "
        "timeline sorted oldest-first. Each item is `{kind: comment|review, "
        "author, author_avatar_url, body, created_at, url, state?}` (`state` only "
        "on review items). Requires GITHUB_TOKEN."
    ),
)
async def get_github_pr_conversation(pr_id: str, request: Request) -> dict[str, Any]:
    """Return the PR's global comments + review summaries as one timeline."""
    pr = await _load_pr(request, pr_id)
    svc = _get_github_service(request)
    try:
        comments = await svc.list_issue_comments(pr.repo_owner, pr.repo_name, pr.number)
        reviews = await svc.list_reviews(pr.repo_owner, pr.repo_name, pr.number)
    except GitHubServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()

    items: list[dict[str, Any]] = [{**c, "kind": "comment", "state": None} for c in comments]
    # Include review summaries that carry a verdict or a written note; skip empty
    # "commented" reviews (those only exist to hold inline comments).
    for r in reviews:
        if r.get("body") or r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            items.append({**r, "kind": "review"})
    # Oldest-first; items without a timestamp sort to the end.
    items.sort(key=lambda i: i.get("created_at") or "")
    return {"pr_id": str(pr.id), "items": items, "total": len(items)}


@router.post(
    "/prs/{pr_id}/conversation",
    summary="Post a conversation comment on a pull request",
    description=(
        "Posts a general (issue-level) comment on the PR's conversation (not "
        "anchored to a diff line). Returns the created comment. Requires "
        "GITHUB_TOKEN."
    ),
)
async def post_github_pr_conversation(
    pr_id: str, body: IssueCommentRequest, request: Request
) -> dict[str, Any]:
    """Post a general comment on a PR's conversation and return it."""
    text = body.body.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Comment body must not be empty")
    pr = await _load_pr(request, pr_id)
    svc = _get_github_service(request)
    try:
        created = await svc.create_issue_comment(pr.repo_owner, pr.repo_name, pr.number, text)
    except GitHubServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()
    return {"pr_id": str(pr.id), "comment": {**created, "kind": "comment", "state": None}}


async def _load_draft(request: Request, pr: GitHubPRModel, *, create: bool) -> GitHubReviewDraftModel | None:
    """Load (or optionally create) the pending review draft for a PR."""
    settings = request.app.state.settings
    db_factory = request.app.state.db_session_factory
    async with db_factory() as db:
        stmt = select(GitHubReviewDraftModel).where(
            GitHubReviewDraftModel.backend_id == settings.RCFLOW_BACKEND_ID,
            GitHubReviewDraftModel.pr_id == pr.id,
        )
        draft = (await db.execute(stmt)).scalar_one_or_none()
        if draft is None and create:
            draft = GitHubReviewDraftModel(
                id=uuid.uuid4(),
                backend_id=settings.RCFLOW_BACKEND_ID,
                pr_id=pr.id,
            )
            db.add(draft)
            await db.commit()
            await db.refresh(draft)
    return draft


@router.get(
    "/prs/{pr_id}/draft",
    summary="Get the pending review draft",
    description="Returns the local in-progress review (verdict, summary, queued inline comments) for a PR.",
)
async def get_github_pr_draft(pr_id: str, request: Request) -> dict[str, Any]:
    """Return the pending review draft (empty default when none exists)."""
    pr = await _load_pr(request, pr_id)
    draft = await _load_draft(request, pr, create=False)
    if draft is None:
        return {"pr_id": str(pr.id), "event": "COMMENT", "body": "", "comments": []}
    return _draft_to_dict(draft)


@router.patch(
    "/prs/{pr_id}/draft",
    summary="Update the pending review draft",
    description="Sets the pending review's verdict (APPROVE/REQUEST_CHANGES/COMMENT) and/or summary body.",
)
async def patch_github_pr_draft(pr_id: str, body: PatchDraftRequest, request: Request) -> dict[str, Any]:
    """Update the pending review's verdict and/or body."""
    pr = await _load_pr(request, pr_id)
    if body.event is not None and body.event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
        raise HTTPException(status_code=422, detail="Invalid event")
    db_factory = request.app.state.db_session_factory
    await _load_draft(request, pr, create=True)
    async with db_factory() as db:
        stmt = select(GitHubReviewDraftModel).where(GitHubReviewDraftModel.pr_id == pr.id)
        draft = (await db.execute(stmt)).scalar_one()
        if body.event is not None:
            draft.event = body.event
        if body.body is not None:
            draft.body = body.body
        await db.commit()
        await db.refresh(draft)
    return _draft_to_dict(draft)


@router.post(
    "/prs/{pr_id}/draft/comments",
    summary="Queue an inline comment on the pending review",
    description=(
        "Appends an inline comment (path, line, side, body) to the local review draft without posting to GitHub."
    ),
)
async def add_github_pr_draft_comment(pr_id: str, body: AddDraftCommentRequest, request: Request) -> dict[str, Any]:
    """Append a queued inline comment to the pending review."""
    pr = await _load_pr(request, pr_id)
    if body.side not in ("LEFT", "RIGHT"):
        raise HTTPException(status_code=422, detail="side must be LEFT or RIGHT")
    if body.start_line is not None:
        if body.start_side is not None and body.start_side not in ("LEFT", "RIGHT"):
            raise HTTPException(status_code=422, detail="start_side must be LEFT or RIGHT")
        if body.start_line > body.line:
            raise HTTPException(status_code=422, detail="start_line must be <= line")
    db_factory = request.app.state.db_session_factory
    await _load_draft(request, pr, create=True)
    async with db_factory() as db:
        stmt = select(GitHubReviewDraftModel).where(GitHubReviewDraftModel.pr_id == pr.id)
        draft = (await db.execute(stmt)).scalar_one()
        comments = json.loads(draft.comments or "[]")
        comment: dict[str, Any] = {"path": body.path, "line": body.line, "side": body.side, "body": body.body}
        if body.start_line is not None:
            comment["start_line"] = body.start_line
            comment["start_side"] = body.start_side or body.side
        comments.append(comment)
        draft.comments = json.dumps(comments)
        await db.commit()
        await db.refresh(draft)
    return _draft_to_dict(draft)


@router.delete(
    "/prs/{pr_id}/draft/comments/{index}",
    summary="Remove a queued inline comment",
    description="Removes the queued inline comment at the given index from the pending review draft.",
)
async def delete_github_pr_draft_comment(pr_id: str, index: int, request: Request) -> dict[str, Any]:
    """Remove a queued inline comment by index."""
    pr = await _load_pr(request, pr_id)
    db_factory = request.app.state.db_session_factory
    async with db_factory() as db:
        stmt = select(GitHubReviewDraftModel).where(
            GitHubReviewDraftModel.pr_id == pr.id,
            GitHubReviewDraftModel.backend_id == request.app.state.settings.RCFLOW_BACKEND_ID,
        )
        draft = (await db.execute(stmt)).scalar_one_or_none()
        if draft is None:
            raise HTTPException(status_code=404, detail="No review draft")
        comments = json.loads(draft.comments or "[]")
        if not 0 <= index < len(comments):
            raise HTTPException(status_code=404, detail="Comment index out of range")
        comments.pop(index)
        draft.comments = json.dumps(comments)
        await db.commit()
        await db.refresh(draft)
    return _draft_to_dict(draft)


@router.post(
    "/prs/{pr_id}/review",
    summary="Submit the pending review to GitHub",
    description=(
        "Submits the review with the given verdict and summary, posting any "
        "queued inline comments, then clears the local draft. Requires "
        "GITHUB_TOKEN."
    ),
)
async def submit_github_pr_review(pr_id: str, body: SubmitReviewRequest, request: Request) -> dict[str, Any]:
    """Submit the pending review (verdict + queued inline comments) to GitHub."""
    pr = await _load_pr(request, pr_id)
    if body.event not in ("APPROVE", "REQUEST_CHANGES", "COMMENT"):
        raise HTTPException(status_code=422, detail="Invalid event")

    db_factory = request.app.state.db_session_factory
    draft = await _load_draft(request, pr, create=False)
    queued = json.loads(draft.comments) if draft else []

    svc = _get_github_service(request)
    try:
        review = await svc.create_review(
            pr.repo_owner,
            pr.repo_name,
            pr.number,
            event=body.event,
            body=body.body,
            comments=queued or None,
        )
    except GitHubServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()

    if draft is not None:
        async with db_factory() as db:
            await db.delete(await db.merge(draft))
            await db.commit()

    pr_data = await _resync_pr(request, pr)
    return {"review": {"id": review.get("id"), "state": review.get("state")}, "pr": pr_data}


@router.post(
    "/prs/{pr_id}/comments/{comment_id}/reply",
    summary="Reply to a review-thread comment",
    description="Posts a reply to an existing review-thread comment (by its GitHub comment id). Requires GITHUB_TOKEN.",
)
async def reply_github_pr_comment(pr_id: str, comment_id: int, body: ReplyRequest, request: Request) -> dict[str, Any]:
    """Reply to an existing review-thread comment."""
    pr = await _load_pr(request, pr_id)
    svc = _get_github_service(request)
    try:
        reply = await svc.reply_review_comment(pr.repo_owner, pr.repo_name, pr.number, comment_id, body.body)
    except GitHubServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()
    return {"id": reply.get("id"), "body": reply.get("body")}


@router.delete(
    "/prs/{pr_id}/comments/{comment_id}",
    summary="Delete a review-thread comment",
    description=(
        "Deletes a review-thread comment (by its GitHub comment id). Only the "
        "comment's author may delete it — GitHub returns 403 otherwise. Requires "
        "GITHUB_TOKEN."
    ),
)
async def delete_github_pr_comment(pr_id: str, comment_id: int, request: Request) -> dict[str, Any]:
    """Delete a review-thread comment (author-only)."""
    pr = await _load_pr(request, pr_id)
    svc = _get_github_service(request)
    try:
        await svc.delete_review_comment(pr.repo_owner, pr.repo_name, comment_id)
    except GitHubServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()
    return {"deleted": True, "comment_id": comment_id}


@router.get(
    "/prs/{pr_id}/project",
    summary="Resolve the PR's local project",
    description=(
        "Finds the local checkout of the PR's repository among the configured "
        "projects directories (by matching the git origin remote). Returns the "
        "project folder name + path, or nulls when no local clone is found."
    ),
)
async def get_github_pr_project(pr_id: str, request: Request) -> dict[str, Any]:
    """Resolve the PR's repo to a local project folder (badge/working dir)."""
    pr = await _load_pr(request, pr_id)
    settings = request.app.state.settings
    match = await git_ops.find_local_repo(list(settings.projects_dirs), pr.repo_owner, pr.repo_name)
    if match is None:
        return {"pr_id": str(pr.id), "project_name": None, "project_path": None}
    return {"pr_id": str(pr.id), "project_name": match.name, "project_path": str(match)}


@router.get(
    "/prs/{pr_id}/conflicts",
    summary="Check a pull request's merge conflicts",
    description=(
        "Reports whether the pull request conflicts with its base branch and, "
        "when a local clone is available, which files conflict. GitHub's API "
        "reports only *that* a PR conflicts, so the conflicting file list is "
        "computed from a local 3-way merge (git merge-tree) against the checkout "
        "found in the configured projects directories — nothing is written to "
        "the working tree. Returns conflicted=null while GitHub is still "
        "computing mergeability, and files=null when no local clone exists or the "
        "local git is too old. Also flags PRs blocked by branch protection / "
        "repository rules (required reviews or status checks), which can't be "
        "merged even when conflict-free. Requires GITHUB_TOKEN."
    ),
)
async def get_github_pr_conflicts(pr_id: str, request: Request) -> dict[str, Any]:
    """Return merge-conflict / mergeability status for a PR.

    ``conflicted`` is True/False/None (None = GitHub still computing). ``files``
    is the list of conflicting paths, an empty list when clean, or null when the
    file list could not be computed locally. ``mergeable`` is whether GitHub will
    accept a merge now (False for conflicts or blocked-by-rules, null while
    computing). ``reason`` is one of ``clean``, ``computing``, ``conflicting``,
    ``no_local_clone``, ``blocked``. ``mergeable_state`` is GitHub's raw state.
    """
    pr = await _load_pr(request, pr_id)
    settings = request.app.state.settings
    svc = _get_github_service(request)
    try:
        detail = await svc.get_pull_request(pr.repo_owner, pr.repo_name, pr.number)
    except GitHubServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()

    mergeable = detail.get("mergeable")
    state = detail.get("mergeable_state")

    # Blocked by branch protection / repo rules (required reviews or checks):
    # conflict-free, but GitHub rejects the merge — surface it like conflicts.
    if state == "blocked":
        return {
            "pr_id": str(pr.id),
            "conflicted": False,
            "files": [],
            "mergeable": False,
            "reason": "blocked",
            "mergeable_state": state,
        }
    if mergeable is True or state == "clean":
        return {
            "pr_id": str(pr.id),
            "conflicted": False,
            "files": [],
            "mergeable": True,
            "reason": "clean",
            "mergeable_state": state,
        }
    if mergeable is None or state in (None, "unknown"):
        # GitHub computes mergeability asynchronously; the client should retry.
        return {
            "pr_id": str(pr.id),
            "conflicted": None,
            "files": None,
            "mergeable": None,
            "reason": "computing",
            "mergeable_state": state,
        }

    # Conflict indicated — try to compute the conflicting file list locally.
    match = await git_ops.find_local_repo(list(settings.projects_dirs), pr.repo_owner, pr.repo_name)
    no_clone = {
        "pr_id": str(pr.id),
        "conflicted": True,
        "files": None,
        "mergeable": False,
        "reason": "no_local_clone",
        "mergeable_state": state,
    }
    if match is None:
        return no_clone
    try:
        files = await git_ops.merge_conflict_files(match, pr.base_ref, pr.number)
    except git_ops.MergeToolUnavailableError:
        return no_clone
    except git_ops.GitOpsError as exc:
        logger.warning("Local conflict computation failed for PR %s: %s", pr.id, exc)
        return no_clone
    # A clean local merge despite GitHub flagging conflict (e.g. stale state).
    if not files:
        return {
            "pr_id": str(pr.id),
            "conflicted": False,
            "files": [],
            "mergeable": True,
            "reason": "clean",
            "mergeable_state": state,
        }
    return {
        "pr_id": str(pr.id),
        "conflicted": True,
        "files": files,
        "mergeable": False,
        "reason": "conflicting",
        "mergeable_state": state,
    }


@router.post(
    "/prs/{pr_id}/threads/{thread_id}/resolve",
    summary="Resolve or unresolve a review thread",
    description="Marks a review thread resolved (or unresolved with ?resolved=false). Requires GITHUB_TOKEN.",
)
async def resolve_github_pr_thread(
    pr_id: str, thread_id: str, request: Request, body: ResolveThreadRequest | None = None
) -> dict[str, Any]:
    """Resolve or unresolve a review thread by its GraphQL node id."""
    await _load_pr(request, pr_id)
    resolved = body.resolved if body is not None else True
    svc = _get_github_service(request)
    try:
        await svc.resolve_thread(thread_id, resolved=resolved)
    except GitHubServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()
    return {"thread_id": thread_id, "resolved": resolved}


@router.post(
    "/prs/{pr_id}/merge",
    summary="Merge a pull request",
    description=(
        "Merges the pull request (squash by default). Returns 502 if GitHub rejects the merge. Requires GITHUB_TOKEN."
    ),
)
async def merge_github_pr(pr_id: str, request: Request, body: MergeRequest | None = None) -> dict[str, Any]:
    """Merge a pull request and refresh its cached state."""
    pr = await _load_pr(request, pr_id)
    req = body or MergeRequest()
    if req.method not in ("merge", "squash", "rebase"):
        raise HTTPException(status_code=422, detail="Invalid merge method")
    svc = _get_github_service(request)
    try:
        result = await svc.merge_pull_request(
            pr.repo_owner,
            pr.repo_name,
            pr.number,
            method=req.method,
            commit_title=req.commit_title,
            commit_message=req.commit_message,
        )
    except GitHubServiceError as exc:
        # GitHub returns 405 when the PR is not in a mergeable state (conflicts,
        # failing required checks, etc.). Surface a clear message instead of the
        # raw "HTTP 405".
        if getattr(exc, "status_code", None) == 405:
            raise HTTPException(
                status_code=409,
                detail="This pull request cannot be merged — it has conflicts with the base branch.",
            ) from exc
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()
    pr_data = await _resync_pr(request, pr)
    return {"merged": bool(result.get("merged")), "message": result.get("message"), "pr": pr_data}


# ---------------------------------------------------------------------------
# Author & open a PR from a local worktree (push + create)
# ---------------------------------------------------------------------------


class OpenPrRequest(BaseModel):
    """Push a worktree's branch and open a pull request for it."""

    selected_worktree_path: str | None = None
    project_name: str | None = None  # resolved under projects_dirs when no path given
    title: str
    body: str = ""
    base: str = "main"
    head_branch: str | None = None  # default: the worktree's current branch
    commit_message: str | None = None  # if set, commit pending changes first
    draft: bool = False


def _resolve_worktree_path(request: Request, body: OpenPrRequest) -> str:
    """Resolve the worktree to operate on (explicit path, else project folder)."""
    if body.selected_worktree_path:
        return body.selected_worktree_path
    settings = request.app.state.settings
    if body.project_name:
        for base in settings.projects_dirs:
            candidate = base / body.project_name
            if candidate.is_dir():
                return str(candidate)
    raise HTTPException(status_code=422, detail="Provide selected_worktree_path or a valid project_name")


@router.post(
    "/open-pr",
    summary="Open a pull request from a local worktree",
    description=(
        "Pushes the selected worktree's branch to GitHub (authenticated with "
        "GITHUB_TOKEN) and opens a pull request for it. Optionally commits "
        "pending changes first. Requires GITHUB_TOKEN and a GitHub 'origin' "
        "remote on the worktree."
    ),
)
async def open_github_pr(body: OpenPrRequest, request: Request) -> dict[str, Any]:
    """Commit (optional) → push the worktree branch → open a PR → cache it."""
    settings = request.app.state.settings
    session_manager = request.app.state.session_manager
    db_factory = request.app.state.db_session_factory
    if not settings.GITHUB_TOKEN:
        raise HTTPException(status_code=503, detail="GitHub token is not configured.")

    worktree_path = _resolve_worktree_path(request, body)

    try:
        owner_repo = await git_ops.parse_github_remote(worktree_path)
        if owner_repo is None:
            raise HTTPException(status_code=422, detail="No GitHub 'origin' remote on the worktree.")
        if body.commit_message:
            await git_ops.commit_all(worktree_path, body.commit_message)
        branch = await git_ops.push_branch(
            worktree_path, settings.GITHUB_TOKEN, branch=body.head_branch, owner_repo=owner_repo
        )
    except git_ops.GitOpsError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    owner, repo = owner_repo
    svc = GitHubService(token=settings.GITHUB_TOKEN)
    try:
        parsed = await svc.create_pull_request(
            owner, repo, title=body.title, head=branch, base=body.base, body=body.body, draft=body.draft
        )
    except GitHubServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()

    parsed["role"] = "created"
    async with db_factory() as db:
        rows = await _upsert_prs(db, settings.RCFLOW_BACKEND_ID, [parsed])
        row = rows[0]
    result = _pr_to_dict(row)
    session_manager.broadcast_github_pr_update(result)
    return {"pr": result, "url": result["url"]}
