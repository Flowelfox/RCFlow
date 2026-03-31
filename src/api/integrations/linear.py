"""HTTP endpoints for the Linear integration.

All endpoints are under /api/integrations/linear/ and require X-API-Key authentication.

Endpoints
---------
POST   /api/integrations/linear/test                Test an API key and return accessible teams
GET    /api/integrations/linear/teams               List teams accessible via the configured API key
GET    /api/integrations/linear/issues              List cached issues
GET    /api/integrations/linear/issues/{id}         Single cached issue
POST   /api/integrations/linear/sync                Trigger full re-sync from Linear
POST   /api/integrations/linear/issues              Create new issue in Linear + cache it
PATCH  /api/integrations/linear/issues/{id}         Update issue in Linear + refresh cache
POST   /api/integrations/linear/issues/{id}/link    Link issue to a local task
DELETE /api/integrations/linear/issues/{id}/link    Unlink issue from its task
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from src.api.deps import verify_http_api_key
from src.models.db import LinearIssue as LinearIssueModel
from src.models.db import Task as TaskModel
from src.services.linear_service import LinearService, LinearServiceError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/integrations/linear",
    tags=["Linear Integration"],
    dependencies=[Depends(verify_http_api_key)],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _issue_to_dict(issue: LinearIssueModel) -> dict[str, Any]:
    """Serialise a LinearIssue ORM row to a JSON-safe dict."""
    return {
        "id": str(issue.id),
        "linear_id": issue.linear_id,
        "identifier": issue.identifier,
        "title": issue.title,
        "description": issue.description,
        "priority": issue.priority,
        "state_name": issue.state_name,
        "state_type": issue.state_type,
        "assignee_id": issue.assignee_id,
        "assignee_name": issue.assignee_name,
        "team_id": issue.team_id,
        "team_name": issue.team_name,
        "url": issue.url,
        "labels": json.loads(issue.labels or "[]"),
        "created_at": issue.created_at.isoformat(),
        "updated_at": issue.updated_at.isoformat(),
        "synced_at": issue.synced_at.isoformat(),
        "task_id": str(issue.task_id) if issue.task_id else None,
    }


def _get_linear_service(request: Request) -> LinearService:
    """Build a LinearService from the request's app settings.

    Raises HTTP 503 if the API key is not configured.
    """
    settings = request.app.state.settings
    if not settings.LINEAR_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="Linear API key is not configured. Set LINEAR_API_KEY in Settings → Linear.",
        )
    return LinearService(api_key=settings.LINEAR_API_KEY)


async def _upsert_issues(
    db: AsyncSession,
    backend_id: str,
    parsed_issues: list[dict[str, Any]],
) -> list[LinearIssueModel]:
    """Insert or update cached Linear issues.  Returns the upserted rows."""
    results: list[LinearIssueModel] = []
    now = datetime.now(UTC)

    for data in parsed_issues:
        stmt = select(LinearIssueModel).where(
            LinearIssueModel.backend_id == backend_id,
            LinearIssueModel.linear_id == data["linear_id"],
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()

        if existing:
            existing.identifier = data["identifier"]
            existing.title = data["title"]
            existing.description = data["description"]
            existing.priority = data["priority"]
            existing.state_name = data["state_name"]
            existing.state_type = data["state_type"]
            existing.assignee_id = data["assignee_id"]
            existing.assignee_name = data["assignee_name"]
            existing.team_id = data["team_id"]
            existing.team_name = data["team_name"]
            existing.url = data["url"]
            existing.labels = data["labels"]
            existing.created_at = data["created_at"]
            existing.updated_at = data["updated_at"]
            existing.synced_at = now
            results.append(existing)
        else:
            row = LinearIssueModel(
                id=uuid.uuid4(),
                backend_id=backend_id,
                linear_id=data["linear_id"],
                identifier=data["identifier"],
                title=data["title"],
                description=data["description"],
                priority=data["priority"],
                state_name=data["state_name"],
                state_type=data["state_type"],
                assignee_id=data["assignee_id"],
                assignee_name=data["assignee_name"],
                team_id=data["team_id"],
                team_name=data["team_name"],
                url=data["url"],
                labels=data["labels"],
                created_at=data["created_at"],
                updated_at=data["updated_at"],
                synced_at=now,
            )
            db.add(row)
            results.append(row)

    await db.commit()
    for r in results:
        await db.refresh(r)
    return results


# ---------------------------------------------------------------------------
# Request/response models
# ---------------------------------------------------------------------------

class TestLinearConnectionRequest(BaseModel):
    api_key: str


class CreateIssueRequest(BaseModel):
    title: str
    description: str | None = None
    priority: int = 0
    team_id: str | None = None  # Required when LINEAR_TEAM_ID is not configured


class UpdateIssueRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    state_id: str | None = None
    priority: int | None = None


class LinkTaskRequest(BaseModel):
    task_id: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/test",
    summary="Test a Linear API key",
    description=(
        "Validates the provided Linear API key by connecting to the Linear API "
        "and returning the list of accessible teams. Does not require an existing "
        "key to be configured — this is used during initial setup."
    ),
)
async def test_linear_connection(body: TestLinearConnectionRequest) -> dict[str, Any]:
    """Test a Linear API key and return accessible teams.

    This endpoint does not require ``LINEAR_API_KEY`` to be set in settings.
    The key is taken directly from the request body for validation purposes.
    """
    svc = LinearService(api_key=body.api_key)
    try:
        teams = await svc.fetch_teams()
    except LinearServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()
    return {"ok": True, "teams": teams}


@router.get(
    "/teams",
    summary="List Linear teams",
    description=(
        "Returns all Linear teams accessible via the configured API key. "
        "Requires LINEAR_API_KEY to be set."
    ),
)
async def list_linear_teams(request: Request) -> dict[str, Any]:
    """List all teams accessible via the configured Linear API key."""
    svc = _get_linear_service(request)
    try:
        teams = await svc.fetch_teams()
    except LinearServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()
    return {"teams": teams}


@router.get(
    "/issues",
    summary="List cached Linear issues",
    description=(
        "Returns all locally-cached Linear issues for this backend. "
        "Use POST /sync to refresh from Linear first."
    ),
)
async def list_linear_issues(
    request: Request,
    state_type: str | None = Query(None, description="Filter by state_type (started, completed, etc.)"),
    priority: int | None = Query(None, description="Filter by priority (0-4)"),
    q: str | None = Query(None, description="Search title or identifier"),
) -> dict[str, Any]:
    """List all cached Linear issues with optional filters."""
    settings = request.app.state.settings
    db_factory = request.app.state.db_session_factory

    async with db_factory() as db:
        stmt = select(LinearIssueModel).where(
            LinearIssueModel.backend_id == settings.RCFLOW_BACKEND_ID
        )
        if state_type:
            stmt = stmt.where(LinearIssueModel.state_type == state_type)
        if priority is not None:
            stmt = stmt.where(LinearIssueModel.priority == priority)
        rows = (await db.execute(stmt)).scalars().all()

    issues = [_issue_to_dict(r) for r in rows]

    if q:
        ql = q.lower()
        issues = [
            i for i in issues
            if ql in i["title"].lower() or ql in i["identifier"].lower()
        ]

    issues.sort(key=lambda i: i["updated_at"], reverse=True)
    return {"issues": issues, "total": len(issues)}


@router.get(
    "/issues/{issue_id}",
    summary="Get a single cached Linear issue",
    description="Returns a cached Linear issue by its local UUID.",
)
async def get_linear_issue(issue_id: str, request: Request) -> dict[str, Any]:
    """Get a single cached Linear issue by local UUID."""
    settings = request.app.state.settings
    db_factory = request.app.state.db_session_factory

    try:
        uid = uuid.UUID(issue_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid UUID format") from None

    async with db_factory() as db:
        stmt = select(LinearIssueModel).where(
            LinearIssueModel.id == uid,
            LinearIssueModel.backend_id == settings.RCFLOW_BACKEND_ID,
        )
        row = (await db.execute(stmt)).scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Linear issue not found")
    return _issue_to_dict(row)


@router.post(
    "/sync",
    summary="Sync Linear issues",
    description=(
        "Fetches issues from Linear and updates the local cache. "
        "If LINEAR_TEAM_ID is set, syncs only that team. "
        "If LINEAR_TEAM_ID is blank, syncs all teams accessible via the API key. "
        "Requires LINEAR_API_KEY to be set."
    ),
)
async def sync_linear_issues(request: Request) -> dict[str, Any]:
    """Trigger a full sync of Linear issues from the API.

    When ``LINEAR_TEAM_ID`` is configured only that team is synced.
    When it is blank all issues accessible via the API key are synced.
    """
    settings = request.app.state.settings
    session_manager = request.app.state.session_manager
    db_factory = request.app.state.db_session_factory

    svc = _get_linear_service(request)
    errors: list[str] = []

    try:
        if settings.LINEAR_TEAM_ID:
            parsed = await svc.fetch_issues(settings.LINEAR_TEAM_ID)
        else:
            parsed = await svc.fetch_all_issues()
    except LinearServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()

    async with db_factory() as db:
        upserted = await _upsert_issues(db, settings.RCFLOW_BACKEND_ID, parsed)

    # Broadcast each upserted issue to connected WS clients
    for row in upserted:
        session_manager.broadcast_linear_issue_update(_issue_to_dict(row))

    logger.info("Linear sync complete: %d issues upserted", len(upserted))
    return {"synced": len(upserted), "errors": errors}


@router.post(
    "/issues",
    status_code=201,
    summary="Create a Linear issue",
    description=(
        "Creates a new issue in Linear and caches it locally. "
        "Uses LINEAR_TEAM_ID from settings if set; otherwise ``team_id`` must be "
        "provided in the request body."
    ),
)
async def create_linear_issue(
    body: CreateIssueRequest,
    request: Request,
) -> dict[str, Any]:
    """Create a new issue in Linear and return the cached record."""
    settings = request.app.state.settings
    session_manager = request.app.state.session_manager
    db_factory = request.app.state.db_session_factory

    team_id = settings.LINEAR_TEAM_ID or body.team_id
    if not team_id:
        raise HTTPException(
            status_code=422,
            detail=(
                "team_id is required when LINEAR_TEAM_ID is not configured. "
                "Pass team_id in the request body."
            ),
        )

    svc = _get_linear_service(request)
    try:
        parsed = await svc.create_issue(
            team_id=team_id,
            title=body.title,
            description=body.description,
            priority=body.priority,
        )
    except LinearServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()

    async with db_factory() as db:
        rows = await _upsert_issues(db, settings.RCFLOW_BACKEND_ID, [parsed])
        row = rows[0]

    result = _issue_to_dict(row)
    session_manager.broadcast_linear_issue_update(result)
    return result


@router.patch(
    "/issues/{issue_id}",
    summary="Update a Linear issue",
    description="Updates a Linear issue in the API and refreshes the local cache.",
)
async def update_linear_issue(
    issue_id: str,
    body: UpdateIssueRequest,
    request: Request,
) -> dict[str, Any]:
    """Update a Linear issue and return the refreshed cached record."""
    settings = request.app.state.settings
    session_manager = request.app.state.session_manager
    db_factory = request.app.state.db_session_factory

    try:
        uid = uuid.UUID(issue_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid UUID format") from None

    async with db_factory() as db:
        stmt = select(LinearIssueModel).where(
            LinearIssueModel.id == uid,
            LinearIssueModel.backend_id == settings.RCFLOW_BACKEND_ID,
        )
        existing = (await db.execute(stmt)).scalar_one_or_none()

    if existing is None:
        raise HTTPException(status_code=404, detail="Linear issue not found")

    svc = _get_linear_service(request)
    try:
        parsed = await svc.update_issue(
            linear_id=existing.linear_id,
            title=body.title,
            description=body.description,
            state_id=body.state_id,
            priority=body.priority,
        )
    except LinearServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await svc.aclose()

    async with db_factory() as db:
        rows = await _upsert_issues(db, settings.RCFLOW_BACKEND_ID, [parsed])
        row = rows[0]

    result = _issue_to_dict(row)
    session_manager.broadcast_linear_issue_update(result)
    return result


@router.post(
    "/issues/{issue_id}/link",
    summary="Link a Linear issue to a task",
    description="Associates a cached Linear issue with an existing local task.",
)
async def link_issue_to_task(
    issue_id: str,
    body: LinkTaskRequest,
    request: Request,
) -> dict[str, Any]:
    """Link a Linear issue to a local task."""
    settings = request.app.state.settings
    session_manager = request.app.state.session_manager
    db_factory = request.app.state.db_session_factory

    try:
        uid = uuid.UUID(issue_id)
        task_uid = uuid.UUID(body.task_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid UUID format") from None

    async with db_factory() as db:
        issue_stmt = select(LinearIssueModel).where(
            LinearIssueModel.id == uid,
            LinearIssueModel.backend_id == settings.RCFLOW_BACKEND_ID,
        )
        issue = (await db.execute(issue_stmt)).scalar_one_or_none()
        if issue is None:
            raise HTTPException(status_code=404, detail="Linear issue not found")

        task_stmt = select(TaskModel).where(
            TaskModel.id == task_uid,
            TaskModel.backend_id == settings.RCFLOW_BACKEND_ID,
        )
        task = (await db.execute(task_stmt)).scalar_one_or_none()
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found")

        issue.task_id = task_uid
        await db.commit()
        await db.refresh(issue)

    result = _issue_to_dict(issue)
    session_manager.broadcast_linear_issue_update(result)
    return result


@router.post(
    "/issues/{issue_id}/create-task",
    status_code=201,
    summary="Create an RCFlow task from a Linear issue",
    description=(
        "Creates a new RCFlow task populated with the Linear issue's title and description, "
        "then atomically links the issue to the newly created task. "
        "Returns 409 if the issue is already linked to a task."
    ),
)
async def create_task_from_linear_issue(
    issue_id: str,
    request: Request,
) -> dict[str, Any]:
    """Create an RCFlow task from a cached Linear issue and link them atomically.

    The new task is created with ``source='linear'``, status ``'todo'``, and its
    title/description copied from the issue. The issue's ``task_id`` is set to the
    new task's ID in the same database transaction. Both updates are broadcast to
    all connected WebSocket clients.
    """
    settings = request.app.state.settings
    session_manager = request.app.state.session_manager
    db_factory = request.app.state.db_session_factory

    try:
        uid = uuid.UUID(issue_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid UUID format") from None

    now = datetime.now(UTC)

    async with db_factory() as db:
        issue_stmt = select(LinearIssueModel).where(
            LinearIssueModel.id == uid,
            LinearIssueModel.backend_id == settings.RCFLOW_BACKEND_ID,
        )
        issue = (await db.execute(issue_stmt)).scalar_one_or_none()

        if issue is None:
            raise HTTPException(status_code=404, detail="Linear issue not found")

        if issue.task_id is not None:
            raise HTTPException(
                status_code=409,
                detail="This Linear issue is already linked to a task.",
            )

        task = TaskModel(
            id=uuid.uuid4(),
            backend_id=settings.RCFLOW_BACKEND_ID,
            title=issue.title,
            description=issue.description,
            status="todo",
            source="linear",
            created_at=now,
            updated_at=now,
        )
        db.add(task)
        await db.flush()

        issue.task_id = task.id
        await db.commit()
        await db.refresh(task)
        await db.refresh(issue)

    task_data: dict[str, Any] = {
        "task_id": str(task.id),
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "source": task.source,
        "created_at": task.created_at.isoformat() if task.created_at else "",
        "updated_at": task.updated_at.isoformat() if task.updated_at else "",
        "sessions": [],
    }
    issue_data = _issue_to_dict(issue)

    session_manager.broadcast_task_update(task_data)
    session_manager.broadcast_linear_issue_update(issue_data)

    return {"task": task_data, "issue": issue_data}


@router.delete(
    "/issues/{issue_id}/link",
    summary="Unlink a Linear issue from its task",
    description="Removes the association between a Linear issue and its linked task.",
)
async def unlink_issue_from_task(issue_id: str, request: Request) -> dict[str, Any]:
    """Remove the task link from a Linear issue."""
    settings = request.app.state.settings
    session_manager = request.app.state.session_manager
    db_factory = request.app.state.db_session_factory

    try:
        uid = uuid.UUID(issue_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid UUID format") from None

    async with db_factory() as db:
        stmt = select(LinearIssueModel).where(
            LinearIssueModel.id == uid,
            LinearIssueModel.backend_id == settings.RCFLOW_BACKEND_ID,
        )
        issue = (await db.execute(stmt)).scalar_one_or_none()
        if issue is None:
            raise HTTPException(status_code=404, detail="Linear issue not found")

        issue.task_id = None
        await db.commit()
        await db.refresh(issue)

    result = _issue_to_dict(issue)
    session_manager.broadcast_linear_issue_update(result)
    return result
