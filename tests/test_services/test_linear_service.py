"""Tests for LinearService and helpers in src/services/linear_service.py."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.linear_service import (
    PRIORITY_LABELS,
    LinearService,
    LinearServiceError,
    _parse_dt,
    _parse_issue,
)

# ---------------------------------------------------------------------------
# Sample fixture data
# ---------------------------------------------------------------------------

_NODE: dict = {
    "id": "lin-abc-123",
    "identifier": "ENG-42",
    "title": "Fix login bug",
    "description": "Users cannot log in with SSO",
    "priority": 1,
    "url": "https://linear.app/eng/issue/ENG-42",
    "createdAt": "2024-03-01T10:00:00.000Z",
    "updatedAt": "2024-03-05T12:30:00.000Z",
    "state": {"name": "In Progress", "type": "started"},
    "assignee": {"id": "user-1", "name": "Alice"},
    "team": {"id": "team-1", "name": "Engineering"},
    "labels": {"nodes": [{"name": "bug"}, {"name": "auth"}]},
}

_PAGE_ONE_RESPONSE = {
    "data": {
        "issues": {
            "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
            "nodes": [_NODE],
        }
    }
}

_PAGE_TWO_RESPONSE = {
    "data": {
        "issues": {
            "pageInfo": {"hasNextPage": False, "endCursor": "cursor-2"},
            "nodes": [
                {**_NODE, "id": "lin-abc-456", "identifier": "ENG-43", "title": "Second issue"},
            ],
        }
    }
}

_SINGLE_PAGE_RESPONSE = {
    "data": {
        "issues": {
            "pageInfo": {"hasNextPage": False, "endCursor": None},
            "nodes": [_NODE],
        }
    }
}

_CREATE_SUCCESS_RESPONSE = {
    "data": {
        "issueCreate": {
            "success": True,
            "issue": _NODE,
        }
    }
}

_UPDATE_SUCCESS_RESPONSE = {
    "data": {
        "issueUpdate": {
            "success": True,
            "issue": {**_NODE, "title": "Updated title", "priority": 3},
        }
    }
}

_GET_ISSUE_RESPONSE = {
    "data": {
        "issue": _NODE,
    }
}


def _mock_response(status_code: int = 200, body: dict | None = None) -> MagicMock:
    """Build a mock httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = body or {}
    return resp


# ---------------------------------------------------------------------------
# TestParseDt
# ---------------------------------------------------------------------------


class TestParseDt:
    def test_valid_iso_string(self) -> None:
        dt = _parse_dt("2024-03-01T10:00:00.000Z")
        assert dt.year == 2024
        assert dt.month == 3
        assert dt.day == 1
        assert dt.tzinfo is not None

    def test_z_suffix_replaced(self) -> None:
        dt = _parse_dt("2024-01-15T08:30:00Z")
        assert dt.tzinfo is not None
        assert dt.year == 2024

    def test_none_returns_fallback(self) -> None:
        dt = _parse_dt(None)
        assert isinstance(dt, datetime)
        assert dt.tzinfo is not None

    def test_invalid_string_returns_fallback(self) -> None:
        dt = _parse_dt("not-a-date")
        assert isinstance(dt, datetime)


# ---------------------------------------------------------------------------
# TestParseIssue
# ---------------------------------------------------------------------------


class TestParseIssue:
    def test_all_fields_populated(self) -> None:
        result = _parse_issue(_NODE)
        assert result["linear_id"] == "lin-abc-123"
        assert result["identifier"] == "ENG-42"
        assert result["title"] == "Fix login bug"
        assert result["description"] == "Users cannot log in with SSO"
        assert result["priority"] == 1
        assert result["state_name"] == "In Progress"
        assert result["state_type"] == "started"
        assert result["assignee_id"] == "user-1"
        assert result["assignee_name"] == "Alice"
        assert result["team_id"] == "team-1"
        assert result["team_name"] == "Engineering"
        assert result["url"] == "https://linear.app/eng/issue/ENG-42"

    def test_labels_are_json_string(self) -> None:
        result = _parse_issue(_NODE)
        parsed_labels = json.loads(result["labels"])
        assert parsed_labels == ["bug", "auth"]

    def test_missing_assignee_is_none(self) -> None:
        node = {**_NODE, "assignee": None}
        result = _parse_issue(node)
        assert result["assignee_id"] is None
        assert result["assignee_name"] is None

    def test_missing_state_uses_empty_string(self) -> None:
        node = {**_NODE, "state": None}
        result = _parse_issue(node)
        assert result["state_name"] == ""
        assert result["state_type"] == ""

    def test_no_labels_gives_empty_array(self) -> None:
        node = {**_NODE, "labels": {"nodes": []}}
        result = _parse_issue(node)
        assert json.loads(result["labels"]) == []

    def test_timestamps_are_datetime_objects(self) -> None:
        result = _parse_issue(_NODE)
        assert isinstance(result["created_at"], datetime)
        assert isinstance(result["updated_at"], datetime)


# ---------------------------------------------------------------------------
# TestPriorityLabels
# ---------------------------------------------------------------------------


class TestPriorityLabels:
    def test_all_priorities_have_labels(self) -> None:
        assert PRIORITY_LABELS[0] == "No priority"
        assert PRIORITY_LABELS[1] == "Urgent"
        assert PRIORITY_LABELS[2] == "High"
        assert PRIORITY_LABELS[3] == "Medium"
        assert PRIORITY_LABELS[4] == "Low"


# ---------------------------------------------------------------------------
# TestLinearServiceFetchIssues
# ---------------------------------------------------------------------------


class TestLinearServiceFetchIssues:
    @pytest.mark.asyncio
    async def test_single_page_returns_issues(self) -> None:
        mock_post = AsyncMock(return_value=_mock_response(200, _SINGLE_PAGE_RESPONSE))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                issues = await svc.fetch_issues("team-1")

        assert len(issues) == 1
        assert issues[0]["identifier"] == "ENG-42"

    @pytest.mark.asyncio
    async def test_pagination_fetches_all_pages(self) -> None:
        responses = [
            _mock_response(200, _PAGE_ONE_RESPONSE),
            _mock_response(200, _PAGE_TWO_RESPONSE),
        ]
        mock_post = AsyncMock(side_effect=responses)
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                issues = await svc.fetch_issues("team-1")

        assert len(issues) == 2
        assert mock_post.call_count == 2
        # Second call should include the cursor
        second_call_payload = mock_post.call_args_list[1].kwargs["json"]
        assert second_call_payload["variables"]["after"] == "cursor-1"

    @pytest.mark.asyncio
    async def test_empty_team_returns_empty_list(self) -> None:
        empty_response = {
            "data": {
                "issues": {
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                    "nodes": [],
                }
            }
        }
        mock_post = AsyncMock(return_value=_mock_response(200, empty_response))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                issues = await svc.fetch_issues("team-empty")

        assert issues == []


# ---------------------------------------------------------------------------
# TestLinearServiceGetIssue
# ---------------------------------------------------------------------------


class TestLinearServiceGetIssue:
    @pytest.mark.asyncio
    async def test_returns_parsed_issue(self) -> None:
        mock_post = AsyncMock(return_value=_mock_response(200, _GET_ISSUE_RESPONSE))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                issue = await svc.get_issue("lin-abc-123")

        assert issue["linear_id"] == "lin-abc-123"
        assert issue["title"] == "Fix login bug"

    @pytest.mark.asyncio
    async def test_null_issue_raises_error(self) -> None:
        resp_body = {"data": {"issue": None}}
        mock_post = AsyncMock(return_value=_mock_response(200, resp_body))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                with pytest.raises(LinearServiceError, match="not found"):
                    await svc.get_issue("nonexistent")


# ---------------------------------------------------------------------------
# TestLinearServiceCreateIssue
# ---------------------------------------------------------------------------


class TestLinearServiceCreateIssue:
    @pytest.mark.asyncio
    async def test_creates_issue(self) -> None:
        mock_post = AsyncMock(return_value=_mock_response(200, _CREATE_SUCCESS_RESPONSE))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                issue = await svc.create_issue(
                    team_id="team-1",
                    title="Fix login bug",
                    description="Details here",
                    priority=1,
                )

        assert issue["title"] == "Fix login bug"
        assert issue["priority"] == 1

    @pytest.mark.asyncio
    async def test_failed_creation_raises_error(self) -> None:
        resp_body = {"data": {"issueCreate": {"success": False, "issue": None}}}
        mock_post = AsyncMock(return_value=_mock_response(200, resp_body))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                with pytest.raises(LinearServiceError, match="creation failed"):
                    await svc.create_issue(team_id="team-1", title="Bad")


# ---------------------------------------------------------------------------
# TestLinearServiceUpdateIssue
# ---------------------------------------------------------------------------


class TestLinearServiceUpdateIssue:
    @pytest.mark.asyncio
    async def test_updates_issue(self) -> None:
        mock_post = AsyncMock(return_value=_mock_response(200, _UPDATE_SUCCESS_RESPONSE))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                issue = await svc.update_issue(
                    linear_id="lin-abc-123",
                    title="Updated title",
                    priority=3,
                )

        assert issue["title"] == "Updated title"
        assert issue["priority"] == 3

    @pytest.mark.asyncio
    async def test_only_provided_fields_included_in_variables(self) -> None:
        mock_post = AsyncMock(return_value=_mock_response(200, _UPDATE_SUCCESS_RESPONSE))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                await svc.update_issue(linear_id="lin-abc-123", title="New title")

        payload = mock_post.call_args.kwargs["json"]
        variables = payload["variables"]
        assert "title" in variables
        assert "priority" not in variables
        assert "description" not in variables

    @pytest.mark.asyncio
    async def test_failed_update_raises_error(self) -> None:
        resp_body = {"data": {"issueUpdate": {"success": False, "issue": None}}}
        mock_post = AsyncMock(return_value=_mock_response(200, resp_body))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                with pytest.raises(LinearServiceError, match="update failed"):
                    await svc.update_issue(linear_id="lin-abc-123", title="x")


# ---------------------------------------------------------------------------
# TestLinearServiceErrors
# ---------------------------------------------------------------------------


class TestLinearServiceErrors:
    @pytest.mark.asyncio
    async def test_401_raises_service_error(self) -> None:
        mock_post = AsyncMock(return_value=_mock_response(401, {}))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="bad-key") as svc:
                with pytest.raises(LinearServiceError) as exc_info:
                    await svc.fetch_issues("team-1")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_429_raises_service_error(self) -> None:
        mock_post = AsyncMock(return_value=_mock_response(429, {}))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                with pytest.raises(LinearServiceError) as exc_info:
                    await svc.fetch_issues("team-1")
        assert exc_info.value.status_code == 429

    @pytest.mark.asyncio
    async def test_5xx_raises_service_error(self) -> None:
        mock_post = AsyncMock(return_value=_mock_response(500, {}))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                with pytest.raises(LinearServiceError) as exc_info:
                    await svc.fetch_issues("team-1")
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_graphql_errors_raise_service_error(self) -> None:
        body = {"errors": [{"message": "Variable $teamId is required"}]}
        mock_post = AsyncMock(return_value=_mock_response(200, body))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                with pytest.raises(LinearServiceError, match="GraphQL error"):
                    await svc.fetch_issues("team-1")

    @pytest.mark.asyncio
    async def test_timeout_raises_service_error(self) -> None:
        mock_post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                with pytest.raises(LinearServiceError, match="timed out"):
                    await svc.fetch_issues("team-1")

    @pytest.mark.asyncio
    async def test_request_error_raises_service_error(self) -> None:
        mock_post = AsyncMock(side_effect=httpx.RequestError("connection refused"))
        with patch.object(httpx.AsyncClient, "post", mock_post):
            async with LinearService(api_key="lin_api_test") as svc:
                with pytest.raises(LinearServiceError, match="request failed"):
                    await svc.fetch_issues("team-1")


# ---------------------------------------------------------------------------
# TestLinearServiceContextManager
# ---------------------------------------------------------------------------


class TestLinearServiceContextManager:
    @pytest.mark.asyncio
    async def test_aclose_closes_http_client(self) -> None:
        svc = LinearService(api_key="lin_api_test")
        mock_aclose = AsyncMock()
        with patch.object(svc._client, "aclose", mock_aclose):
            await svc.aclose()
        mock_aclose.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_closes_on_exit(self) -> None:
        close_called = False

        async with LinearService(api_key="lin_api_test") as svc:
            mock_aclose = AsyncMock(side_effect=lambda: None)

            async def _set_flag() -> None:
                nonlocal close_called
                close_called = True

            mock_aclose.side_effect = _set_flag
            svc._client.aclose = mock_aclose  # type: ignore[method-assign]

        assert close_called
