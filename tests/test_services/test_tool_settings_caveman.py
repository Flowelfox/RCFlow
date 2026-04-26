"""Tests for caveman mode in ToolSettingsManager."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from src.config import CONFIG_OPTIONS
from src.services.tool_settings import (
    _CAVEMAN_CLAUDE_MD_TEXT,
    ToolSettingsManager,
    _sync_caveman_mode,
)


@pytest.fixture
def manager(tmp_path: Path) -> ToolSettingsManager:
    return ToolSettingsManager(base_dir=tmp_path)


# ---------------------------------------------------------------------------
# _sync_caveman_mode — Claude Code
# ---------------------------------------------------------------------------


class TestSyncCavemanClaudeCode:
    def test_enable_writes_claude_md(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "claude-code" / "config"
        config_dir.mkdir(parents=True)
        _sync_caveman_mode("claude_code", {"caveman_mode": True}, config_dir)
        target = config_dir / "CLAUDE.md"
        assert target.is_file()
        assert target.read_text(encoding="utf-8") == _CAVEMAN_CLAUDE_MD_TEXT

    def test_disable_deletes_claude_md(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "claude-code" / "config"
        config_dir.mkdir(parents=True)
        target = config_dir / "CLAUDE.md"
        target.write_text("old content")
        _sync_caveman_mode("claude_code", {"caveman_mode": False}, config_dir)
        assert not target.exists()

    def test_disable_when_file_missing_is_noop(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "claude-code" / "config"
        config_dir.mkdir(parents=True)
        _sync_caveman_mode("claude_code", {"caveman_mode": False}, config_dir)
        assert not (config_dir / "CLAUDE.md").exists()

    def test_enable_creates_parent_dirs(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "new" / "nested" / "dir"
        _sync_caveman_mode("claude_code", {"caveman_mode": True}, config_dir)
        assert (config_dir / "CLAUDE.md").is_file()


class TestSyncCavemanCodex:
    def test_codex_enable_is_noop(self, tmp_path: Path) -> None:
        """Codex caveman is unverified — _sync_caveman_mode should be a no-op."""
        _sync_caveman_mode("codex", {"caveman_mode": True}, tmp_path)
        # No files created
        assert list(tmp_path.iterdir()) == []


class TestSyncCavemanOpenCode:
    def test_opencode_enable_is_noop(self, tmp_path: Path) -> None:
        """OpenCode caveman is unverified — _sync_caveman_mode should be a no-op."""
        _sync_caveman_mode("opencode", {"caveman_mode": True}, tmp_path)
        assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# ToolSettingsManager integration
# ---------------------------------------------------------------------------


class TestCavemanSettingsIntegration:
    def test_caveman_mode_not_in_json_file(self, manager: ToolSettingsManager) -> None:
        """caveman_mode must be stripped from the tool config JSON."""
        manager.update_settings("claude_code", {"caveman_mode": True})
        settings = manager.get_settings("claude_code")
        assert "caveman_mode" not in settings

    def test_caveman_mode_reflected_in_schema_output(self, manager: ToolSettingsManager) -> None:
        """get_settings_with_schema should derive caveman_mode from CLAUDE.md."""
        manager.update_settings("claude_code", {"caveman_mode": True})
        result = manager.get_settings_with_schema("claude_code")
        caveman_field = next(f for f in result["fields"] if f["key"] == "caveman_mode")
        assert caveman_field["value"] is True

    def test_disable_caveman_removes_file_and_reports_false(self, manager: ToolSettingsManager) -> None:
        manager.update_settings("claude_code", {"caveman_mode": True})
        manager.update_settings("claude_code", {"caveman_mode": False})
        result = manager.get_settings_with_schema("claude_code")
        caveman_field = next(f for f in result["fields"] if f["key"] == "caveman_mode")
        assert caveman_field["value"] is False

    def test_other_settings_preserved_after_caveman_toggle(self, manager: ToolSettingsManager) -> None:
        """Enabling caveman should not interfere with other settings."""
        manager.update_settings("claude_code", {"max_turns": "50"})
        manager.update_settings("claude_code", {"caveman_mode": True})
        settings = manager.get_settings("claude_code")
        assert settings["max_turns"] == "50"
        assert "caveman_mode" not in settings

    def test_caveman_schema_field_present_for_all_tools(self, manager: ToolSettingsManager) -> None:
        for tool in ("claude_code", "codex", "opencode"):
            result = manager.get_settings_with_schema(tool)
            keys = {f["key"] for f in result["fields"]}
            assert "caveman_mode" in keys

    def test_unrelated_claude_md_does_not_report_enabled(self, manager: ToolSettingsManager) -> None:
        """A manually-created CLAUDE.md with different content must not show as enabled."""
        config_dir = manager.get_config_dir("claude_code")
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "CLAUDE.md").write_text("# My custom instructions\n")
        result = manager.get_settings_with_schema("claude_code")
        caveman_field = next(f for f in result["fields"] if f["key"] == "caveman_mode")
        assert caveman_field["value"] is False


# ---------------------------------------------------------------------------
# ToolSettingsManager.is_caveman_active
# ---------------------------------------------------------------------------


class TestIsCavemanActive:
    def test_returns_true_when_active(self, manager: ToolSettingsManager) -> None:
        manager.update_settings("claude_code", {"caveman_mode": True})
        assert manager.is_caveman_active("claude_code") is True

    def test_returns_false_when_inactive(self, manager: ToolSettingsManager) -> None:
        assert manager.is_caveman_active("claude_code") is False

    def test_returns_false_after_disable(self, manager: ToolSettingsManager) -> None:
        manager.update_settings("claude_code", {"caveman_mode": True})
        manager.update_settings("claude_code", {"caveman_mode": False})
        assert manager.is_caveman_active("claude_code") is False

    def test_codex_always_false(self, manager: ToolSettingsManager) -> None:
        assert manager.is_caveman_active("codex") is False

    def test_opencode_always_false(self, manager: ToolSettingsManager) -> None:
        assert manager.is_caveman_active("opencode") is False


# ---------------------------------------------------------------------------
# CONFIG_OPTIONS schema validation
# ---------------------------------------------------------------------------


class TestConfigOptionsCaveman:
    def test_caveman_mode_in_config_options(self) -> None:
        keys = {opt["key"] for opt in CONFIG_OPTIONS}
        assert "CAVEMAN_MODE" in keys
        assert "CAVEMAN_LEVEL" in keys

    def test_caveman_level_visible_when_mode_true(self) -> None:
        level_opt = next(o for o in CONFIG_OPTIONS if o["key"] == "CAVEMAN_LEVEL")
        assert level_opt["visible_when"] == {"key": "CAVEMAN_MODE", "value": "true"}

    def test_caveman_mode_not_restart_required(self) -> None:
        for key in ("CAVEMAN_MODE", "CAVEMAN_LEVEL"):
            opt = next(o for o in CONFIG_OPTIONS if o["key"] == key)
            assert opt["restart_required"] is False
