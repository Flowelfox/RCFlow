"""Tests for the ACP-by-default executor mode resolution (prompt_router)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.prompt_router import _acp_mode_enabled
from src.tools.loader import ToolDefinition

if TYPE_CHECKING:
    import pytest

ENV = "RCFLOW_OPENCODE_EXECUTOR"


def _tool(with_acp: bool = True) -> ToolDefinition:
    executor_config: dict = {"opencode": {"binary_path": "opencode"}}
    if with_acp:
        executor_config["acp"] = {"binary_path": "opencode", "args": ["acp"]}
    return ToolDefinition(
        name="opencode",
        description="d",
        session_type="long-running",
        llm_context="session-scoped",
        executor="opencode",
        parameters={"type": "object", "properties": {}},
        executor_config=executor_config,
    )


class TestDefaultIsAcp:
    def test_unset_flag_with_adapter_available_uses_acp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV, raising=False)
        assert _acp_mode_enabled(ENV, _tool(), lambda b: "/managed/opencode") is True

    def test_unset_flag_without_adapter_falls_back_to_legacy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV, raising=False)
        assert _acp_mode_enabled(ENV, _tool(), lambda b: None) is False

    def test_unset_flag_without_acp_config_is_legacy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV, raising=False)
        assert _acp_mode_enabled(ENV, _tool(with_acp=False), lambda b: "/managed/opencode") is False

    def test_resolver_receives_adapter_binary_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ENV, raising=False)
        seen: list[str] = []

        def resolver(binary: str) -> str | None:
            seen.append(binary)
            return "/x"

        _acp_mode_enabled(ENV, _tool(), resolver)
        assert seen == ["opencode"]


class TestExplicitOverrides:
    def test_explicit_legacy_always_opts_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV, "legacy")
        assert _acp_mode_enabled(ENV, _tool(), lambda b: "/managed/opencode") is False

    def test_explicit_acp_skips_availability_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV, "acp")
        assert _acp_mode_enabled(ENV, _tool(), lambda b: None) is True

    def test_explicit_acp_without_config_still_legacy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV, "acp")
        assert _acp_mode_enabled(ENV, _tool(with_acp=False), lambda b: "/x") is False
