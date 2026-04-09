"""Tests for POST /api/tasks/{task_id}/plan.

Covers:
- 404 when task_id is not found (prepare_plan_session raises ValueError)
- 400 when no project is configured (prepare_plan_session raises RuntimeError)
- 200 with session_id on success
- Background handle_prompt task is fired
- Auth enforcement
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from fastapi import FastAPI

API_KEY = "test-api-key"


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


def _auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


def _plan_url(task_id: str = "00000000-0000-0000-0000-000000000001") -> str:
    return f"/api/tasks/{task_id}/plan"


def _mock_router(
    *,
    session_id: str = "sess-plan-1",
    planning_prompt: str = "# Plan this task",
    prepare_side_effect=None,
) -> MagicMock:
    router = MagicMock()
    if prepare_side_effect is not None:
        router.prepare_plan_session = AsyncMock(side_effect=prepare_side_effect)
    else:
        router.prepare_plan_session = AsyncMock(return_value=(session_id, planning_prompt))
    router.handle_prompt = AsyncMock()
    return router


class TestStartPlanSession:
    def test_returns_200_with_session_id(self, client: TestClient, test_app: FastAPI) -> None:
        router = _mock_router(session_id="sess-abc")
        test_app.state.prompt_router = router

        resp = client.post(_plan_url(), json={}, headers=_auth())

        assert resp.status_code == 200
        assert resp.json()["session_id"] == "sess-abc"

    def test_response_has_session_id_key(self, client: TestClient, test_app: FastAPI) -> None:
        router = _mock_router(session_id="sess-xyz")
        test_app.state.prompt_router = router

        resp = client.post(_plan_url(), json={}, headers=_auth())

        assert resp.status_code == 200
        body = resp.json()
        assert "session_id" in body
        assert body["session_id"] == "sess-xyz"

    def test_unknown_task_returns_404(self, client: TestClient, test_app: FastAPI) -> None:
        router = _mock_router(prepare_side_effect=ValueError("Task not found: missing-id"))
        test_app.state.prompt_router = router

        resp = client.post(_plan_url(), json={}, headers=_auth())

        assert resp.status_code == 404

    def test_no_project_returns_400(self, client: TestClient, test_app: FastAPI) -> None:
        router = _mock_router(
            prepare_side_effect=RuntimeError("No project configured — cannot determine plan output path.")
        )
        test_app.state.prompt_router = router

        resp = client.post(_plan_url(), json={}, headers=_auth())

        assert resp.status_code == 400

    def test_handle_prompt_is_fired_as_background_task(self, client: TestClient, test_app: FastAPI) -> None:
        router = _mock_router(session_id="sess-bg", planning_prompt="Plan prompt")
        test_app.state.prompt_router = router

        resp = client.post(
            _plan_url(),
            json={"project_name": "myproject"},
            headers=_auth(),
        )

        assert resp.status_code == 200
        # handle_prompt must have been scheduled (it's a create_task fire-and-forget,
        # but the TestClient runs it synchronously via the event loop)
        router.handle_prompt.assert_awaited_once()
        call_kwargs = router.handle_prompt.call_args
        assert call_kwargs.args[0] == "Plan prompt"  # planning_prompt positional
        assert call_kwargs.args[1] == "sess-bg"  # session_id positional

    def test_project_name_forwarded_to_prepare(self, client: TestClient, test_app: FastAPI) -> None:
        router = _mock_router()
        test_app.state.prompt_router = router

        client.post(
            _plan_url(),
            json={"project_name": "my-project", "selected_worktree_path": "/repo/.wt/feat"},
            headers=_auth(),
        )

        router.prepare_plan_session.assert_awaited_once_with(
            task_id="00000000-0000-0000-0000-000000000001",
            project_name="my-project",
            selected_worktree_path="/repo/.wt/feat",
        )

    def test_empty_body_uses_none_defaults(self, client: TestClient, test_app: FastAPI) -> None:
        router = _mock_router()
        test_app.state.prompt_router = router

        client.post(_plan_url(), json={}, headers=_auth())

        router.prepare_plan_session.assert_awaited_once_with(
            task_id="00000000-0000-0000-0000-000000000001",
            project_name=None,
            selected_worktree_path=None,
        )

    def test_requires_auth(self, client: TestClient, test_app: FastAPI) -> None:
        router = _mock_router()
        test_app.state.prompt_router = router

        resp = client.post(_plan_url())  # no X-API-Key header

        assert resp.status_code in (401, 403, 422)
