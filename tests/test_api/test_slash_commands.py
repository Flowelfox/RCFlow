"""Tests for GET /api/slash-commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Generator

    from fastapi import FastAPI

import src.api.routes.slash_commands as _sc_module

API_KEY = "test-api-key"


@pytest.fixture(autouse=True)
def reset_cc_builtins_cache(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    """Reset the in-process CC builtins cache and block subprocess calls between tests.

    Clears the cache so that monkeypatches affecting the fetch path take effect,
    and patches out the two entry points that spawn real subprocesses — the disk-
    cache loader (calls ``claude --version`` on every invocation) and ``shutil.which``
    — so that tests which don't explicitly need the live-fetch path don't pay the
    3-5 s per-test penalty of spawning the ``claude`` binary.

    Tests in ``TestCCBuiltinDescriptionsFromClaude`` override these patches with
    their own ``monkeypatch.setattr`` calls; because monkeypatch applies patches
    in order and the later call wins, the test-specific behaviour takes precedence.
    """
    _sc_module._cc_builtins_cache = None
    # Default: no disk cache, no binary — falls through to the hard-coded fallback
    # instantly.  Individual tests that need the live-fetch or disk-cache path
    # override these with their own monkeypatch.setattr() calls.
    monkeypatch.setattr("src.api.routes.slash_commands._load_disk_cache", lambda: None)
    monkeypatch.setattr("src.api.routes.slash_commands.shutil.which", lambda _: None)
    yield
    _sc_module._cc_builtins_cache = None


@pytest.fixture
def client(test_app: FastAPI) -> TestClient:
    return TestClient(test_app)


def _auth() -> dict[str, str]:
    return {"X-API-Key": API_KEY}


class TestSlashCommandsEndpoint:
    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/slash-commands", headers=_auth())
        assert resp.status_code == 200

    def test_requires_auth(self, client: TestClient) -> None:
        resp = client.get("/api/slash-commands")
        assert resp.status_code in (401, 403, 422)

    def test_response_shape(self, client: TestClient) -> None:
        resp = client.get("/api/slash-commands", headers=_auth())
        body: dict[str, Any] = resp.json()
        assert "commands" in body
        assert isinstance(body["commands"], list)
        for cmd in body["commands"]:
            assert "name" in cmd
            assert "description" in cmd
            assert "source" in cmd

    def test_rcflow_commands_present(self, client: TestClient) -> None:
        resp = client.get("/api/slash-commands", headers=_auth())
        commands = resp.json()["commands"]
        rcflow = {c["name"] for c in commands if c["source"] == "rcflow"}
        assert {"clear", "new", "help", "pause", "resume", "plugins"} == rcflow

    def test_btw_is_claude_code_builtin(self, client: TestClient) -> None:
        resp = client.get("/api/slash-commands", headers=_auth())
        commands = resp.json()["commands"]
        btw = next((c for c in commands if c["name"] == "btw"), None)
        assert btw is not None
        assert btw["source"] == "claude_code_builtin"
        assert btw["description"] != ""

    def test_btw_filter_match(self, client: TestClient) -> None:
        resp = client.get("/api/slash-commands", params={"q": "btw"}, headers=_auth())
        names = {c["name"] for c in resp.json()["commands"]}
        assert "btw" in names

    def test_claude_code_builtins_present(self, client: TestClient) -> None:
        resp = client.get("/api/slash-commands", headers=_auth())
        commands = resp.json()["commands"]
        cc_builtin_names = {c["name"] for c in commands if c["source"] == "claude_code_builtin"}
        assert "compact" in cc_builtin_names
        assert "cost" in cc_builtin_names
        assert "doctor" in cc_builtin_names

    def test_sources_are_valid(self, client: TestClient) -> None:
        valid_sources = {
            "rcflow",
            "claude_code_builtin",
            "claude_code_user",
            "claude_code_project",
            "claude_code_plugin",
            "rcflow_plugin",
        }
        resp = client.get("/api/slash-commands", headers=_auth())
        for cmd in resp.json()["commands"]:
            assert cmd["source"] in valid_sources


class TestSlashCommandsFilter:
    def test_filter_by_exact_name(self, client: TestClient) -> None:
        resp = client.get("/api/slash-commands", params={"q": "clear"}, headers=_auth())
        commands = resp.json()["commands"]
        assert len(commands) >= 1
        assert all("clear" in c["name"] for c in commands)

    def test_filter_is_case_insensitive(self, client: TestClient) -> None:
        resp_lower = client.get("/api/slash-commands", params={"q": "clear"}, headers=_auth())
        resp_upper = client.get("/api/slash-commands", params={"q": "CLEAR"}, headers=_auth())
        names_lower = {c["name"] for c in resp_lower.json()["commands"]}
        names_upper = {c["name"] for c in resp_upper.json()["commands"]}
        assert names_lower == names_upper

    def test_filter_no_match_returns_empty(self, client: TestClient) -> None:
        resp = client.get("/api/slash-commands", params={"q": "zzz_no_match_zzz"}, headers=_auth())
        assert resp.json()["commands"] == []

    def test_filter_substring_match(self, client: TestClient) -> None:
        # "com" should match "compact" (claude_code_builtin)
        resp = client.get("/api/slash-commands", params={"q": "com"}, headers=_auth())
        names = {c["name"] for c in resp.json()["commands"]}
        assert "compact" in names

    def test_empty_q_returns_all(self, client: TestClient) -> None:
        resp_no_q = client.get("/api/slash-commands", headers=_auth())
        resp_empty_q = client.get("/api/slash-commands", params={"q": ""}, headers=_auth())
        assert resp_no_q.json()["commands"] == resp_empty_q.json()["commands"]


class TestSlashCommandsUserLevelSkills:
    def test_missing_commands_dir_does_not_crash(self, client: TestClient, tmp_path: Path) -> None:
        """Endpoint should return gracefully when ~/.claude/commands/ doesn't exist."""
        resp = client.get("/api/slash-commands", headers=_auth())
        assert resp.status_code == 200

    def test_user_commands_loaded_from_md_files(
        self, client: TestClient, test_app: FastAPI, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Commands are read from .md files in the user commands directory."""
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "my-skill.md").write_text(
            "---\ndescription: My custom skill\nallowed-tools: Bash\n---\n\nDo something.",
            encoding="utf-8",
        )

        # Patch Path.home() to return tmp_path so the endpoint finds our fake commands dir
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        resp = client.get("/api/slash-commands", headers=_auth())
        assert resp.status_code == 200
        user_cmds = [c for c in resp.json()["commands"] if c["source"] == "claude_code_user"]
        assert any(c["name"] == "my-skill" and c["description"] == "My custom skill" for c in user_cmds)

    def test_md_file_without_description_included_with_empty_description(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "no-desc.md").write_text(
            "---\nallowed-tools: Bash\n---\n\nNo description here.",
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        resp = client.get("/api/slash-commands", headers=_auth())
        user_cmds = [c for c in resp.json()["commands"] if c["source"] == "claude_code_user"]
        assert any(c["name"] == "no-desc" and c["description"] == "" for c in user_cmds)

    def test_zone_identifier_files_are_skipped(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "real-skill.md").write_text("---\ndescription: Real\n---\n", encoding="utf-8")
        # Windows Zone.Identifier sidecar file — must be ignored
        (commands_dir / "real-skill.md:Zone.Identifier").write_text("", encoding="utf-8")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        resp = client.get("/api/slash-commands", headers=_auth())
        user_names = [c["name"] for c in resp.json()["commands"] if c["source"] == "claude_code_user"]
        assert "real-skill" in user_names
        assert "real-skill.md:Zone" not in " ".join(user_names)

    def test_multiple_user_skills(self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        for name, desc in [("deploy", "Deploy to production"), ("lint", "Run linter"), ("test-all", "Run all tests")]:
            (commands_dir / f"{name}.md").write_text(f"---\ndescription: {desc}\n---\n", encoding="utf-8")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        resp = client.get("/api/slash-commands", headers=_auth())
        user_cmds = {c["name"]: c["description"] for c in resp.json()["commands"] if c["source"] == "claude_code_user"}
        assert user_cmds["deploy"] == "Deploy to production"
        assert user_cmds["lint"] == "Run linter"
        assert user_cmds["test-all"] == "Run all tests"


class TestCCBuiltinDescriptionsFromClaude:
    """Tests for the live-fetch path that sources descriptions from Claude."""

    def test_fallback_used_when_binary_not_found(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """When claude binary is not on PATH, hard-coded fallback is served."""
        monkeypatch.setattr("src.api.routes.slash_commands.shutil.which", lambda _: None)

        resp = client.get("/api/slash-commands", headers=_auth())
        assert resp.status_code == 200
        builtins = [c for c in resp.json()["commands"] if c["source"] == "claude_code_builtin"]
        assert len(builtins) > 0
        assert all(c["description"] != "" for c in builtins)

    def test_live_descriptions_used_when_fetch_succeeds(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When claude -p returns valid JSON, those descriptions replace the fallback."""
        live_json = (
            '{"help": "Show live help", "clear": "Live clear", "compact": "Live compact",'
            ' "cost": "Live cost", "resume": "Live resume", "init": "Live init",'
            ' "bug": "Live bug", "pr-comments": "Live pr-comments",'
            ' "permissions": "Live permissions", "doctor": "Live doctor",'
            ' "vim": "Live vim", "btw": "Live btw"}'
        )

        async def fake_fetch(binary: str) -> list[dict[str, str]]:
            import json  # noqa: PLC0415

            data = json.loads(live_json)
            return [{"name": k, "description": v, "source": "claude_code_builtin"} for k, v in data.items()]

        monkeypatch.setattr("src.api.routes.slash_commands.shutil.which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr("src.api.routes.slash_commands._fetch_from_claude", fake_fetch)
        monkeypatch.setattr("src.api.routes.slash_commands._load_disk_cache", lambda: None)
        monkeypatch.setattr("src.api.routes.slash_commands._save_disk_cache", lambda *_: None)
        monkeypatch.setattr("src.api.routes.slash_commands._get_cc_version", lambda: "99.0.0")

        resp = client.get("/api/slash-commands", headers=_auth())
        assert resp.status_code == 200
        builtins = {
            c["name"]: c["description"] for c in resp.json()["commands"] if c["source"] == "claude_code_builtin"
        }
        assert builtins["compact"] == "Live compact"
        assert builtins["btw"] == "Live btw"

    def test_disk_cache_used_without_subprocess_call(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """When a valid disk cache exists, _fetch_from_claude is never called."""
        cached_commands = [
            {"name": "help", "description": "Cached help", "source": "claude_code_builtin"},
            {"name": "compact", "description": "Cached compact", "source": "claude_code_builtin"},
            {"name": "btw", "description": "Cached btw", "source": "claude_code_builtin"},
        ]
        fetch_called = []

        async def should_not_be_called(binary: str) -> list[dict[str, str]]:
            fetch_called.append(True)
            return []

        monkeypatch.setattr("src.api.routes.slash_commands._load_disk_cache", lambda: cached_commands)
        monkeypatch.setattr("src.api.routes.slash_commands._fetch_from_claude", should_not_be_called)

        resp = client.get("/api/slash-commands", headers=_auth())
        assert resp.status_code == 200
        assert not fetch_called, "_fetch_from_claude should not be called when disk cache is valid"
        builtins = {
            c["name"]: c["description"] for c in resp.json()["commands"] if c["source"] == "claude_code_builtin"
        }
        assert builtins.get("compact") == "Cached compact"

    def test_fallback_when_fetch_returns_empty(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the subprocess returns no commands, hard-coded fallback is used."""

        async def empty_fetch(binary: str) -> list[dict[str, str]]:
            return []

        monkeypatch.setattr("src.api.routes.slash_commands.shutil.which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr("src.api.routes.slash_commands._fetch_from_claude", empty_fetch)
        monkeypatch.setattr("src.api.routes.slash_commands._load_disk_cache", lambda: None)
        monkeypatch.setattr("src.api.routes.slash_commands._save_disk_cache", lambda *_: None)

        resp = client.get("/api/slash-commands", headers=_auth())
        assert resp.status_code == 200
        builtins = [c for c in resp.json()["commands"] if c["source"] == "claude_code_builtin"]
        # Fallback list must contain at least the well-known commands
        names = {c["name"] for c in builtins}
        assert "compact" in names
        assert "btw" in names

    def test_in_process_cache_prevents_second_fetch(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """After the first request the in-process cache is populated; no further
        subprocess calls are made on subsequent requests."""
        call_count = []

        async def counting_fetch(binary: str) -> list[dict[str, str]]:
            call_count.append(1)
            return [{"name": "compact", "description": "From fetch", "source": "claude_code_builtin"}]

        monkeypatch.setattr("src.api.routes.slash_commands.shutil.which", lambda _: "/usr/bin/claude")
        monkeypatch.setattr("src.api.routes.slash_commands._fetch_from_claude", counting_fetch)
        monkeypatch.setattr("src.api.routes.slash_commands._load_disk_cache", lambda: None)
        monkeypatch.setattr("src.api.routes.slash_commands._save_disk_cache", lambda *_: None)
        monkeypatch.setattr("src.api.routes.slash_commands._get_cc_version", lambda: "1.0.0")

        client.get("/api/slash-commands", headers=_auth())
        client.get("/api/slash-commands", headers=_auth())

        assert len(call_count) == 1, "Fetch should only happen once; cache should serve subsequent requests"


class TestPluginSkillCommands:
    """Tests for Claude Code plugin command enumeration."""

    def _make_plugin(
        self,
        tmp_path: Path,
        plugin_key: str,
        commands: dict[str, str],  # name → frontmatter body (everything inside the --- blocks)
    ) -> tuple[Path, Path]:
        """Create a fake plugin directory and return (install_path, plugins_root)."""
        short_name = plugin_key.split("@")[0]
        install_path = tmp_path / "plugins" / "cache" / short_name / "1.0.0"
        commands_dir = install_path / "commands"
        commands_dir.mkdir(parents=True)
        for name, frontmatter in commands.items():
            (commands_dir / f"{name}.md").write_text(
                f"---\n{frontmatter}\n---\n\nBody text.",
                encoding="utf-8",
            )
        return install_path, tmp_path / "plugins"

    def _make_registry(
        self,
        tmp_path: Path,
        plugins: dict[str, Path],  # key → installPath
        enabled: dict[str, bool],
    ) -> None:
        """Write installed_plugins.json and settings.json under tmp_path/.claude/."""
        dot_claude = tmp_path / ".claude"
        dot_claude.mkdir(parents=True, exist_ok=True)
        plugins_dir = dot_claude / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)

        registry: dict[str, list[dict]] = {}
        for key, path in plugins.items():
            registry[key] = [{"scope": "user", "installPath": str(path), "version": "1.0.0"}]
        (plugins_dir / "installed_plugins.json").write_text(
            json.dumps({"version": 2, "plugins": registry}),
            encoding="utf-8",
        )
        settings: dict = {"enabledPlugins": enabled}
        (dot_claude / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    def test_plugin_commands_are_returned(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Commands from an enabled plugin appear with source=claude_code_plugin."""
        plugin_key = "my-plugin@marketplace"
        install_path, _ = self._make_plugin(tmp_path, plugin_key, {"do-thing": 'description: "Do the thing"'})
        self._make_registry(tmp_path, {plugin_key: install_path}, {plugin_key: True})
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / ".claude" / ".."))
        monkeypatch.setattr(
            "src.api.routes.slash_commands._INSTALLED_PLUGINS_FILE",
            tmp_path / ".claude" / "plugins" / "installed_plugins.json",
        )
        monkeypatch.setattr("src.api.routes.slash_commands._CC_SETTINGS_FILE", tmp_path / ".claude" / "settings.json")

        resp = client.get("/api/slash-commands", headers=_auth())
        assert resp.status_code == 200
        plugin_cmds = [c for c in resp.json()["commands"] if c["source"] == "claude_code_plugin"]
        assert any(c["name"] == "do-thing" for c in plugin_cmds)

    def test_plugin_description_quotes_are_stripped(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Descriptions wrapped in quotes are returned without the quotes."""
        plugin_key = "my-plugin@marketplace"
        install_path, _ = self._make_plugin(tmp_path, plugin_key, {"skill": 'description: "Quoted description"'})
        self._make_registry(tmp_path, {plugin_key: install_path}, {plugin_key: True})
        monkeypatch.setattr(
            "src.api.routes.slash_commands._INSTALLED_PLUGINS_FILE",
            tmp_path / ".claude" / "plugins" / "installed_plugins.json",
        )
        monkeypatch.setattr("src.api.routes.slash_commands._CC_SETTINGS_FILE", tmp_path / ".claude" / "settings.json")

        resp = client.get("/api/slash-commands", headers=_auth())
        plugin_cmds = {c["name"]: c for c in resp.json()["commands"] if c["source"] == "claude_code_plugin"}
        assert plugin_cmds["skill"]["description"] == "Quoted description"

    def test_hidden_commands_are_excluded(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Commands with hide-from-slash-command-tool: true are not returned."""
        plugin_key = "my-plugin@marketplace"
        install_path, _ = self._make_plugin(
            tmp_path,
            plugin_key,
            {
                "visible": 'description: "I am visible"',
                "hidden": 'description: "I am hidden"\nhide-from-slash-command-tool: "true"',
            },
        )
        self._make_registry(tmp_path, {plugin_key: install_path}, {plugin_key: True})
        monkeypatch.setattr(
            "src.api.routes.slash_commands._INSTALLED_PLUGINS_FILE",
            tmp_path / ".claude" / "plugins" / "installed_plugins.json",
        )
        monkeypatch.setattr("src.api.routes.slash_commands._CC_SETTINGS_FILE", tmp_path / ".claude" / "settings.json")

        resp = client.get("/api/slash-commands", headers=_auth())
        plugin_names = {c["name"] for c in resp.json()["commands"] if c["source"] == "claude_code_plugin"}
        assert "visible" in plugin_names
        assert "hidden" not in plugin_names

    def test_disabled_plugin_commands_not_returned(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Commands from a plugin listed as enabled=false are omitted."""
        plugin_key = "disabled-plugin@marketplace"
        install_path, _ = self._make_plugin(tmp_path, plugin_key, {"secret": 'description: "Should not appear"'})
        self._make_registry(tmp_path, {plugin_key: install_path}, {plugin_key: False})
        monkeypatch.setattr(
            "src.api.routes.slash_commands._INSTALLED_PLUGINS_FILE",
            tmp_path / ".claude" / "plugins" / "installed_plugins.json",
        )
        monkeypatch.setattr("src.api.routes.slash_commands._CC_SETTINGS_FILE", tmp_path / ".claude" / "settings.json")

        resp = client.get("/api/slash-commands", headers=_auth())
        plugin_cmds = [c for c in resp.json()["commands"] if c["source"] == "claude_code_plugin"]
        assert not any(c["name"] == "secret" for c in plugin_cmds)

    def test_plugin_field_present_on_plugin_commands(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each plugin command carries a 'plugin' field with the short plugin name."""
        plugin_key = "awesome-tool@marketplace"
        install_path, _ = self._make_plugin(tmp_path, plugin_key, {"run": 'description: "Run it"'})
        self._make_registry(tmp_path, {plugin_key: install_path}, {plugin_key: True})
        monkeypatch.setattr(
            "src.api.routes.slash_commands._INSTALLED_PLUGINS_FILE",
            tmp_path / ".claude" / "plugins" / "installed_plugins.json",
        )
        monkeypatch.setattr("src.api.routes.slash_commands._CC_SETTINGS_FILE", tmp_path / ".claude" / "settings.json")

        resp = client.get("/api/slash-commands", headers=_auth())
        plugin_cmds = [c for c in resp.json()["commands"] if c["source"] == "claude_code_plugin"]
        run_cmd = next((c for c in plugin_cmds if c["name"] == "run"), None)
        assert run_cmd is not None
        assert run_cmd.get("plugin") == "awesome-tool"

    def test_missing_installed_plugins_file_does_not_crash(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Endpoint should return gracefully when installed_plugins.json doesn't exist."""
        monkeypatch.setattr(
            "src.api.routes.slash_commands._INSTALLED_PLUGINS_FILE", tmp_path / "nonexistent" / "installed_plugins.json"
        )
        monkeypatch.setattr(
            "src.api.routes.slash_commands._CC_SETTINGS_FILE", tmp_path / "nonexistent" / "settings.json"
        )

        resp = client.get("/api/slash-commands", headers=_auth())
        assert resp.status_code == 200

    def test_zone_identifier_files_skipped_in_plugin_commands(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Windows Zone.Identifier sidecar files in plugin command dirs are ignored."""
        plugin_key = "my-plugin@marketplace"
        install_path, _ = self._make_plugin(tmp_path, plugin_key, {"real": 'description: "Real command"'})
        # Simulate a sidecar file
        sidecar = install_path / "commands" / "real.md:Zone.Identifier"
        sidecar.write_text("", encoding="utf-8")
        self._make_registry(tmp_path, {plugin_key: install_path}, {plugin_key: True})
        monkeypatch.setattr(
            "src.api.routes.slash_commands._INSTALLED_PLUGINS_FILE",
            tmp_path / ".claude" / "plugins" / "installed_plugins.json",
        )
        monkeypatch.setattr("src.api.routes.slash_commands._CC_SETTINGS_FILE", tmp_path / ".claude" / "settings.json")

        resp = client.get("/api/slash-commands", headers=_auth())
        plugin_names = [c["name"] for c in resp.json()["commands"] if c["source"] == "claude_code_plugin"]
        assert "real" in plugin_names
        assert any("Zone" in n for n in plugin_names) is False


class TestRCFlowManagedPlugins:
    """Tests for RCFlow-managed Claude Code plugin commands."""

    def _make_rcflow_plugin(self, plugins_dir: Path, plugin_name: str, commands: dict[str, str]) -> Path:
        """Create a fake RCFlow-managed plugin directory and return the plugin path."""
        plugin_dir = plugins_dir / plugin_name / "commands"
        plugin_dir.mkdir(parents=True)
        for name, frontmatter in commands.items():
            (plugin_dir / f"{name}.md").write_text(
                f"---\n{frontmatter}\n---\n\nBody text.",
                encoding="utf-8",
            )
        return plugins_dir / plugin_name

    def test_rcflow_plugin_commands_are_returned(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Commands from a plugin in the RCFlow-managed plugins dir appear with source=rcflow_plugin."""
        plugins_dir = tmp_path / "plugins"
        self._make_rcflow_plugin(plugins_dir, "my-tool", {"do-thing": 'description: "Do the thing"'})
        monkeypatch.setattr("src.api.routes.slash_commands.get_managed_cc_plugins_dir", lambda: plugins_dir)

        resp = client.get("/api/slash-commands", headers=_auth())
        assert resp.status_code == 200
        rcflow_plugin_cmds = [c for c in resp.json()["commands"] if c["source"] == "rcflow_plugin"]
        assert any(c["name"] == "do-thing" for c in rcflow_plugin_cmds)

    def test_rcflow_plugin_has_plugin_field(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The 'plugin' field reflects the plugin directory name."""
        plugins_dir = tmp_path / "plugins"
        self._make_rcflow_plugin(plugins_dir, "my-tool", {"skill": 'description: "A skill"'})
        monkeypatch.setattr("src.api.routes.slash_commands.get_managed_cc_plugins_dir", lambda: plugins_dir)

        resp = client.get("/api/slash-commands", headers=_auth())
        rcflow_plugin_cmds = {c["name"]: c for c in resp.json()["commands"] if c["source"] == "rcflow_plugin"}
        assert rcflow_plugin_cmds["skill"]["plugin"] == "my-tool"

    def test_rcflow_plugin_hidden_commands_excluded(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Commands with hide-from-slash-command-tool: true are excluded."""
        plugins_dir = tmp_path / "plugins"
        self._make_rcflow_plugin(
            plugins_dir,
            "my-tool",
            {
                "visible": 'description: "Visible"',
                "hidden": 'description: "Hidden"\nhide-from-slash-command-tool: "true"',
            },
        )
        monkeypatch.setattr("src.api.routes.slash_commands.get_managed_cc_plugins_dir", lambda: plugins_dir)

        resp = client.get("/api/slash-commands", headers=_auth())
        names = {c["name"] for c in resp.json()["commands"] if c["source"] == "rcflow_plugin"}
        assert "visible" in names
        assert "hidden" not in names

    def test_missing_rcflow_plugins_dir_does_not_crash(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Endpoint returns gracefully when the RCFlow plugins dir doesn't exist yet."""
        monkeypatch.setattr(
            "src.api.routes.slash_commands.get_managed_cc_plugins_dir",
            lambda: tmp_path / "nonexistent" / "plugins",
        )

        resp = client.get("/api/slash-commands", headers=_auth())
        assert resp.status_code == 200

    def test_multiple_rcflow_plugins(self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Commands from multiple plugin subdirectories are all returned."""
        plugins_dir = tmp_path / "plugins"
        self._make_rcflow_plugin(plugins_dir, "alpha", {"alpha-cmd": 'description: "Alpha"'})
        self._make_rcflow_plugin(plugins_dir, "beta", {"beta-cmd": 'description: "Beta"'})
        monkeypatch.setattr("src.api.routes.slash_commands.get_managed_cc_plugins_dir", lambda: plugins_dir)

        resp = client.get("/api/slash-commands", headers=_auth())
        names = {c["name"] for c in resp.json()["commands"] if c["source"] == "rcflow_plugin"}
        assert "alpha-cmd" in names
        assert "beta-cmd" in names

    def test_disabled_rcflow_plugin_excluded(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Commands from a disabled RCFlow-managed plugin are not returned."""
        plugins_dir = tmp_path / "plugins"
        self._make_rcflow_plugin(plugins_dir, "disabled-plugin", {"secret-cmd": 'description: "Should not appear"'})
        # Write plugins_state.json marking this plugin as disabled
        (plugins_dir / "plugins_state.json").write_text(json.dumps({"disabled": ["disabled-plugin"]}), encoding="utf-8")
        monkeypatch.setattr("src.api.routes.slash_commands.get_managed_cc_plugins_dir", lambda: plugins_dir)

        resp = client.get("/api/slash-commands", headers=_auth())
        assert resp.status_code == 200
        names = {c["name"] for c in resp.json()["commands"] if c["source"] == "rcflow_plugin"}
        assert "secret-cmd" not in names

    def test_enabled_rcflow_plugin_included(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Commands from an enabled plugin are returned even when a state file exists."""
        plugins_dir = tmp_path / "plugins"
        self._make_rcflow_plugin(plugins_dir, "enabled-plugin", {"good-cmd": 'description: "Should appear"'})
        self._make_rcflow_plugin(plugins_dir, "off-plugin", {"bad-cmd": 'description: "Should NOT appear"'})
        (plugins_dir / "plugins_state.json").write_text(json.dumps({"disabled": ["off-plugin"]}), encoding="utf-8")
        monkeypatch.setattr("src.api.routes.slash_commands.get_managed_cc_plugins_dir", lambda: plugins_dir)

        resp = client.get("/api/slash-commands", headers=_auth())
        names = {c["name"] for c in resp.json()["commands"] if c["source"] == "rcflow_plugin"}
        assert "good-cmd" in names
        assert "bad-cmd" not in names

    def test_plugins_command_description_updated(self, client: TestClient) -> None:
        """The /plugins RCFlow command description should reflect the new navigation behaviour."""
        resp = client.get("/api/slash-commands", headers=_auth())
        plugins_cmd = next(
            (c for c in resp.json()["commands"] if c["name"] == "plugins" and c["source"] == "rcflow"),
            None,
        )
        assert plugins_cmd is not None
        assert "agent" in plugins_cmd["description"].lower()
