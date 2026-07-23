"""Tests for the managed CODEX_HOME config.toml MCP registration."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

import pytest

from src.services.tool_settings import ensure_codex_mcp_registration

if TYPE_CHECKING:
    from pathlib import Path

_CMD = "/opt/rcflow/.venv/bin/rcflow-mcp"


@pytest.fixture
def codex_home(tmp_path: Path) -> Path:
    home = tmp_path / "codex"
    home.mkdir()
    return home


def _config(codex_home: Path) -> str:
    return (codex_home / "config.toml").read_text()


def _parsed(codex_home: Path) -> dict:
    return tomllib.loads(_config(codex_home))


class TestRegistration:
    def test_writes_block_when_enabled(self, codex_home: Path) -> None:
        ensure_codex_mcp_registration(codex_home, True, _CMD)
        parsed = _parsed(codex_home)
        assert parsed["mcp_servers"]["rcflow"]["command"] == _CMD
        assert parsed["mcp_servers"]["rcflow"]["args"] == []

    def test_idempotent(self, codex_home: Path) -> None:
        ensure_codex_mcp_registration(codex_home, True, _CMD)
        first = _config(codex_home)
        ensure_codex_mcp_registration(codex_home, True, _CMD)
        assert _config(codex_home) == first

    def test_disabled_removes_block(self, codex_home: Path) -> None:
        ensure_codex_mcp_registration(codex_home, True, _CMD)
        ensure_codex_mcp_registration(codex_home, False, _CMD)
        assert "rcflow" not in _parsed(codex_home).get("mcp_servers", {})

    def test_disabled_on_missing_file_writes_nothing(self, codex_home: Path) -> None:
        ensure_codex_mcp_registration(codex_home, False, _CMD)
        assert not (codex_home / "config.toml").exists()

    def test_command_path_updated_in_place(self, codex_home: Path) -> None:
        ensure_codex_mcp_registration(codex_home, True, _CMD)
        ensure_codex_mcp_registration(codex_home, True, "/new/path/rcflow-mcp")
        parsed = _parsed(codex_home)
        assert parsed["mcp_servers"]["rcflow"]["command"] == "/new/path/rcflow-mcp"
        # Only one block present.
        assert _config(codex_home).count("[mcp_servers.rcflow]") == 1

    def test_windows_path_escaped(self, codex_home: Path) -> None:
        win_cmd = "C:\\Users\\Fox\\rcflow\\Scripts\\rcflow-mcp.exe"
        ensure_codex_mcp_registration(codex_home, True, win_cmd)
        assert _parsed(codex_home)["mcp_servers"]["rcflow"]["command"] == win_cmd


class TestUserContentPreserved:
    def test_foreign_entries_survive(self, codex_home: Path) -> None:
        (codex_home / "config.toml").write_text('model = "o3"\n\n[mcp_servers.other]\ncommand = "/bin/other"\n')
        ensure_codex_mcp_registration(codex_home, True, _CMD)
        parsed = _parsed(codex_home)
        assert parsed["model"] == "o3"
        assert parsed["mcp_servers"]["other"]["command"] == "/bin/other"
        assert parsed["mcp_servers"]["rcflow"]["command"] == _CMD

        ensure_codex_mcp_registration(codex_home, False, _CMD)
        parsed = _parsed(codex_home)
        assert parsed["model"] == "o3"
        assert parsed["mcp_servers"]["other"]["command"] == "/bin/other"
        assert "rcflow" not in parsed["mcp_servers"]

    def test_user_owned_rcflow_entry_untouched(self, codex_home: Path) -> None:
        """A hand-written [mcp_servers.rcflow] outside our markers is never modified."""
        original = '[mcp_servers.rcflow]\ncommand = "/home/fox/custom-rcflow-mcp"\n'
        (codex_home / "config.toml").write_text(original)
        ensure_codex_mcp_registration(codex_home, True, _CMD)
        assert _config(codex_home) == original
        ensure_codex_mcp_registration(codex_home, False, _CMD)
        assert _config(codex_home) == original

    def test_invalid_toml_left_alone(self, codex_home: Path) -> None:
        broken = "this is [not valid toml\n"
        (codex_home / "config.toml").write_text(broken)
        ensure_codex_mcp_registration(codex_home, True, _CMD)
        assert _config(codex_home) == broken
