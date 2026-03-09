from __future__ import annotations

import json
import logging
import secrets
import sys
import uuid
from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.paths import get_default_tools_dir, get_install_dir

# Default port: 53891 on Windows, 53890 on Linux
_DEFAULT_PORT = 53891 if sys.platform == "win32" else 53890


def _default_tools_dir() -> Path:
    return get_default_tools_dir()


def _get_settings_path() -> Path:
    """Return the path to settings.json (next to the executable or project root)."""
    return get_install_dir() / "settings.json"


def _load_settings_into_env() -> None:
    """Read settings.json and inject values into os.environ (if not already set).

    This is called before pydantic_settings constructs a Settings instance so
    that values from settings.json are available as environment variables.
    Environment variables explicitly set in the shell take precedence.
    """
    import os  # noqa: PLC0415

    cfg_path = _get_settings_path()

    # Migrate from .env on first run if settings.json doesn't exist yet
    _migrate_env_to_json(cfg_path)

    if not cfg_path.exists():
        return

    data = json.loads(cfg_path.read_text(encoding="utf-8"))

    for key, value in data.items():
        env_key = key.upper()
        if env_key not in os.environ:
            os.environ[env_key] = str(value)


def _migrate_env_to_json(json_path: Path) -> None:
    """If a .env file exists but settings.json does not, migrate settings."""
    if json_path.exists():
        return

    env_path = json_path.parent / ".env"
    if not env_path.exists():
        return

    logger = logging.getLogger(__name__)
    logger.info("Migrating settings from .env to settings.json")

    updates: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        eq_pos = stripped.find("=")
        if eq_pos > 0:
            key = stripped[:eq_pos].strip()
            value = stripped[eq_pos + 1:].strip()
            updates[key] = value

    if updates:
        update_settings_file(updates)
        logger.info("Migrated %d settings from .env to settings.json", len(updates))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        case_sensitive=False,
    )

    # Server
    RCFLOW_HOST: str = "0.0.0.0"
    RCFLOW_PORT: int = _DEFAULT_PORT
    RCFLOW_API_KEY: str = ""
    RCFLOW_BACKEND_ID: str = ""

    # SSL/TLS (WSS)
    WSS_ENABLED: bool = True
    SSL_CERTFILE: str = ""
    SSL_KEYFILE: str = ""

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/rcflow.db"

    # LLM provider: "anthropic" (direct API), "bedrock" (AWS Bedrock), or "openai"
    LLM_PROVIDER: str = "anthropic"

    # Anthropic LLM (used when LLM_PROVIDER = "anthropic")
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-20250514"

    # AWS Bedrock (used when LLM_PROVIDER = "bedrock")
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""

    # OpenAI (used when LLM_PROVIDER = "openai")
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"

    # STT (Speech-to-Text)
    STT_PROVIDER: str = "wispr_flow"
    STT_API_KEY: str = ""

    # TTS (Text-to-Speech)
    TTS_PROVIDER: str = "none"
    TTS_API_KEY: str = ""

    # Projects (comma-separated list of directories)
    PROJECTS_DIR: str = "~/Projects"

    # Tools
    TOOLS_DIR: Path = Field(default_factory=_default_tools_dir)

    # Codex CLI (OpenAI Codex)
    CODEX_API_KEY: str = ""

    # Summarization (TTS-friendly summary of Claude Code results)
    # Use Anthropic model ID for direct API, Bedrock model ID for Bedrock
    # e.g. "claude-haiku-4-5-20251001" or "us.anthropic.claude-haiku-4-5-v1:0"
    SUMMARY_MODEL: str = ""

    # Global prompt (appended to system prompt for all sessions)
    GLOBAL_PROMPT: str = ""

    # Tool Management
    TOOL_AUTO_UPDATE: bool = True
    TOOL_UPDATE_INTERVAL_HOURS: float = 6.0

    # Session token limits (0 = unlimited)
    SESSION_INPUT_TOKEN_LIMIT: int = 0
    SESSION_OUTPUT_TOKEN_LIMIT: int = 0

    # Artifacts
    ARTIFACT_INCLUDE_PATTERN: str = "*.md"
    ARTIFACT_EXCLUDE_PATTERN: str = "node_modules/**,__pycache__/**,.git/**,.venv/**,venv/**,.env/**,build/**,dist/**,target/**,*.pyc"
    ARTIFACT_AUTO_SCAN: bool = True
    ARTIFACT_MAX_FILE_SIZE: int = 5242880  # 5MB in bytes

    # Logging
    LOG_LEVEL: str = "INFO"

    @property
    def projects_dirs(self) -> list[Path]:
        """Parse PROJECTS_DIR into a list of expanded, resolved Path objects."""
        raw = self.PROJECTS_DIR.strip()
        if not raw:
            return []
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return [Path(p).expanduser().resolve() for p in parts]



def get_settings() -> Settings:
    logger = logging.getLogger(__name__)
    settings = Settings()  # type: ignore[call-arg]
    if not settings.RCFLOW_API_KEY:
        api_key = secrets.token_urlsafe(32)
        update_settings_file({"RCFLOW_API_KEY": api_key})
        settings.RCFLOW_API_KEY = api_key
        logger.info("Generated new RCFLOW_API_KEY: %s", api_key)
    if not settings.RCFLOW_BACKEND_ID:
        backend_id = str(uuid.uuid4())
        update_settings_file({"RCFLOW_BACKEND_ID": backend_id})
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
            {"value": "openai", "label": "OpenAI"},
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
        "label": "Anthropic Model",
        "type": "string",
        "group": "LLM",
        "description": "Model ID (e.g. claude-sonnet-4-20250514). For Bedrock use Bedrock model IDs.",
        "required": False,
        "restart_required": True,
        "visible_when": {"key": "LLM_PROVIDER", "value_not": "openai"},
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
        "key": "OPENAI_API_KEY",
        "label": "OpenAI API Key",
        "type": "secret",
        "group": "LLM",
        "description": "API key for OpenAI API access",
        "required": False,
        "restart_required": True,
        "visible_when": {"key": "LLM_PROVIDER", "value": "openai"},
    },
    {
        "key": "OPENAI_MODEL",
        "label": "OpenAI Model",
        "type": "string",
        "group": "LLM",
        "description": "OpenAI model ID (e.g. gpt-4o, gpt-4.1, o3)",
        "required": False,
        "restart_required": True,
        "visible_when": {"key": "LLM_PROVIDER", "value": "openai"},
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
    # --- Prompt ---
    {
        "key": "GLOBAL_PROMPT",
        "label": "Global Prompt",
        "type": "textarea",
        "group": "Prompt",
        "description": "Custom instructions appended to the system prompt for every session (e.g. language preferences, behavioral guidelines, domain expertise)",
        "required": False,
        "restart_required": False,
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
        "label": "Project Directories",
        "type": "string_list",
        "group": "Paths",
        "description": "Root directories containing project folders",
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
    # --- Session Limits ---
    {
        "key": "SESSION_INPUT_TOKEN_LIMIT",
        "label": "Input Token Limit",
        "type": "number",
        "group": "Session Limits",
        "description": "Maximum input tokens per session (0 = unlimited)",
        "required": False,
        "restart_required": False,
    },
    {
        "key": "SESSION_OUTPUT_TOKEN_LIMIT",
        "label": "Output Token Limit",
        "type": "number",
        "group": "Session Limits",
        "description": "Maximum output tokens per session (0 = unlimited)",
        "required": False,
        "restart_required": False,
    },
    # --- Artifacts ---
    {
        "key": "ARTIFACT_INCLUDE_PATTERN",
        "label": "Include Pattern",
        "type": "string",
        "group": "Artifacts",
        "description": "Glob pattern for files to include (e.g., '*.[mM][dD]' for markdown files)",
        "required": False,
        "restart_required": False,
    },
    {
        "key": "ARTIFACT_EXCLUDE_PATTERN",
        "label": "Exclude Pattern",
        "type": "string",
        "group": "Artifacts",
        "description": "Comma-separated glob patterns to exclude (e.g., 'node_modules/**,build/**')",
        "required": False,
        "restart_required": False,
    },
    {
        "key": "ARTIFACT_AUTO_SCAN",
        "label": "Auto-Extract Artifacts",
        "type": "boolean",
        "group": "Artifacts",
        "description": "Automatically extract file artifacts from messages in real time during session execution",
        "required": False,
        "restart_required": False,
    },
    {
        "key": "ARTIFACT_MAX_FILE_SIZE",
        "label": "Maximum File Size (bytes)",
        "type": "number",
        "group": "Artifacts",
        "description": "Maximum file size to track as artifact (5242880 = 5MB)",
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

        if opt["type"] == "string_list":
            # Comma-separated string → list for the frontend
            if isinstance(raw_value, str) and raw_value.strip():
                value: Any = [p.strip() for p in raw_value.split(",") if p.strip()]
            else:
                value = []
        elif isinstance(raw_value, Path):
            value = str(raw_value)
        elif isinstance(raw_value, bool):
            value = raw_value
        elif isinstance(raw_value, (int, float)):
            value = raw_value
        else:
            value = str(raw_value) if raw_value is not None else ""

        entry = {**opt, "value": value}

        if opt["type"] == "secret":
            entry["value"] = _mask_secret(str(raw_value))

        result.append(entry)
    return result


CONFIGURABLE_KEYS: set[str] = {opt["key"] for opt in CONFIG_OPTIONS}


def update_settings_file(updates: dict[str, str]) -> None:
    """Apply key=value updates to settings.json.

    Creates the file if it does not exist. Existing keys are updated; new keys
    are added. Also updates ``os.environ`` so that any subsequent ``Settings()``
    call picks up the new values.
    """
    import os  # noqa: PLC0415

    path = _get_settings_path()
    data: dict[str, str] = {}

    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))

    for key, value in updates.items():
        data[key] = value
        os.environ[key.upper()] = value

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


_load_settings_into_env()
