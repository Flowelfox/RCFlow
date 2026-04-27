"""Behavioural tests for ``_populate_missing_defaults``.

Covers the rule that install-relative fields (currently just ``TOOLS_DIR``) must
not be persisted to ``settings.json`` — they re-resolve from ``sys.executable``
each launch, so freezing the path at first launch breaks movable bundles such
as macOS ``.app`` (DMG vs ``/Applications`` vs anywhere else the user copies it).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from src import config
from src.config import _INSTALL_RELATIVE_FIELDS, Settings, _populate_missing_defaults

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def isolated_settings_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(config, "_get_settings_path", lambda: settings_path)
    return settings_path


def test_install_relative_fields_includes_tools_dir() -> None:
    assert "TOOLS_DIR" in _INSTALL_RELATIVE_FIELDS


def test_populate_skips_install_relative_fields(isolated_settings_path: Path) -> None:
    settings = Settings()
    _populate_missing_defaults(settings)

    written = json.loads(isolated_settings_path.read_text(encoding="utf-8"))
    for field in _INSTALL_RELATIVE_FIELDS:
        assert field not in written, (
            f"{field} must not be persisted — it should re-resolve from sys.executable each launch"
        )


def test_populate_writes_other_defaults(isolated_settings_path: Path) -> None:
    settings = Settings()
    _populate_missing_defaults(settings)

    written = json.loads(isolated_settings_path.read_text(encoding="utf-8"))
    # Sanity: regular defaults still get persisted on first run.
    assert "RCFLOW_HOST" in written
    assert "LLM_PROVIDER" in written


def test_populate_preserves_existing_install_relative_value(isolated_settings_path: Path) -> None:
    # Users with a hand-edited or migrated TOOLS_DIR keep their override.
    isolated_settings_path.write_text(json.dumps({"TOOLS_DIR": "/custom/tools"}), encoding="utf-8")

    settings = Settings()
    _populate_missing_defaults(settings)

    written = json.loads(isolated_settings_path.read_text(encoding="utf-8"))
    assert written["TOOLS_DIR"] == "/custom/tools"
