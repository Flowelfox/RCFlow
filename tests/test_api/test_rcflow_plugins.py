"""Tests for the plugin management API.

Covers both the canonical tool-scoped endpoints:
    GET    /api/tools/{tool_name}/plugins
    POST   /api/tools/{tool_name}/plugins
    DELETE /api/tools/{tool_name}/plugins/{name}
    PATCH  /api/tools/{tool_name}/plugins/{name}

And the deprecated legacy aliases:
    GET    /api/rcflow-plugins
    POST   /api/rcflow-plugins
    DELETE /api/rcflow-plugins/{name}
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI

API_KEY = "test-api-key"


def _auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


def _make_plugin(plugins_dir: Path, name: str, commands: dict[str, str]) -> Path:
    """Create a minimal plugin directory and return the plugin path."""
    plugin_dir = plugins_dir / name / "commands"
    plugin_dir.mkdir(parents=True)
    for cmd_name, frontmatter in commands.items():
        (plugin_dir / f"{cmd_name}.md").write_text(
            f"---\n{frontmatter}\n---\n\nBody.",
            encoding="utf-8",
        )
    return plugins_dir / name


def _make_plugins_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plugins"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Tool-scoped endpoints — list
# ---------------------------------------------------------------------------

class TestListToolPlugins:
    def test_returns_200_for_claude_code(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.get("/api/tools/claude_code/plugins", headers=_auth())
        assert resp.status_code == 200

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/tools/claude_code/plugins")
        assert resp.status_code in (401, 403, 422)

    def test_unknown_tool_returns_404(self, client: TestClient) -> None:
        resp = client.get("/api/tools/unknown_tool/plugins", headers=_auth())
        assert resp.status_code == 404

    def test_codex_returns_422(self, client: TestClient) -> None:
        resp = client.get("/api/tools/codex/plugins", headers=_auth())
        assert resp.status_code == 422
        assert "codex" in resp.json()["detail"].lower()

    def test_response_shape(self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pd = _make_plugins_dir(tmp_path)
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.get("/api/tools/claude_code/plugins", headers=_auth())
        body = resp.json()
        assert "plugins" in body
        assert isinstance(body["plugins"], list)

    def test_empty_when_no_plugins(self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        empty = tmp_path / "empty-plugins"
        empty.mkdir()
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: empty)
        resp = client.get("/api/tools/claude_code/plugins", headers=_auth())
        assert resp.json()["plugins"] == []

    def test_lists_installed_plugins(self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "my-tool", {"do-thing": 'description: "Do the thing"'})
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.get("/api/tools/claude_code/plugins", headers=_auth())
        plugins = resp.json()["plugins"]
        assert any(p["name"] == "my-tool" for p in plugins)

    def test_plugin_has_enabled_field(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "some-plugin", {"cmd": 'description: "X"'})
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.get("/api/tools/claude_code/plugins", headers=_auth())
        plugin = next(p for p in resp.json()["plugins"] if p["name"] == "some-plugin")
        assert "enabled" in plugin
        assert plugin["enabled"] is True  # enabled by default

    def test_disabled_plugin_shows_enabled_false(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "disabled-one", {"cmd": 'description: "X"'})
        # Write the plugins_state.json marking this plugin as disabled
        (pd / "plugins_state.json").write_text(
            json.dumps({"disabled": ["disabled-one"]}), encoding="utf-8"
        )
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.get("/api/tools/claude_code/plugins", headers=_auth())
        plugin = next(p for p in resp.json()["plugins"] if p["name"] == "disabled-one")
        assert plugin["enabled"] is False

    def test_plugin_has_commands_list(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "multi", {"alpha": 'description: "A"', "beta": 'description: "B"'})
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.get("/api/tools/claude_code/plugins", headers=_auth())
        plugin = next(p for p in resp.json()["plugins"] if p["name"] == "multi")
        assert set(plugin["commands"]) == {"alpha", "beta"}

    def test_hidden_commands_excluded(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "my-tool", {
            "visible": 'description: "OK"',
            "hidden": 'description: "Nope"\nhide-from-slash-command-tool: "true"',
        })
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.get("/api/tools/claude_code/plugins", headers=_auth())
        plugin = next(p for p in resp.json()["plugins"] if p["name"] == "my-tool")
        assert "visible" in plugin["commands"]
        assert "hidden" not in plugin["commands"]

    def test_description_from_plugin_json(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        plugin_dir = pd / "with-desc"
        plugin_dir.mkdir()
        manifest_dir = plugin_dir / ".claude-plugin"
        manifest_dir.mkdir()
        (manifest_dir / "plugin.json").write_text(
            json.dumps({"name": "with-desc", "description": "My manifest desc"}), encoding="utf-8"
        )
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.get("/api/tools/claude_code/plugins", headers=_auth())
        plugin = next(p for p in resp.json()["plugins"] if p["name"] == "with-desc")
        assert plugin["description"] == "My manifest desc"


# ---------------------------------------------------------------------------
# Tool-scoped endpoints — install
# ---------------------------------------------------------------------------

class TestInstallToolPlugin:
    def test_install_from_local_path_201(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        source_dir = tmp_path / "source-plugin"
        (source_dir / "commands").mkdir(parents=True)
        (source_dir / "commands" / "run.md").write_text("---\ndescription: Run\n---\n", encoding="utf-8")
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.post(
            "/api/tools/claude_code/plugins",
            json={"source": str(source_dir), "name": "my-local"},
            headers=_auth(),
        )
        assert resp.status_code == 201
        plugin = resp.json()["plugin"]
        assert plugin["name"] == "my-local"
        assert "run" in plugin["commands"]

    def test_install_creates_directory(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        source_dir = tmp_path / "src-plugin"
        source_dir.mkdir()
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.post(
            "/api/tools/claude_code/plugins",
            json={"source": str(source_dir), "name": "new-plugin"},
            headers=_auth(),
        )
        assert resp.status_code == 201
        assert (pd / "new-plugin").is_dir()

    def test_install_duplicate_returns_409(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "existing", {"cmd": 'description: "X"'})
        source_dir = tmp_path / "another-source"
        source_dir.mkdir()
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.post(
            "/api/tools/claude_code/plugins",
            json={"source": str(source_dir), "name": "existing"},
            headers=_auth(),
        )
        assert resp.status_code == 409

    def test_install_empty_source_returns_422(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.post("/api/tools/claude_code/plugins", json={"source": ""}, headers=_auth())
        assert resp.status_code == 422

    def test_install_requires_auth(self, client: TestClient) -> None:
        resp = client.post("/api/tools/claude_code/plugins", json={"source": "x"})
        assert resp.status_code in (401, 403, 422)

    def test_install_name_derived_from_source(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        source_dir = tmp_path / "cool-tool"
        source_dir.mkdir()
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.post(
            "/api/tools/claude_code/plugins",
            json={"source": str(source_dir)},
            headers=_auth(),
        )
        assert resp.status_code == 201
        assert resp.json()["plugin"]["name"] == "cool-tool"

    def test_install_codex_returns_422(self, client: TestClient, tmp_path: Path) -> None:
        resp = client.post(
            "/api/tools/codex/plugins",
            json={"source": "/some/path"},
            headers=_auth(),
        )
        assert resp.status_code == 422

    def test_install_unknown_tool_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/api/tools/unknown/plugins",
            json={"source": "/some/path"},
            headers=_auth(),
        )
        assert resp.status_code == 404

    def test_installed_plugin_has_enabled_true(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        source_dir = tmp_path / "fresh-plugin"
        source_dir.mkdir()
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.post(
            "/api/tools/claude_code/plugins",
            json={"source": str(source_dir), "name": "fresh-plugin"},
            headers=_auth(),
        )
        assert resp.status_code == 201
        assert resp.json()["plugin"]["enabled"] is True


# ---------------------------------------------------------------------------
# Tool-scoped endpoints — uninstall
# ---------------------------------------------------------------------------

class TestUninstallToolPlugin:
    def test_uninstall_200(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "bye-plugin", {"cmd": 'description: "X"'})
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.delete("/api/tools/claude_code/plugins/bye-plugin", headers=_auth())
        assert resp.status_code == 200
        assert resp.json()["name"] == "bye-plugin"
        assert not (pd / "bye-plugin").exists()

    def test_uninstall_missing_returns_404(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.delete("/api/tools/claude_code/plugins/no-such-plugin", headers=_auth())
        assert resp.status_code == 404

    def test_uninstall_requires_auth(self, client: TestClient) -> None:
        resp = client.delete("/api/tools/claude_code/plugins/anything")
        assert resp.status_code in (401, 403, 422)

    def test_uninstall_codex_returns_422(self, client: TestClient) -> None:
        resp = client.delete("/api/tools/codex/plugins/any-name", headers=_auth())
        assert resp.status_code == 422

    def test_uninstall_removes_from_disabled_state(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "was-disabled", {"cmd": 'description: "X"'})
        # Pre-mark as disabled
        (pd / "plugins_state.json").write_text(
            json.dumps({"disabled": ["was-disabled"]}), encoding="utf-8"
        )
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        client.delete("/api/tools/claude_code/plugins/was-disabled", headers=_auth())
        # After uninstall, the state file should no longer list it
        state = json.loads((pd / "plugins_state.json").read_text())
        assert "was-disabled" not in state.get("disabled", [])


# ---------------------------------------------------------------------------
# Tool-scoped endpoints — enable/disable (PATCH)
# ---------------------------------------------------------------------------

class TestSetToolPluginEnabled:
    def test_disable_plugin_returns_enabled_false(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "toggle-me", {"cmd": 'description: "X"'})
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.patch(
            "/api/tools/claude_code/plugins/toggle-me",
            json={"enabled": False},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert resp.json()["plugin"]["enabled"] is False

    def test_enable_plugin_returns_enabled_true(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "toggle-me", {"cmd": 'description: "X"'})
        (pd / "plugins_state.json").write_text(
            json.dumps({"disabled": ["toggle-me"]}), encoding="utf-8"
        )
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.patch(
            "/api/tools/claude_code/plugins/toggle-me",
            json={"enabled": True},
            headers=_auth(),
        )
        assert resp.status_code == 200
        assert resp.json()["plugin"]["enabled"] is True

    def test_disable_persisted_to_state_file(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "persist-test", {"cmd": 'description: "X"'})
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        client.patch(
            "/api/tools/claude_code/plugins/persist-test",
            json={"enabled": False},
            headers=_auth(),
        )
        state = json.loads((pd / "plugins_state.json").read_text())
        assert "persist-test" in state["disabled"]

    def test_enable_removes_from_state_file(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "re-enable", {"cmd": 'description: "X"'})
        (pd / "plugins_state.json").write_text(
            json.dumps({"disabled": ["re-enable"]}), encoding="utf-8"
        )
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        client.patch(
            "/api/tools/claude_code/plugins/re-enable",
            json={"enabled": True},
            headers=_auth(),
        )
        state = json.loads((pd / "plugins_state.json").read_text())
        assert "re-enable" not in state.get("disabled", [])

    def test_missing_plugin_returns_404(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.patch(
            "/api/tools/claude_code/plugins/nonexistent",
            json={"enabled": False},
            headers=_auth(),
        )
        assert resp.status_code == 404

    def test_codex_returns_422(self, client: TestClient) -> None:
        resp = client.patch(
            "/api/tools/codex/plugins/some-plugin",
            json={"enabled": False},
            headers=_auth(),
        )
        assert resp.status_code == 422

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.patch("/api/tools/claude_code/plugins/any", json={"enabled": True})
        assert resp.status_code in (401, 403, 422)


# ---------------------------------------------------------------------------
# Deprecated legacy endpoints — still work, carry deprecation header
# ---------------------------------------------------------------------------

class TestDeprecatedLegacyEndpoints:
    def test_list_returns_200(self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pd = _make_plugins_dir(tmp_path)
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_managed_cc_plugins_dir", lambda: pd)
        resp = client.get("/api/rcflow-plugins", headers=_auth())
        assert resp.status_code == 200

    def test_list_has_deprecation_header(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_managed_cc_plugins_dir", lambda: pd)
        resp = client.get("/api/rcflow-plugins", headers=_auth())
        assert "X-RCFlow-Deprecated" in resp.headers

    def test_list_returns_plugins(self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "my-tool", {"cmd": 'description: "X"'})
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_managed_cc_plugins_dir", lambda: pd)
        resp = client.get("/api/rcflow-plugins", headers=_auth())
        assert any(p["name"] == "my-tool" for p in resp.json()["plugins"])

    def test_install_returns_201(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        source_dir = tmp_path / "src-p"
        source_dir.mkdir()
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.post(
            "/api/rcflow-plugins",
            json={"source": str(source_dir), "name": "via-legacy"},
            headers=_auth(),
        )
        assert resp.status_code == 201
        assert resp.json()["plugin"]["name"] == "via-legacy"

    def test_install_has_deprecation_header(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        source_dir = tmp_path / "src-p2"
        source_dir.mkdir()
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.post(
            "/api/rcflow-plugins",
            json={"source": str(source_dir), "name": "via-legacy2"},
            headers=_auth(),
        )
        assert "X-RCFlow-Deprecated" in resp.headers

    def test_delete_returns_200(self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "to-remove", {"cmd": 'description: "X"'})
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_managed_cc_plugins_dir", lambda: pd)
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.delete("/api/rcflow-plugins/to-remove", headers=_auth())
        assert resp.status_code == 200

    def test_delete_has_deprecation_header(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pd = _make_plugins_dir(tmp_path)
        _make_plugin(pd, "del-dep", {"cmd": 'description: "X"'})
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_managed_cc_plugins_dir", lambda: pd)
        monkeypatch.setattr("src.api.routes.rcflow_plugins.get_tool_plugins_dir", lambda _: pd)
        resp = client.delete("/api/rcflow-plugins/del-dep", headers=_auth())
        assert "X-RCFlow-Deprecated" in resp.headers
