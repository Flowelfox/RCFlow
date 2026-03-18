"""Tests for GET /api/slash-commands."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

API_KEY = "test-api-key"


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
        assert {"clear", "new", "help", "pause", "resume"} == rcflow

    def test_claude_code_builtins_present(self, client: TestClient) -> None:
        resp = client.get("/api/slash-commands", headers=_auth())
        commands = resp.json()["commands"]
        cc_builtin_names = {c["name"] for c in commands if c["source"] == "claude_code_builtin"}
        assert "compact" in cc_builtin_names
        assert "cost" in cc_builtin_names
        assert "doctor" in cc_builtin_names

    def test_sources_are_valid(self, client: TestClient) -> None:
        valid_sources = {"rcflow", "claude_code_builtin", "claude_code_user", "claude_code_project"}
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
        # The test environment is unlikely to have ~/.claude/commands set up exactly,
        # but the endpoint must not raise a 500 regardless.
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

    def test_multiple_user_skills(
        self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        commands_dir = tmp_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        for name, desc in [("deploy", "Deploy to production"), ("lint", "Run linter"), ("test-all", "Run all tests")]:
            (commands_dir / f"{name}.md").write_text(
                f"---\ndescription: {desc}\n---\n", encoding="utf-8"
            )
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        resp = client.get("/api/slash-commands", headers=_auth())
        user_cmds = {c["name"]: c["description"] for c in resp.json()["commands"] if c["source"] == "claude_code_user"}
        assert user_cmds["deploy"] == "Deploy to production"
        assert user_cmds["lint"] == "Run linter"
        assert user_cmds["test-all"] == "Run all tests"
