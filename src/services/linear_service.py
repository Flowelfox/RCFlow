"""Linear GraphQL API client.

Wraps Linear's GraphQL API using httpx.  All methods are async.
Authentication uses a Personal API Token passed as the ``Authorization`` header.

Usage::

    async with LinearService(api_key="lin_api_...") as svc:
        issues = await svc.fetch_issues(team_id="...", limit=50)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

LINEAR_API_URL = "https://api.linear.app/graphql"

# Priority int → label (matches Linear's API)
PRIORITY_LABELS: dict[int, str] = {
    0: "No priority",
    1: "Urgent",
    2: "High",
    3: "Medium",
    4: "Low",
}

_ISSUES_QUERY = """
query ListIssues($teamId: String!, $after: String) {
  issues(
    filter: { team: { id: { eq: $teamId } } }
    first: 50
    after: $after
    orderBy: updatedAt
  ) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      identifier
      title
      description
      priority
      url
      createdAt
      updatedAt
      state {
        name
        type
      }
      assignee {
        id
        name
      }
      team {
        id
        name
      }
      labels {
        nodes {
          name
        }
      }
    }
  }
}
"""

_CREATE_ISSUE_MUTATION = """
mutation CreateIssue($teamId: String!, $title: String!, $description: String, $priority: Int) {
  issueCreate(input: {
    teamId: $teamId
    title: $title
    description: $description
    priority: $priority
  }) {
    success
    issue {
      id
      identifier
      title
      description
      priority
      url
      createdAt
      updatedAt
      state { name type }
      assignee { id name }
      team { id name }
      labels { nodes { name } }
    }
  }
}
"""

_UPDATE_ISSUE_MUTATION = """
mutation UpdateIssue($id: String!, $title: String, $description: String, $stateId: String, $priority: Int) {
  issueUpdate(id: $id, input: {
    title: $title
    description: $description
    stateId: $stateId
    priority: $priority
  }) {
    success
    issue {
      id
      identifier
      title
      description
      priority
      url
      createdAt
      updatedAt
      state { name type }
      assignee { id name }
      team { id name }
      labels { nodes { name } }
    }
  }
}
"""

_GET_ISSUE_QUERY = """
query GetIssue($id: String!) {
  issue(id: $id) {
    id
    identifier
    title
    description
    priority
    url
    createdAt
    updatedAt
    state { name type }
    assignee { id name }
    team { id name }
    labels { nodes { name } }
  }
}
"""

_TEAMS_QUERY = """
query Teams {
  teams {
    nodes {
      id
      name
    }
  }
}
"""

_ALL_ISSUES_QUERY = """
query ListAllIssues($after: String) {
  issues(
    first: 50
    after: $after
    orderBy: updatedAt
  ) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      identifier
      title
      description
      priority
      url
      createdAt
      updatedAt
      state {
        name
        type
      }
      assignee {
        id
        name
      }
      team {
        id
        name
      }
      labels {
        nodes {
          name
        }
      }
    }
  }
}
"""


def _parse_issue(node: dict[str, Any]) -> dict[str, Any]:
    """Convert a raw GraphQL issue node to a normalised dict."""
    state = node.get("state") or {}
    assignee = node.get("assignee") or {}
    team = node.get("team") or {}
    labels = [lbl["name"] for lbl in (node.get("labels") or {}).get("nodes", [])]

    return {
        "linear_id": node["id"],
        "identifier": node.get("identifier", ""),
        "title": node.get("title", ""),
        "description": node.get("description"),
        "priority": node.get("priority", 0),
        "state_name": state.get("name", ""),
        "state_type": state.get("type", ""),
        "assignee_id": assignee.get("id"),
        "assignee_name": assignee.get("name"),
        "team_id": team.get("id", ""),
        "team_name": team.get("name"),
        "url": node.get("url", ""),
        "labels": json.dumps(labels),
        "created_at": _parse_dt(node.get("createdAt")),
        "updated_at": _parse_dt(node.get("updatedAt")),
    }


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(UTC)


class LinearServiceError(Exception):
    """Raised when the Linear API returns an error response."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class LinearService:
    """Async client for the Linear GraphQL API.

    Can be used as an async context manager or standalone (call ``aclose()`` when done).
    """

    def __init__(self, api_key: str) -> None:
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def _gql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute a GraphQL request and return the ``data`` payload."""
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables

        try:
            resp = await self._client.post(LINEAR_API_URL, json=payload)
        except httpx.TimeoutException as exc:
            raise LinearServiceError("Linear API request timed out") from exc
        except httpx.RequestError as exc:
            raise LinearServiceError(f"Linear API request failed: {exc}") from exc

        if resp.status_code == 401:
            raise LinearServiceError("Linear API key is invalid or expired", status_code=401)
        if resp.status_code == 429:
            raise LinearServiceError("Linear API rate limit exceeded", status_code=429)
        if resp.status_code >= 400:
            raise LinearServiceError(
                f"Linear API returned HTTP {resp.status_code}", status_code=resp.status_code
            )

        body = resp.json()
        if "errors" in body:
            msgs = "; ".join(e.get("message", "unknown") for e in body["errors"])
            raise LinearServiceError(f"Linear GraphQL error: {msgs}")

        return body.get("data", {})

    async def fetch_teams(self) -> list[dict[str, str]]:
        """Fetch all teams accessible to the API key.

        Returns a list of dicts with ``id`` and ``name`` keys.
        """
        data = await self._gql(_TEAMS_QUERY)
        nodes = data.get("teams", {}).get("nodes", [])
        return [{"id": t["id"], "name": t["name"]} for t in nodes]

    async def fetch_all_issues(self) -> list[dict[str, Any]]:
        """Fetch all issues across all accessible teams, paginating through all pages.

        Returns a list of normalised issue dicts ready for upsert into the DB.
        """
        issues: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            variables: dict[str, Any] = {}
            if cursor:
                variables["after"] = cursor

            data = await self._gql(_ALL_ISSUES_QUERY, variables or None)
            page = data.get("issues", {})
            nodes = page.get("nodes", [])
            issues.extend(_parse_issue(n) for n in nodes)

            page_info = page.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        logger.info("Fetched %d issues from all teams", len(issues))
        return issues

    async def fetch_issues(self, team_id: str) -> list[dict[str, Any]]:
        """Fetch all issues for a team, paginating through all pages.

        Returns a list of normalised issue dicts ready for upsert into the DB.
        """
        issues: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            variables: dict[str, Any] = {"teamId": team_id}
            if cursor:
                variables["after"] = cursor

            data = await self._gql(_ISSUES_QUERY, variables)
            page = data.get("issues", {})
            nodes = page.get("nodes", [])
            issues.extend(_parse_issue(n) for n in nodes)

            page_info = page.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            cursor = page_info.get("endCursor")

        logger.info("Fetched %d issues from Linear team %s", len(issues), team_id)
        return issues

    async def get_issue(self, linear_id: str) -> dict[str, Any]:
        """Fetch a single issue by its Linear ID."""
        data = await self._gql(_GET_ISSUE_QUERY, {"id": linear_id})
        issue = data.get("issue")
        if not issue:
            raise LinearServiceError(f"Issue {linear_id} not found")
        return _parse_issue(issue)

    async def create_issue(
        self,
        team_id: str,
        title: str,
        description: str | None = None,
        priority: int = 0,
    ) -> dict[str, Any]:
        """Create a new issue in Linear and return the normalised issue dict."""
        variables: dict[str, Any] = {
            "teamId": team_id,
            "title": title,
            "description": description,
            "priority": priority,
        }
        data = await self._gql(_CREATE_ISSUE_MUTATION, variables)
        result = data.get("issueCreate", {})
        if not result.get("success"):
            raise LinearServiceError("Linear issue creation failed")
        return _parse_issue(result["issue"])

    async def update_issue(
        self,
        linear_id: str,
        title: str | None = None,
        description: str | None = None,
        state_id: str | None = None,
        priority: int | None = None,
    ) -> dict[str, Any]:
        """Update fields on an existing Linear issue."""
        variables: dict[str, Any] = {"id": linear_id}
        if title is not None:
            variables["title"] = title
        if description is not None:
            variables["description"] = description
        if state_id is not None:
            variables["stateId"] = state_id
        if priority is not None:
            variables["priority"] = priority

        data = await self._gql(_UPDATE_ISSUE_MUTATION, variables)
        result = data.get("issueUpdate", {})
        if not result.get("success"):
            raise LinearServiceError("Linear issue update failed")
        return _parse_issue(result["issue"])

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "LinearService":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
