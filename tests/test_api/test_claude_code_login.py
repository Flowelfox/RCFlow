"""Tests for ``POST /api/tools/claude_code/login/code``.

Locks in the regression where a single failed token-exchange attempt cleared
the PKCE verifier, forcing the user back through the browser-OAuth dance for
nothing more than a typo in the pasted code.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from fastapi import FastAPI

API_KEY = "test-api-key"


def _auth_headers() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


@pytest.fixture
def client(test_app: FastAPI, tmp_path) -> TestClient:  # type: ignore[no-untyped-def]
    # /tools/claude_code/login/code touches tool_settings.get_config_dir(); the
    # base test_app does not provision tool_settings/tool_manager, so install
    # minimal stand-ins.
    test_app.state.tool_settings = SimpleNamespace(
        get_config_dir=lambda _name: tmp_path,
        update_settings=MagicMock(),
    )
    test_app.state.tool_manager = SimpleNamespace(get_binary_path=lambda _name: None)
    return TestClient(test_app)


def test_login_code_requires_prior_login(client: TestClient) -> None:
    """Calling /code without /login first returns 409 with the expected message."""
    resp = client.post(
        "/api/tools/claude_code/login/code",
        headers=_auth_headers(),
        json={"code": "anything"},
    )
    assert resp.status_code == 409
    assert "No active login" in resp.json()["detail"]


def test_failed_token_exchange_keeps_verifier(client: TestClient, test_app: FastAPI) -> None:
    """A 4xx from the OAuth provider (typo'd code) must NOT clear the verifier."""
    test_app.state._claude_login_verifier = "v" * 32
    test_app.state._claude_login_state = "s" * 32

    fake_response = SimpleNamespace(status_code=400, text='{"error": "invalid_grant"}')

    class _FakeAsyncClient:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_a):  # type: ignore[no-untyped-def]
            return False

        async def post(self, *_a, **_kw):  # type: ignore[no-untyped-def]
            return fake_response

    with patch("httpx.AsyncClient", return_value=_FakeAsyncClient()):
        resp = client.post(
            "/api/tools/claude_code/login/code",
            headers=_auth_headers(),
            json={"code": "bad-code"},
        )

    assert resp.status_code == 502
    # Verifier must still be present so the user can retype the code without
    # restarting the browser flow.
    assert test_app.state._claude_login_verifier == "v" * 32
    assert test_app.state._claude_login_state == "s" * 32


def test_successful_token_exchange_clears_verifier(client: TestClient, test_app: FastAPI) -> None:
    """A 200 from the OAuth provider consumes the verifier (single-use)."""
    test_app.state._claude_login_verifier = "v" * 32
    test_app.state._claude_login_state = "s" * 32

    fake_response = SimpleNamespace(
        status_code=200,
        text="",
        json=lambda: {
            "access_token": "tok",
            "refresh_token": "ref",
            "expires_in": 3600,
        },
    )

    class _FakeAsyncClient:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_a):  # type: ignore[no-untyped-def]
            return False

        async def post(self, *_a, **_kw):  # type: ignore[no-untyped-def]
            return fake_response

    with patch("httpx.AsyncClient", return_value=_FakeAsyncClient()):
        resp = client.post(
            "/api/tools/claude_code/login/code",
            headers=_auth_headers(),
            json={"code": "good-code"},
        )

    assert resp.status_code == 200
    assert resp.json()["logged_in"] is True
    assert test_app.state._claude_login_verifier is None
    assert test_app.state._claude_login_state is None


def test_network_failure_keeps_verifier(client: TestClient, test_app: FastAPI) -> None:
    """A transport-level error (Anthropic unreachable) must keep the verifier."""
    test_app.state._claude_login_verifier = "v" * 32
    test_app.state._claude_login_state = "s" * 32

    class _FakeAsyncClient:
        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return self

        async def __aexit__(self, *_a):  # type: ignore[no-untyped-def]
            return False

        post = AsyncMock(side_effect=ConnectionError("boom"))

    with patch("httpx.AsyncClient", return_value=_FakeAsyncClient()):
        resp = client.post(
            "/api/tools/claude_code/login/code",
            headers=_auth_headers(),
            json={"code": "anything"},
        )

    assert resp.status_code == 502
    assert test_app.state._claude_login_verifier == "v" * 32
