from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Server
    RCFLOW_HOST: str = "0.0.0.0"
    RCFLOW_PORT: int = 8765
    RCFLOW_API_KEY: str
    RCFLOW_BACKEND_ID: str = ""

    # SSL/TLS (set both to enable WSS)
    SSL_CERTFILE: str = ""
    SSL_KEYFILE: str = ""

    # Database
    DATABASE_URL: str

    # LLM provider: "anthropic" (direct API) or "bedrock" (AWS Bedrock)
    LLM_PROVIDER: str = "anthropic"

    # Anthropic LLM (used when LLM_PROVIDER = "anthropic")
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"

    # AWS Bedrock (used when LLM_PROVIDER = "bedrock")
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""

    # STT (Speech-to-Text)
    STT_PROVIDER: str = "wispr_flow"
    STT_API_KEY: str = ""

    # TTS (Text-to-Speech)
    TTS_PROVIDER: str = "none"
    TTS_API_KEY: str = ""

    # Projects
    PROJECTS_DIR: Path = Field(default=Path("~/Projects"))

    # Tools
    TOOLS_DIR: Path = Field(default=Path("./tools"))

    # Codex CLI (OpenAI Codex)
    CODEX_API_KEY: str = ""

    # Summarization (TTS-friendly summary of Claude Code results)
    # Use Anthropic model ID for direct API, Bedrock model ID for Bedrock
    # e.g. "claude-haiku-4-5-20251001" or "us.anthropic.claude-haiku-4-5-v1:0"
    SUMMARY_MODEL: str = ""

    # Tool Management
    TOOL_AUTO_UPDATE: bool = True
    TOOL_UPDATE_INTERVAL_HOURS: float = 6.0

    # Logging
    LOG_LEVEL: str = "INFO"


def get_settings() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    if not settings.RCFLOW_BACKEND_ID:
        backend_id = str(uuid.uuid4())
        update_env_file({"RCFLOW_BACKEND_ID": backend_id})
        settings.RCFLOW_BACKEND_ID = backend_id
    return settings


def _mask_secret(value: str) -> str:
    """Mask a secret value, showing only the last 4 characters."""
    if len(value) <= 4:
        return "****" if value else ""
    return "*" * (len(value) - 4) + value[-4:]


CONFIG_OPTIONS: list[dict[str, Any]] = [
    # --- LLM ---
    {
        "key": "LLM_PROVIDER",
        "label": "LLM Provider",
        "type": "select",
        "options": [
            {"value": "anthropic", "label": "Anthropic Key"},
            {"value": "bedrock", "label": "Bedrock"},
        ],
        "group": "LLM",
        "description": "LLM backend to use for inference",
        "required": True,
        "restart_required": True,
    },
    {
        "key": "ANTHROPIC_API_KEY",
        "label": "Anthropic API Key",
        "type": "secret",
        "group": "LLM",
        "description": "API key for direct Anthropic API access",
        "required": False,
        "restart_required": True,
        "visible_when": {"key": "LLM_PROVIDER", "value": "anthropic"},
    },
    {
        "key": "ANTHROPIC_MODEL",
        "label": "Model",
        "type": "string",
        "group": "LLM",
        "description": "Model ID (e.g. claude-sonnet-4-20250514). For Bedrock use Bedrock model IDs.",
        "required": False,
        "restart_required": True,
    },
    {
        "key": "AWS_REGION",
        "label": "AWS Region",
        "type": "string",
        "group": "LLM",
        "description": "AWS region for Bedrock (e.g. us-east-1)",
        "required": False,
        "restart_required": True,
        "visible_when": {"key": "LLM_PROVIDER", "value": "bedrock"},
    },
    {
        "key": "AWS_ACCESS_KEY_ID",
        "label": "AWS Access Key ID",
        "type": "secret",
        "group": "LLM",
        "description": "AWS access key for Bedrock authentication",
        "required": False,
        "restart_required": True,
        "visible_when": {"key": "LLM_PROVIDER", "value": "bedrock"},
    },
    {
        "key": "AWS_SECRET_ACCESS_KEY",
        "label": "AWS Secret Access Key",
        "type": "secret",
        "group": "LLM",
        "description": "AWS secret key for Bedrock authentication",
        "required": False,
        "restart_required": True,
        "visible_when": {"key": "LLM_PROVIDER", "value": "bedrock"},
    },
    {
        "key": "SUMMARY_MODEL",
        "label": "Summary Model",
        "type": "string",
        "group": "LLM",
        "description": "Model for TTS-friendly summaries (blank = use main model)",
        "required": False,
        "restart_required": True,
    },
    # --- STT ---
    {
        "key": "STT_PROVIDER",
        "label": "STT Provider",
        "type": "select",
        "options": [
            {"value": "wispr_flow", "label": "Wispr Flow"},
        ],
        "group": "STT",
        "description": "Speech-to-text provider",
        "required": True,
        "restart_required": True,
    },
    {
        "key": "STT_API_KEY",
        "label": "STT API Key",
        "type": "secret",
        "group": "STT",
        "description": "API key for the STT provider",
        "required": False,
        "restart_required": True,
    },
    # --- TTS ---
    {
        "key": "TTS_PROVIDER",
        "label": "TTS Provider",
        "type": "select",
        "options": [
            {"value": "none", "label": "None"},
        ],
        "group": "TTS",
        "description": "Text-to-speech provider",
        "required": True,
        "restart_required": True,
    },
    {
        "key": "TTS_API_KEY",
        "label": "TTS API Key",
        "type": "secret",
        "group": "TTS",
        "description": "API key for the TTS provider",
        "required": False,
        "restart_required": True,
        "visible_when": {"key": "TTS_PROVIDER", "value_not": "none"},
    },
    # --- Codex ---
    {
        "key": "CODEX_API_KEY",
        "label": "API Key",
        "type": "secret",
        "group": "Codex",
        "description": "API key for OpenAI Codex",
        "required": False,
        "restart_required": False,
    },
    # --- Paths ---
    {
        "key": "PROJECTS_DIR",
        "label": "Projects Directory",
        "type": "string",
        "group": "Paths",
        "description": "Root directory containing project folders",
        "required": True,
        "restart_required": False,
    },
    # --- Tool Management ---
    {
        "key": "TOOL_AUTO_UPDATE",
        "label": "Auto-Update Tools",
        "type": "boolean",
        "group": "Tool Management",
        "description": "Automatically check for and install updates to Claude Code and Codex CLI",
        "required": False,
        "restart_required": False,
    },
    {
        "key": "TOOL_UPDATE_INTERVAL_HOURS",
        "label": "Update Check Interval (hours)",
        "type": "string",
        "group": "Tool Management",
        "description": "How often to check for tool updates (in hours)",
        "required": False,
        "restart_required": False,
    },
    # --- Logging ---
    {
        "key": "LOG_LEVEL",
        "label": "Log Level",
        "type": "select",
        "options": [
            {"value": "DEBUG", "label": "Debug"},
            {"value": "INFO", "label": "Info"},
            {"value": "WARNING", "label": "Warning"},
            {"value": "ERROR", "label": "Error"},
        ],
        "group": "Logging",
        "description": "Server log verbosity",
        "required": True,
        "restart_required": False,
    },
]


def get_config_schema(settings: Settings) -> list[dict[str, Any]]:
    """Build the config schema with current values from the given Settings instance.

    Secret values are masked. Path values are serialized as strings.
    """
    result: list[dict[str, Any]] = []
    for opt in CONFIG_OPTIONS:
        key = opt["key"]
        raw_value = getattr(settings, key)

        if isinstance(raw_value, Path):
            value: Any = str(raw_value)
        elif isinstance(raw_value, bool):
            value = raw_value
        else:
            value = str(raw_value) if raw_value is not None else ""

        entry = {**opt, "value": value}

        if opt["type"] == "secret":
            entry["value"] = _mask_secret(str(raw_value))

        result.append(entry)
    return result


CONFIGURABLE_KEYS: set[str] = {opt["key"] for opt in CONFIG_OPTIONS}


def update_env_file(updates: dict[str, str], env_path: str = ".env") -> None:
    """Apply key=value updates to the .env file, preserving comments and order.

    New keys that don't exist in the file are appended at the end.
    """
    path = Path(env_path)
    lines: list[str] = []
    if path.exists():
        lines = path.read_text().splitlines()

    updated_keys: set[str] = set()
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            eq_pos = stripped.find("=")
            if eq_pos > 0:
                env_key = stripped[:eq_pos].strip()
                if env_key in updates:
                    new_lines.append(f"{env_key}={updates[env_key]}")
                    updated_keys.add(env_key)
                    continue
        new_lines.append(line)

    for key, value in updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={value}")

    path.write_text("\n".join(new_lines) + "\n")
