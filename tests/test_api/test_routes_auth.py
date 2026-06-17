"""Tests for the coding-agent CLI auth HTTP routes (`src/api/routes/auth.py`).

Covers the Codex ChatGPT login (browser + device-code streaming, status) and
the Claude Code Anthropic OAuth login (PKCE URL generation, code exchange,
status, logout). External processes (`codex` / `claude` CLIs) and the Anthropic
token endpoint are faked via monkeypatching so nothing real is spawned.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import httpx
import pytest
from fastapi.testclient import TestClient

import src.api.routes.auth as auth_mod

if TYPE_CHECKING:
    from fastapi import FastAPI

API_KEY = "test-api-key"


def _auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


# ── Fakes for tool_manager / tool_settings ────────────────────────────────


class _FakeToolManager:
    def __init__(self, binaries: dict[str, str | None]) -> None:
        self._binaries = binaries

    def get_binary_path(self, tool: str) -> str | None:
        return self._binaries.get(tool)


class _FakeToolSettings:
    def __init__(self, tmp_path) -> None:
        self._base = tmp_path
        self.updated: list[tuple[str, dict]] = []

    def get_config_dir(self, tool: str):
        return self._base / tool

    def update_settings(self, tool: str, values: dict) -> None:
        self.updated.append((tool, values))


# ── Fake async subprocess ─────────────────────────────────────────────────


class _FakeProc:
    """Minimal stand-in for asyncio subprocess Process."""

    def __init__(self, *, lines: list[bytes] | None = None, output: bytes = b"", returncode: int = 0) -> None:
        self._lines = list(lines or [])
        self._output = output
        self.returncode: int | None = returncode
        self.stdout = self  # readline lives on the proc itself for simplicity

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""

    async def wait(self) -> int:
        return self.returncode or 0

    async def communicate(self):
        return self._output, b""

    def kill(self) -> None:  # pragma: no cover - only called on timeout paths
        self.returncode = -9


def _make_exec(procs: list[_FakeProc]):
    """Return an async create_subprocess_exec that yields the given procs in order."""
    queue = list(procs)

    async def _exec(*_args, **_kwargs):
        if queue:
            return queue.pop(0)
        return _FakeProc(output=b"", returncode=0)

    return _exec


@pytest.fixture
def client(test_app: FastAPI, tmp_path) -> TestClient:
    test_app.state.tool_manager = _FakeToolManager({"codex": "/bin/codex", "claude_code": "/bin/claude"})
    test_app.state.tool_settings = _FakeToolSettings(tmp_path)
    test_app.state.model_catalog = None
    return TestClient(test_app)


# ── Codex login (browser + device) ────────────────────────────────────────


class TestCodexLogin:
    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.post("/api/tools/codex/login")
        assert resp.status_code in (401, 403)

    def test_not_installed_returns_400(self, test_app: FastAPI, tmp_path) -> None:
        test_app.state.tool_manager = _FakeToolManager({"codex": None})
        test_app.state.tool_settings = _FakeToolSettings(tmp_path)
        test_app.state.model_catalog = None
        c = TestClient(test_app)
        resp = c.post("/api/tools/codex/login", headers=_auth())
        assert resp.status_code == 400
        assert "not installed" in resp.json()["detail"]

    def test_browser_login_streams_auth_url_and_complete(
        self, client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        login_proc = _FakeProc(
            lines=[
                b"Starting local server...\n",
                b"https://auth.openai.com/oauth?redirect=localhost\n",
                b"Successfully logged in\n",
            ],
            returncode=0,
        )
        status_proc = _FakeProc(output=b"Logged in via ChatGPT", returncode=0)
        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _make_exec([login_proc, status_proc]))

        resp = client.post("/api/tools/codex/login", headers=_auth())
        assert resp.status_code == 200
        steps = [json.loads(line)["step"] for line in resp.text.splitlines() if line.strip()]
        assert "auth_url" in steps
        assert "complete" in steps

    def test_browser_login_spawn_failure(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(*_a, **_k):
            raise OSError("nope")

        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _boom)
        resp = client.post("/api/tools/codex/login", headers=_auth())
        assert resp.status_code == 200
        steps = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
        assert steps[0]["step"] == "error"

    def test_device_login_streams_code(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        login_proc = _FakeProc(
            lines=[
                b"Open this URL: https://auth.openai.com/device\n",
                b"Enter code: ABCD-1234\n",
                b"You are now authenticated\n",
            ],
            returncode=0,
        )
        status_proc = _FakeProc(output=b"chatgpt", returncode=0)
        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _make_exec([login_proc, status_proc]))

        resp = client.post("/api/tools/codex/login", params={"device_code": "true"}, headers=_auth())
        assert resp.status_code == 200
        events = [json.loads(line) for line in resp.text.splitlines() if line.strip()]
        steps = [e["step"] for e in events]
        assert "device_code" in steps
        device = next(e for e in events if e["step"] == "device_code")
        assert device["code"] == "ABCD-1234"
        assert "auth" in device["url"]

    def test_device_login_spawn_failure(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(*_a, **_k):
            raise OSError("nope")

        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _boom)
        resp = client.post("/api/tools/codex/login", params={"device_code": "true"}, headers=_auth())
        assert resp.status_code == 200
        first = json.loads(resp.text.splitlines()[0])
        assert first["step"] == "error"


# ── Codex login status ────────────────────────────────────────────────────


class TestCodexLoginStatus:
    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/tools/codex/login/status")
        assert resp.status_code in (401, 403)

    def test_not_installed(self, test_app: FastAPI, tmp_path) -> None:
        test_app.state.tool_manager = _FakeToolManager({"codex": None})
        test_app.state.tool_settings = _FakeToolSettings(tmp_path)
        test_app.state.model_catalog = None
        c = TestClient(test_app)
        resp = c.get("/api/tools/codex/login/status", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"logged_in": False, "method": None}

    def test_logged_in_chatgpt(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        proc = _FakeProc(output=b"Logged in via ChatGPT", returncode=0)
        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _make_exec([proc]))
        resp = client.get("/api/tools/codex/login/status", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"logged_in": True, "method": "ChatGPT"}

    def test_not_logged_in(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        proc = _FakeProc(output=b"Not authenticated", returncode=1)
        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _make_exec([proc]))
        resp = client.get("/api/tools/codex/login/status", headers=_auth())
        assert resp.json()["logged_in"] is False

    def test_exception_returns_false(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(*_a, **_k):
            raise OSError("boom")

        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _boom)
        resp = client.get("/api/tools/codex/login/status", headers=_auth())
        assert resp.json()["logged_in"] is False


# ── Claude Code login (PKCE URL) ──────────────────────────────────────────


class TestClaudeCodeLogin:
    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.post("/api/tools/claude_code/login")
        assert resp.status_code in (401, 403)

    def test_returns_auth_url_and_stores_verifier(self, client: TestClient, test_app: FastAPI) -> None:
        resp = client.post("/api/tools/claude_code/login", headers=_auth())
        assert resp.status_code == 200
        url = resp.json()["auth_url"]
        assert url.startswith("https://claude.ai/oauth/authorize?")
        assert "code_challenge_method=S256" in url
        assert getattr(test_app.state, "_claude_login_verifier", None)
        assert getattr(test_app.state, "_claude_login_state", None)


# ── Claude Code login code exchange ───────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


class _FakeHttpxClient:
    def __init__(self, response: _FakeResponse | None = None, raise_exc: Exception | None = None) -> None:
        self._response = response
        self._raise = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc) -> None:
        return None

    async def post(self, *_a, **_k):
        if self._raise is not None:
            raise self._raise
        return self._response


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, client_obj: _FakeHttpxClient) -> None:
    def _factory(*_a, **_k):
        return client_obj

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


class TestClaudeCodeLoginCode:
    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.post("/api/tools/claude_code/login/code", json={"code": "x"})
        assert resp.status_code in (401, 403)

    def test_no_active_login_409(self, client: TestClient, test_app: FastAPI) -> None:
        test_app.state._claude_login_verifier = None
        resp = client.post("/api/tools/claude_code/login/code", json={"code": "abc"}, headers=_auth())
        assert resp.status_code == 409

    def test_validation_missing_code(self, client: TestClient, test_app: FastAPI) -> None:
        test_app.state._claude_login_verifier = "v"
        resp = client.post("/api/tools/claude_code/login/code", json={}, headers=_auth())
        assert resp.status_code == 422

    def test_token_exchange_network_error_502(
        self, client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        test_app.state._claude_login_verifier = "v"
        test_app.state._claude_login_state = "s"
        _patch_httpx(monkeypatch, _FakeHttpxClient(raise_exc=RuntimeError("conn refused")))
        resp = client.post("/api/tools/claude_code/login/code", json={"code": "abc"}, headers=_auth())
        assert resp.status_code == 502
        assert "Token exchange failed" in resp.json()["detail"]

    def test_token_exchange_non_200_502(
        self, client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        test_app.state._claude_login_verifier = "v"
        test_app.state._claude_login_state = "s"
        _patch_httpx(monkeypatch, _FakeHttpxClient(_FakeResponse(400, text="bad request")))
        resp = client.post("/api/tools/claude_code/login/code", json={"code": "abc"}, headers=_auth())
        assert resp.status_code == 502
        assert "400" in resp.json()["detail"]

    def test_token_exchange_no_access_token_502(
        self, client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        test_app.state._claude_login_verifier = "v"
        test_app.state._claude_login_state = "s"
        _patch_httpx(monkeypatch, _FakeHttpxClient(_FakeResponse(200, {"access_token": ""})))
        resp = client.post("/api/tools/claude_code/login/code", json={"code": "abc"}, headers=_auth())
        assert resp.status_code == 502
        assert "no access_token" in resp.json()["detail"]

    def test_success_writes_credentials(
        self, client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        test_app.state._claude_login_verifier = "v"
        test_app.state._claude_login_state = "s"
        _patch_httpx(
            monkeypatch,
            _FakeHttpxClient(_FakeResponse(200, {"access_token": "tok", "refresh_token": "ref", "expires_in": 3600})),
        )
        # CLI verify returns non-logged-in JSON so the route falls through to the
        # default return without claiming a subscription.
        verify_proc = _FakeProc(output=b'{"loggedIn": false}', returncode=0)
        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _make_exec([verify_proc]))

        resp = client.post(
            "/api/tools/claude_code/login/code",
            json={"code": "thecode#thestate"},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert resp.json()["logged_in"] is True
        # verifier consumed
        assert test_app.state._claude_login_verifier is None
        # provider auto-set
        ts: _FakeToolSettings = test_app.state.tool_settings
        assert ("claude_code", {"provider": "anthropic_login"}) in ts.updated
        # credentials file written
        cred = ts.get_config_dir("claude_code") / ".credentials.json"
        assert cred.exists()
        data = json.loads(cred.read_text())
        assert data["claudeAiOauth"]["accessToken"] == "tok"

    def test_success_with_cli_subscription(
        self, client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        test_app.state._claude_login_verifier = "v"
        test_app.state._claude_login_state = "s"
        _patch_httpx(
            monkeypatch,
            _FakeHttpxClient(_FakeResponse(200, {"access_token": "tok", "expires_in": 100})),
        )
        verify_proc = _FakeProc(
            output=b'{"loggedIn": true, "email": "a@b.com", "subscriptionType": "max"}',
            returncode=0,
        )
        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _make_exec([verify_proc]))

        resp = client.post("/api/tools/claude_code/login/code", json={"code": "c"}, headers=_auth())
        assert resp.status_code == 200
        body = resp.json()
        assert body["logged_in"] is True
        assert body["email"] == "a@b.com"
        assert body["subscription"] == "max"


# ── Claude Code login status ──────────────────────────────────────────────


class TestClaudeCodeLoginStatus:
    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/tools/claude_code/login/status")
        assert resp.status_code in (401, 403)

    def test_not_installed(self, test_app: FastAPI, tmp_path) -> None:
        test_app.state.tool_manager = _FakeToolManager({"claude_code": None})
        test_app.state.tool_settings = _FakeToolSettings(tmp_path)
        c = TestClient(test_app)
        resp = c.get("/api/tools/claude_code/login/status", headers=_auth())
        assert resp.json() == {"logged_in": False, "method": None, "email": None, "subscription": None}

    def test_logged_in(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        proc = _FakeProc(
            output=b'{"loggedIn": true, "authMethod": "oauth", "email": "x@y.com", "subscriptionType": "pro"}',
            returncode=0,
        )
        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _make_exec([proc]))
        resp = client.get("/api/tools/claude_code/login/status", headers=_auth())
        body = resp.json()
        assert body["logged_in"] is True
        assert body["method"] == "oauth"
        assert body["subscription"] == "pro"

    def test_not_logged_in_json(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        proc = _FakeProc(output=b'{"loggedIn": false}', returncode=0)
        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _make_exec([proc]))
        resp = client.get("/api/tools/claude_code/login/status", headers=_auth())
        assert resp.json()["logged_in"] is False

    def test_bad_json_returns_false(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        proc = _FakeProc(output=b"not json at all", returncode=0)
        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _make_exec([proc]))
        resp = client.get("/api/tools/claude_code/login/status", headers=_auth())
        assert resp.json()["logged_in"] is False

    def test_exception_returns_false(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(*_a, **_k):
            raise OSError("boom")

        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _boom)
        resp = client.get("/api/tools/claude_code/login/status", headers=_auth())
        assert resp.json()["logged_in"] is False


# ── Claude Code logout ────────────────────────────────────────────────────


class TestClaudeCodeLogout:
    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.post("/api/tools/claude_code/logout")
        assert resp.status_code in (401, 403)

    def test_not_installed_400(self, test_app: FastAPI, tmp_path) -> None:
        test_app.state.tool_manager = _FakeToolManager({"claude_code": None})
        test_app.state.tool_settings = _FakeToolSettings(tmp_path)
        c = TestClient(test_app)
        resp = c.post("/api/tools/claude_code/logout", headers=_auth())
        assert resp.status_code == 400

    def test_logout_success(self, client: TestClient, test_app: FastAPI, monkeypatch: pytest.MonkeyPatch) -> None:
        proc = _FakeProc(output=b"Logged out", returncode=0)
        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _make_exec([proc]))
        resp = client.post("/api/tools/claude_code/logout", headers=_auth())
        assert resp.status_code == 200
        assert resp.json() == {"logged_out": True}
        ts: _FakeToolSettings = test_app.state.tool_settings
        assert ("claude_code", {"provider": ""}) in ts.updated

    def test_logout_failure_500(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(*_a, **_k):
            raise OSError("boom")

        monkeypatch.setattr(auth_mod.asyncio, "create_subprocess_exec", _boom)
        resp = client.post("/api/tools/claude_code/logout", headers=_auth())
        assert resp.status_code == 500
