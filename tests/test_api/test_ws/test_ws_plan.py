"""Tests for the start_plan_session WebSocket handler in /ws/input/text.

Covers:
- Missing task_id → MISSING_TASK_ID error
- prepare_plan_session raises ValueError → PLAN_SESSION_ERROR error
- prepare_plan_session raises RuntimeError → PLAN_SESSION_ERROR error
- Success: ack sent with session_id and purpose="plan"
- handle_prompt background task is scheduled
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from fastapi import FastAPI

API_KEY = "test-api-key"


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


@pytest.fixture(autouse=True)
def _patch_ws_auth():
    """Bypass WebSocket API-key verification for all tests in this module."""

    async def _noop(websocket: object = None, api_key: str = "") -> str:
        return api_key

    with (
        patch("src.api.ws.output_text.verify_ws_api_key", new=_noop),
        patch("src.api.ws.input_text.verify_ws_api_key", new=_noop),
    ):
        yield


def _ws_url() -> str:
    return f"/ws/input/text?api_key={API_KEY}"


def _mock_router(
    *,
    session_id: str = "plan-sess-1",
    prompt: str = "# Plan",
    prepare_side_effect=None,
) -> MagicMock:
    router = MagicMock()
    if prepare_side_effect is not None:
        router.prepare_plan_session = AsyncMock(side_effect=prepare_side_effect)
    else:
        router.prepare_plan_session = AsyncMock(return_value=(session_id, prompt))
    router.handle_prompt = AsyncMock()
    return router


class TestStartPlanSessionWsHandler:
    def test_missing_task_id_returns_error(self, client: TestClient, test_app: FastAPI) -> None:
        test_app.state.prompt_router = _mock_router()

        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "start_plan_session"})  # no task_id
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "MISSING_TASK_ID"

    def test_value_error_returns_plan_session_error(self, client: TestClient, test_app: FastAPI) -> None:
        router = _mock_router(prepare_side_effect=ValueError("Task not found: bad-id"))
        test_app.state.prompt_router = router

        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "start_plan_session", "task_id": "bad-id"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "PLAN_SESSION_ERROR"
        assert "Task not found" in data["content"]

    def test_runtime_error_returns_plan_session_error(self, client: TestClient, test_app: FastAPI) -> None:
        router = _mock_router(prepare_side_effect=RuntimeError("No project configured"))
        test_app.state.prompt_router = router

        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "start_plan_session", "task_id": "some-task-id"})
            data = ws.receive_json()

        assert data["type"] == "error"
        assert data["code"] == "PLAN_SESSION_ERROR"

    def test_success_sends_ack_with_session_id(self, client: TestClient, test_app: FastAPI) -> None:
        router = _mock_router(session_id="plan-sess-abc")
        test_app.state.prompt_router = router

        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "start_plan_session", "task_id": "task-123"})
            data = ws.receive_json()

        assert data["type"] == "ack"
        assert data["session_id"] == "plan-sess-abc"
        assert data["purpose"] == "plan"

    def test_success_fires_handle_prompt(self, client: TestClient, test_app: FastAPI) -> None:
        router = _mock_router(session_id="plan-sess-xyz", prompt="Do the plan")
        test_app.state.prompt_router = router

        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json({"type": "start_plan_session", "task_id": "task-456"})
            ws.receive_json()  # consume ack

        router.handle_prompt.assert_awaited_once()
        args = router.handle_prompt.call_args
        assert args.args[0] == "Do the plan"  # planning_prompt
        assert args.args[1] == "plan-sess-xyz"  # session_id

    def test_project_name_forwarded(self, client: TestClient, test_app: FastAPI) -> None:
        router = _mock_router()
        test_app.state.prompt_router = router

        with client.websocket_connect(_ws_url()) as ws:
            ws.send_json(
                {
                    "type": "start_plan_session",
                    "task_id": "task-789",
                    "project_name": "my-project",
                    "selected_worktree_path": "/repo/.wt/feat",
                }
            )
            ws.receive_json()

        router.prepare_plan_session.assert_awaited_once_with(
            task_id="task-789",
            project_name="my-project",
            selected_worktree_path="/repo/.wt/feat",
        )
