"""Per-tool settings management for RCFlow-managed CLI tools.

Reads and writes isolated JSON settings files so that RCFlow-launched
instances of Claude Code and Codex don't share configuration with
user-installed ones.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Keys related to provider configuration (used for env sync detection).
_PROVIDER_KEYS = frozenset({
    "provider",
    "anthropic_api_key",
    "aws_region",
    "aws_access_key_id",
    "aws_secret_access_key",
})

# Keys related to Codex provider configuration (used for env sync detection).
_CODEX_PROVIDER_KEYS = frozenset({
    "provider",
    "codex_api_key",
})

_MASK_CHAR = "*"
_MASK_VISIBLE_CHARS = 4


def _mask_secret(value: str) -> str:
    """Mask a secret value, showing only the last 4 characters."""
    if not value:
        return ""
    if len(value) <= _MASK_VISIBLE_CHARS:
        return _MASK_CHAR * len(value)
    return _MASK_CHAR * (len(value) - _MASK_VISIBLE_CHARS) + value[-_MASK_VISIBLE_CHARS:]


def _is_masked(value: str) -> bool:
    """Return True if *value* looks like a masked secret (asterisk prefix)."""
    if not value or len(value) <= _MASK_VISIBLE_CHARS:
        return False
    return value[: len(value) - _MASK_VISIBLE_CHARS] == _MASK_CHAR * (len(value) - _MASK_VISIBLE_CHARS)


# ---------------------------------------------------------------------------
# Settings schemas
# ---------------------------------------------------------------------------

CLAUDE_CODE_SETTINGS_SCHEMA: list[dict[str, Any]] = [
    {
        "key": "permissions.allow",
        "label": "Allowed permissions",
        "type": "string_list",
        "default": [],
        "description": "Tool permissions to always allow (e.g. Bash, Read, Write).",
    },
    {
        "key": "permissions.deny",
        "label": "Denied permissions",
        "type": "string_list",
        "default": [],
        "description": "Tool permissions to always deny.",
    },
    {
        "key": "enableAllProjectMcpServers",
        "label": "Enable all project MCP servers",
        "type": "boolean",
        "default": False,
        "description": "Automatically enable MCP servers defined in project config.",
    },
    {
        "key": "provider",
        "label": "API Provider",
        "type": "select",
        "default": "",
        "description": "LLM provider for Claude Code. 'Global' uses server-level config.",
        "options": [
            {"value": "", "label": "Global"},
            {"value": "anthropic", "label": "Anthropic"},
            {"value": "bedrock", "label": "AWS Bedrock"},
        ],
        "managed_only": True,
    },
    {
        "key": "anthropic_api_key",
        "label": "Anthropic API Key",
        "type": "secret",
        "default": "",
        "description": "API key for Anthropic provider.",
        "managed_only": True,
        "visible_when": {"key": "provider", "value": "anthropic"},
    },
    {
        "key": "aws_region",
        "label": "AWS Region",
        "type": "string",
        "default": "us-east-1",
        "description": "AWS region for Bedrock.",
        "managed_only": True,
        "visible_when": {"key": "provider", "value": "bedrock"},
    },
    {
        "key": "aws_access_key_id",
        "label": "AWS Access Key ID",
        "type": "secret",
        "default": "",
        "description": "AWS access key for Bedrock.",
        "managed_only": True,
        "visible_when": {"key": "provider", "value": "bedrock"},
    },
    {
        "key": "aws_secret_access_key",
        "label": "AWS Secret Access Key",
        "type": "secret",
        "default": "",
        "description": "AWS secret access key for Bedrock.",
        "managed_only": True,
        "visible_when": {"key": "provider", "value": "bedrock"},
    },
    {
        "key": "model",
        "label": "Model",
        "type": "string",
        "default": "",
        "description": "Default model override for Claude Code sessions.",
        "managed_only": True,
    },
    {
        "key": "default_permission_mode",
        "label": "Permission mode",
        "type": "select",
        "default": "",
        "description": "CLI --permission-mode flag for managed sessions.",
        "options": [
            {"value": "", "label": "Default"},
            {"value": "bypassPermissions", "label": "Bypass Permissions"},
            {"value": "allowEdits", "label": "Allow Edits"},
            {"value": "interactive", "label": "Interactive (Ask User)"},
        ],
        "managed_only": True,
    },
    {
        "key": "max_turns",
        "label": "Max turns",
        "type": "string",
        "default": "",
        "description": "Maximum agentic turns per session (default 200).",
        "managed_only": True,
    },
    {
        "key": "timeout",
        "label": "Timeout (seconds)",
        "type": "string",
        "default": "",
        "description": "Process timeout in seconds (default 1800).",
        "managed_only": True,
    },
]

CODEX_SETTINGS_SCHEMA: list[dict[str, Any]] = [
    {
        "key": "provider",
        "label": "API Provider",
        "type": "select",
        "default": "",
        "description": "API key source for Codex. 'Global' uses server-level config.",
        "options": [
            {"value": "", "label": "Global"},
            {"value": "openai", "label": "OpenAI"},
            {"value": "chatgpt", "label": "ChatGPT (Subscription)"},
        ],
        "managed_only": True,
    },
    {
        "key": "codex_api_key",
        "label": "OpenAI API Key",
        "type": "secret",
        "default": "",
        "description": "API key for OpenAI provider.",
        "managed_only": True,
        "visible_when": {"key": "provider", "value": "openai"},
    },
    {
        "key": "model",
        "label": "Model",
        "type": "string",
        "default": "",
        "description": "Model name to use for Codex sessions.",
    },
    {
        "key": "approval_mode",
        "label": "Approval mode",
        "type": "select",
        "default": "full-auto",
        "description": "How Codex handles tool-call approval.",
        "options": [
            {"value": "full-auto", "label": "Full Auto"},
            {"value": "yolo", "label": "YOLO"},
        ],
    },
    {
        "key": "timeout",
        "label": "Timeout (seconds)",
        "type": "string",
        "default": "",
        "description": "Process timeout in seconds (default 600).",
        "managed_only": True,
    },
]

_TOOL_SCHEMAS: dict[str, list[dict[str, Any]]] = {
    "claude_code": CLAUDE_CODE_SETTINGS_SCHEMA,
    "codex": CODEX_SETTINGS_SCHEMA,
}

_TOOL_CONFIG_PATHS: dict[str, str] = {
    "claude_code": os.path.join("claude-code", "config", "settings.json"),
    "codex": os.path.join("codex", "config", "codex.json"),
}


# ---------------------------------------------------------------------------
# Helpers for nested dict access via dotted keys
# ---------------------------------------------------------------------------


def _get_nested(d: dict[str, Any], dotted_key: str) -> Any:
    """Retrieve a value from a nested dict using a dotted key path."""
    keys = dotted_key.split(".")
    current: Any = d
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _set_nested(d: dict[str, Any], dotted_key: str, value: Any) -> None:
    """Set a value in a nested dict using a dotted key path."""
    keys = dotted_key.split(".")
    current = d
    for key in keys[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[keys[-1]] = value


# ---------------------------------------------------------------------------
# Provider env sync
# ---------------------------------------------------------------------------


def _sync_provider_env(settings: dict[str, Any]) -> None:
    """Rebuild the ``env`` section of Claude Code settings.json based on provider fields.

    Called after provider-related keys are updated. Mutates *settings* in place.
    """
    provider = settings.get("provider", "")

    if provider == "anthropic":
        api_key = settings.get("anthropic_api_key", "")
        env: dict[str, str] = {}
        if api_key:
            env["ANTHROPIC_API_KEY"] = api_key
        settings["env"] = env if env else {}
    elif provider == "bedrock":
        env = {"CLAUDE_CODE_USE_BEDROCK": "1"}
        for setting_key, env_key in (
            ("aws_region", "AWS_REGION"),
            ("aws_access_key_id", "AWS_ACCESS_KEY_ID"),
            ("aws_secret_access_key", "AWS_SECRET_ACCESS_KEY"),
        ):
            val = settings.get(setting_key, "")
            if val:
                env[env_key] = val
        settings["env"] = env
    else:
        # Global / empty — remove env section so global config takes over.
        settings.pop("env", None)


def _sync_codex_provider_env(settings: dict[str, Any]) -> None:
    """Rebuild the ``env`` section of Codex settings based on provider fields.

    Called after provider-related keys are updated. Mutates *settings* in place.
    """
    provider = settings.get("provider", "")

    if provider == "openai":
        api_key = settings.get("codex_api_key", "")
        env: dict[str, str] = {}
        if api_key:
            env["CODEX_API_KEY"] = api_key
        settings["env"] = env if env else {}
    elif provider == "chatgpt":
        # ChatGPT subscription auth uses OAuth tokens from auth.json,
        # not an API key.  Clear the env section so no key is injected.
        settings["env"] = {}
    else:
        # Global / empty — remove env section so global config takes over.
        settings.pop("env", None)


# ---------------------------------------------------------------------------
# ToolSettingsManager
# ---------------------------------------------------------------------------


class ToolSettingsManager:
    """Reads and writes per-tool JSON settings files.

    Settings are stored under ``~/.local/share/rcflow/tools/`` so that
    RCFlow-managed tool instances use isolated configuration.
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or (Path.home() / ".local" / "share" / "rcflow" / "tools")

    def get_config_dir(self, tool_name: str) -> Path:
        """Return the config directory for a tool (for env var injection)."""
        rel = _TOOL_CONFIG_PATHS.get(tool_name)
        if rel is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        settings_path = self._base_dir / rel
        return settings_path.parent

    def get_settings(self, tool_name: str) -> dict[str, Any]:
        """Read the raw JSON settings for a tool."""
        rel = _TOOL_CONFIG_PATHS.get(tool_name)
        if rel is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        settings_path = self._base_dir / rel
        if not settings_path.is_file():
            return {}
        try:
            return json.loads(settings_path.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to read settings for %s at %s", tool_name, settings_path)
            return {}

    def get_settings_with_schema(self, tool_name: str, *, managed: bool = True) -> dict[str, Any]:
        """Return schema fields merged with current values for the UI.

        When *managed* is False, fields marked ``managed_only`` are excluded.
        """
        schema = _TOOL_SCHEMAS.get(tool_name)
        if schema is None:
            raise ValueError(f"Unknown tool: {tool_name}")
        current = self.get_settings(tool_name)

        fields: list[dict[str, Any]] = []
        for field_def in schema:
            if not managed and field_def.get("managed_only"):
                continue
            value = _get_nested(current, field_def["key"])
            if value is None:
                value = field_def["default"]
            # Mask secret values before returning to the client.
            if field_def["type"] == "secret" and isinstance(value, str) and value:
                value = _mask_secret(value)

            entry: dict[str, Any] = {
                "key": field_def["key"],
                "label": field_def["label"],
                "type": field_def["type"],
                "value": value,
                "default": field_def["default"],
                "description": field_def["description"],
            }
            if "options" in field_def:
                entry["options"] = field_def["options"]
            if "visible_when" in field_def:
                entry["visible_when"] = field_def["visible_when"]
            fields.append(entry)

        return {"tool": tool_name, "fields": fields}

    def update_settings(self, tool_name: str, updates: dict[str, Any], *, managed: bool = True) -> dict[str, Any]:
        """Validate keys, apply updates, write atomically, return schema+values.

        When *managed* is False, keys marked ``managed_only`` are rejected.
        """
        schema = _TOOL_SCHEMAS.get(tool_name)
        if schema is None:
            raise ValueError(f"Unknown tool: {tool_name}")

        valid_keys = {f["key"] for f in schema}
        invalid = set(updates.keys()) - valid_keys
        if invalid:
            raise ValueError(f"Unknown settings keys: {', '.join(sorted(invalid))}")

        if not managed:
            managed_only_keys = {f["key"] for f in schema if f.get("managed_only")}
            rejected = set(updates.keys()) & managed_only_keys
            if rejected:
                raise ValueError(
                    f"Cannot update managed-only settings when tool is external: {', '.join(sorted(rejected))}"
                )

        # Build a lookup of secret-type keys for masked value detection.
        secret_keys = {f["key"] for f in schema if f["type"] == "secret"}

        current = self.get_settings(tool_name)

        # Filter out masked secret values — they represent unchanged secrets.
        for key, value in updates.items():
            if key in secret_keys and isinstance(value, str) and _is_masked(value):
                continue  # Skip; preserve existing value in current dict.
            _set_nested(current, key, value)

        # Sync the env section in settings.json when provider-related keys change.
        if tool_name == "claude_code" and _PROVIDER_KEYS & set(updates.keys()):
            _sync_provider_env(current)
        elif tool_name == "codex" and _CODEX_PROVIDER_KEYS & set(updates.keys()):
            _sync_codex_provider_env(current)

        rel = _TOOL_CONFIG_PATHS.get(tool_name)
        assert rel is not None
        settings_path = self._base_dir / rel
        settings_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = settings_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(current, indent=2) + "\n")
            tmp_path.rename(settings_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        return self.get_settings_with_schema(tool_name, managed=managed)
