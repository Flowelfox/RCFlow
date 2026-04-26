"""Tests for ``src.core.agent_auth.agent_configuration_issue``.

Mirrors the LLM-side preflight (``llm_configuration_issue``) but for the
managed coding-agent CLIs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.agent_auth import agent_configuration_issue


def _settings(**overrides: str | int) -> SimpleNamespace:
    base = {"ANTHROPIC_API_KEY": "", "CODEX_API_KEY": "", "OPENAI_API_KEY": ""}
    base.update(overrides)  # ty:ignore[invalid-argument-type]
    return SimpleNamespace(**base)


class _FakeToolSettings:
    def __init__(self, settings: dict[str, dict[str, object]]) -> None:
        self._settings = settings

    def get_settings(self, tool_name: str) -> dict[str, object]:
        return self._settings.get(tool_name, {})


class _FakeToolManager:
    """Stand-in for ToolManager.get_binary_path used by the install check."""

    def __init__(self, installed: dict[str, str | None]) -> None:
        self._installed = installed

    def get_binary_path(self, name: str) -> str | None:
        return self._installed.get(name)


# ----- claude_code ---------------------------------------------------------


def test_claude_code_unset_provider_returns_issue_even_with_global_key() -> None:
    """``Settings.ANTHROPIC_API_KEY`` no longer satisfies an unset agent provider."""
    issue = agent_configuration_issue(
        "claude_code",
        _settings(ANTHROPIC_API_KEY="sk-ant"),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({"claude_code": {"provider": ""}}),  # ty:ignore[invalid-argument-type]
    )
    assert issue is not None
    assert "no provider selected" in issue


def test_claude_code_unset_provider_returns_issue() -> None:
    issue = agent_configuration_issue(
        "claude_code",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({"claude_code": {"provider": ""}}),  # ty:ignore[invalid-argument-type]
    )
    assert issue is not None
    assert "Claude Code" in issue
    assert "no provider selected" in issue


def test_claude_code_anthropic_provider_with_key_is_ready() -> None:
    issue = agent_configuration_issue(
        "claude_code",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings(  # ty:ignore[invalid-argument-type]
            {"claude_code": {"provider": "anthropic", "anthropic_api_key": "sk-ant"}}
        ),
    )
    assert issue is None


def test_claude_code_anthropic_provider_without_key_returns_issue() -> None:
    issue = agent_configuration_issue(
        "claude_code",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({"claude_code": {"provider": "anthropic", "anthropic_api_key": ""}}),  # ty:ignore[invalid-argument-type]
    )
    assert issue is not None
    assert "Anthropic" in issue


def test_claude_code_anthropic_login_skipped() -> None:
    """OAuth login state is not preflighted synchronously — defer to runtime."""
    issue = agent_configuration_issue(
        "claude_code",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({"claude_code": {"provider": "anthropic_login"}}),  # ty:ignore[invalid-argument-type]
    )
    assert issue is None


def test_claude_code_bedrock_missing_creds_returns_issue() -> None:
    issue = agent_configuration_issue(
        "claude_code",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings(  # ty:ignore[invalid-argument-type]
            {"claude_code": {"provider": "bedrock", "aws_region": "us-east-1"}}
        ),
    )
    assert issue is not None
    assert "Bedrock" in issue
    assert "access key" in issue.lower()


def test_claude_code_bedrock_full_creds_is_ready() -> None:
    issue = agent_configuration_issue(
        "claude_code",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings(  # ty:ignore[invalid-argument-type]
            {
                "claude_code": {
                    "provider": "bedrock",
                    "aws_region": "us-east-1",
                    "aws_access_key_id": "AKIA",
                    "aws_secret_access_key": "secret",
                }
            }
        ),
    )
    assert issue is None


# ----- codex ---------------------------------------------------------------


def test_codex_unset_provider_returns_issue_even_with_global_key() -> None:
    """``Settings.CODEX_API_KEY`` no longer satisfies an unset agent provider."""
    issue = agent_configuration_issue(
        "codex",
        _settings(CODEX_API_KEY="sk-cdx"),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({"codex": {"provider": ""}}),  # ty:ignore[invalid-argument-type]
    )
    assert issue is not None
    assert "no provider selected" in issue


def test_codex_unset_provider_returns_issue() -> None:
    issue = agent_configuration_issue(
        "codex",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({"codex": {"provider": ""}}),  # ty:ignore[invalid-argument-type]
    )
    assert issue is not None
    assert "Codex" in issue


def test_codex_chatgpt_provider_skipped() -> None:
    issue = agent_configuration_issue(
        "codex",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({"codex": {"provider": "chatgpt"}}),  # ty:ignore[invalid-argument-type]
    )
    assert issue is None


def test_codex_openai_provider_without_key_returns_issue() -> None:
    issue = agent_configuration_issue(
        "codex",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({"codex": {"provider": "openai", "codex_api_key": ""}}),  # ty:ignore[invalid-argument-type]
    )
    assert issue is not None


# ----- opencode ------------------------------------------------------------


def test_opencode_global_returns_issue() -> None:
    """OpenCode has no managed login flow — global mode means no provider chosen."""
    issue = agent_configuration_issue(
        "opencode",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({"opencode": {"provider": ""}}),  # ty:ignore[invalid-argument-type]
    )
    assert issue is not None
    assert "OpenCode" in issue


def test_opencode_anthropic_without_key_returns_issue() -> None:
    issue = agent_configuration_issue(
        "opencode",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({"opencode": {"provider": "anthropic", "opencode_api_key": ""}}),  # ty:ignore[invalid-argument-type]
    )
    assert issue is not None
    assert "OpenCode" in issue


def test_opencode_openai_with_key_is_ready() -> None:
    issue = agent_configuration_issue(
        "opencode",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({"opencode": {"provider": "openai", "openai_api_key": "sk"}}),  # ty:ignore[invalid-argument-type]
    )
    assert issue is None


# ----- unknown agents ------------------------------------------------------


def test_unknown_agent_returns_none() -> None:
    issue = agent_configuration_issue(
        "no_such_agent",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({}),  # ty:ignore[invalid-argument-type]
    )
    assert issue is None


def test_no_settings_no_tool_settings_for_claude_code_returns_issue() -> None:
    issue = agent_configuration_issue("claude_code", None, None)
    assert issue is not None


# ----- not installed (highest priority) ------------------------------------


def test_not_installed_takes_precedence_over_provider_check() -> None:
    """A not-installed agent surfaces 'is not installed' instead of provider issues."""
    issue = agent_configuration_issue(
        "codex",
        _settings(CODEX_API_KEY="sk"),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({"codex": {"provider": "openai", "codex_api_key": "sk"}}),  # ty:ignore[invalid-argument-type]
        _FakeToolManager({"codex": None}),  # ty:ignore[invalid-argument-type]
    )
    assert issue == "Codex is not installed."


def test_installed_falls_through_to_provider_check() -> None:
    issue = agent_configuration_issue(
        "codex",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({"codex": {"provider": ""}}),  # ty:ignore[invalid-argument-type]
        _FakeToolManager({"codex": "/path/to/codex"}),  # ty:ignore[invalid-argument-type]
    )
    assert issue is not None
    assert "no provider selected" in issue


def test_tool_manager_omitted_skips_install_check() -> None:
    """Backward-compat: callers that don't pass a tool_manager get the old behaviour."""
    issue = agent_configuration_issue(
        "codex",
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({"codex": {"provider": "openai", "codex_api_key": "sk"}}),  # ty:ignore[invalid-argument-type]
    )
    assert issue is None


@pytest.mark.parametrize(
    ("agent", "expected_label"),
    [
        ("claude_code", "Claude Code is not installed."),
        ("codex", "Codex is not installed."),
        ("opencode", "OpenCode is not installed."),
    ],
)
def test_not_installed_message_per_agent(agent: str, expected_label: str) -> None:
    issue = agent_configuration_issue(
        agent,
        _settings(),  # ty:ignore[invalid-argument-type]
        _FakeToolSettings({}),  # ty:ignore[invalid-argument-type]
        _FakeToolManager({agent: None}),  # ty:ignore[invalid-argument-type]
    )
    assert issue == expected_label
