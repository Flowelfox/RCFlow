"""Tests for src/api/routes/tools.py.

Covers the read endpoints and the not-installed / unknown-tool error paths,
which is the bulk of the route logic.  Streaming install/update endpoints
(which require network access) are exercised only for their 404 guard.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from src.services.tool_manager import ToolManager
from src.services.tool_settings import ToolSettingsManager

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

_AUTH = {"X-API-Key": "test-api-key"}


@pytest.fixture
def app_with_tools(test_app: FastAPI, test_settings, tmp_path: Path) -> FastAPI:
    tm = ToolManager(test_settings)
    tm._base_dir = tmp_path / "managed-tools"
    tm._tools = {}
    test_app.state.tool_manager = tm
    test_app.state.tool_settings = ToolSettingsManager(base_dir=tmp_path / "tool-settings")
    return test_app


@pytest.fixture
def client(app_with_tools: FastAPI) -> TestClient:
    return TestClient(app_with_tools)


class TestListTools:
    def test_requires_api_key(self, client: TestClient) -> None:
        assert client.get("/api/tools").status_code == 401

    def test_lists_tools(self, client: TestClient) -> None:
        resp = client.get("/api/tools", headers=_AUTH)
        assert resp.status_code == 200
        tools = resp.json()["tools"]
        assert isinstance(tools, list)
        assert all({"name", "executor", "mention_name"} <= t.keys() for t in tools)
        # Sorted by name.
        names = [t["name"] for t in tools]
        assert names == sorted(names)

    def test_filter_no_match(self, client: TestClient) -> None:
        resp = client.get("/api/tools", params={"q": "zzz-no-such-tool"}, headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json()["tools"] == []


class TestToolStatus:
    def test_status_empty(self, client: TestClient) -> None:
        resp = client.get("/api/tools/status", headers=_AUTH)
        assert resp.status_code == 200
        assert resp.json() == {"tools": {}}


class TestAuthPreflight:
    def test_preflight_reports_all_agents(self, client: TestClient) -> None:
        resp = client.get("/api/tools/auth/preflight", headers=_AUTH)
        assert resp.status_code == 200
        agents = resp.json()["agents"]
        assert set(agents) == {"claude_code", "codex", "opencode"}
        for entry in agents.values():
            assert "ready" in entry
            assert "issue" in entry


class TestToolSettings:
    def test_settings_not_installed(self, client: TestClient) -> None:
        resp = client.get("/api/tools/claude_code/settings", headers=_AUTH)
        assert resp.status_code == 200
        body = resp.json()
        assert body["installed"] is False
        assert body["fields"] == []

    def test_settings_unknown_tool(self, client: TestClient) -> None:
        resp = client.get("/api/tools/not-a-tool/settings", headers=_AUTH)
        assert resp.status_code == 404

    def test_patch_settings_not_installed_conflicts(self, client: TestClient) -> None:
        resp = client.patch(
            "/api/tools/claude_code/settings",
            json={"updates": {"provider": "anthropic"}},
            headers=_AUTH,
        )
        assert resp.status_code == 409


class TestUninstall:
    def test_uninstall_unknown_tool(self, client: TestClient) -> None:
        resp = client.delete("/api/tools/not-a-tool/install", headers=_AUTH)
        assert resp.status_code == 404

    def test_uninstall_not_managed(self, client: TestClient) -> None:
        # Known tool name but no managed install on disk → 400 from ValueError.
        resp = client.delete("/api/tools/claude_code/install", headers=_AUTH)
        assert resp.status_code == 400


class TestStreamingGuards:
    def test_update_unknown_tool(self, client: TestClient) -> None:
        assert client.post("/api/tools/update/not-a-tool", headers=_AUTH).status_code == 404

    def test_install_unknown_tool(self, client: TestClient) -> None:
        assert client.post("/api/tools/not-a-tool/install", headers=_AUTH).status_code == 404
