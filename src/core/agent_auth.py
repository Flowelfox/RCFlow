"""Auth-readiness checks for managed coding-agent CLIs (Claude Code, Codex, OpenCode).

Each ``*_configuration_issue`` returns a user-friendly string explaining why the
agent cannot run, or ``None`` when configuration is sufficient.  Used as a
preflight before spawning the subprocess so the client surfaces a readable
``AGENT_CONFIG_ERROR`` instead of a silent hang on auth prompts inside the
PTY-backed CLI.

Messages here describe **only the problem**, not where to fix it — the client
already renders the error alongside a "Configure" button that opens the right
settings page.

OAuth-based providers (``anthropic_login``, ``chatgpt``) are intentionally not
preflighted here: they delegate to the CLI's own credential store, which can
only be inspected by running the CLI itself.  The dedicated ``auth/status``
endpoints handle that asynchronously.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.config import Settings
    from src.services.tool_manager import ToolManager
    from src.services.tool_settings import ToolSettingsManager


_DISPLAY_NAMES = {
    "claude_code": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
}


def _claude_code_issue(
    settings: Settings | None,
    tool_settings: ToolSettingsManager | None,
) -> str | None:
    del settings  # unused — provider is the only gate now
    cfg = tool_settings.get_settings("claude_code") if tool_settings else {}
    provider = cfg.get("provider", "")

    if provider == "anthropic":
        if not cfg.get("anthropic_api_key"):
            return "Claude Code: Anthropic API key is missing."
        return None
    if provider == "bedrock":
        missing = [
            label
            for key, label in (
                ("aws_region", "AWS region"),
                ("aws_access_key_id", "AWS access key ID"),
                ("aws_secret_access_key", "AWS secret access key"),
            )
            if not cfg.get(key)
        ]
        if missing:
            return f"Claude Code (Bedrock): missing {', '.join(missing)}."
        return None
    if provider == "anthropic_login":
        # OAuth — defer to runtime auth/status check.
        return None
    return "Claude Code: no provider selected."


def _codex_issue(
    settings: Settings | None,
    tool_settings: ToolSettingsManager | None,
) -> str | None:
    del settings  # unused — provider is the only gate now
    cfg = tool_settings.get_settings("codex") if tool_settings else {}
    provider = cfg.get("provider", "")

    if provider == "openai":
        if not cfg.get("codex_api_key"):
            return "Codex: OpenAI API key is missing."
        return None
    if provider == "chatgpt":
        # OAuth — auth.json checked at spawn time via _ensure_codex_auth_symlink.
        return None
    return "Codex: no provider selected."


def _opencode_issue(
    settings: Settings | None,
    tool_settings: ToolSettingsManager | None,
) -> str | None:
    cfg = tool_settings.get_settings("opencode") if tool_settings else {}
    provider = cfg.get("provider", "")

    if provider == "anthropic":
        if not cfg.get("opencode_api_key"):
            return "OpenCode: Anthropic API key is missing."
        return None
    if provider == "openai":
        if not cfg.get("openai_api_key"):
            return "OpenCode: OpenAI API key is missing."
        return None
    return "OpenCode: no provider selected."


_CHECKERS = {
    "claude_code": _claude_code_issue,
    "codex": _codex_issue,
    "opencode": _opencode_issue,
}


def agent_configuration_issue(
    agent_name: str,
    settings: Settings | None,
    tool_settings: ToolSettingsManager | None,
    tool_manager: ToolManager | None = None,
) -> str | None:
    """Return a user-friendly reason *agent_name* cannot run, or ``None``.

    Checks installation first (when *tool_manager* is supplied) so
    a not-installed agent surfaces "X is not installed." instead of the
    less-helpful "no provider selected." that the pure config check would
    return.  Unknown agents return ``None`` (no preflight enforcement).
    """
    checker = _CHECKERS.get(agent_name)
    if checker is None:
        return None
    if tool_manager is not None and tool_manager.get_binary_path(agent_name) is None:
        label = _DISPLAY_NAMES.get(agent_name, agent_name)
        return f"{label} is not installed."
    return checker(settings, tool_settings)
