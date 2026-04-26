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

from src.paths import get_default_tools_dir

# Provider-aware model lists for model_select fields.
PROVIDER_MODELS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "options": [
            {"value": "claude-opus-4-6", "label": "Claude Opus 4.6"},
            {"value": "claude-sonnet-4-6", "label": "Claude Sonnet 4.6"},
            {"value": "claude-haiku-4-5", "label": "Claude Haiku 4.5"},
        ],
        "allow_custom": True,
    },
    "bedrock": {
        "options": [
            {"value": "us.anthropic.claude-opus-4-5-20251101-v1:0", "label": "Claude Opus 4.5"},
            {"value": "us.anthropic.claude-sonnet-4-20250514-v1:0", "label": "Claude Sonnet 4"},
            {"value": "us.anthropic.claude-haiku-4-5-20251001-v1:0", "label": "Claude Haiku 4.5"},
        ],
        "allow_custom": True,
    },
    "openai": {
        "options": [
            {"value": "gpt-5.5", "label": "ChatGPT 5.5"},
            {"value": "gpt-5.4", "label": "GPT-5.4"},
            {"value": "gpt-4.1", "label": "GPT-4.1"},
            {"value": "gpt-4.1-mini", "label": "GPT-4.1 Mini"},
            {"value": "gpt-4.1-nano", "label": "GPT-4.1 Nano"},
            {"value": "gpt-4o", "label": "GPT-4o"},
            {"value": "gpt-5-mini", "label": "GPT-5 Mini"},
            {"value": "o3", "label": "o3"},
            {"value": "o4-mini", "label": "o4-mini"},
        ],
        "allow_custom": True,
    },
}

# Default backend port across platforms
_DEFAULT_PORT = 53890


def _default_tools_dir() -> Path:
    return get_default_tools_dir()


def _get_settings_path() -> Path:
    """Return the path to settings.json.

    Uses :func:`~src.paths.get_data_dir` so that on macOS frozen builds the
    file lives in ``~/Library/Application Support/rcflow/`` rather than inside
    the read-only ``.app`` bundle.
    """
    from src.paths import get_data_dir  # noqa: PLC0415

    return get_data_dir() / "settings.json"


def read_token_from_file() -> str:
    """Read RCFLOW_API_KEY directly from settings.json.

    Unlike ``Settings().RCFLOW_API_KEY``, this bypasses ``os.environ`` so it
    always reflects the value written by the server subprocess, even when the
    GUI process environment was initialised before the server generated the
    token.  Returns an empty string if the file does not exist or the key is
    absent.
    """
    cfg_path = _get_settings_path()
    if not cfg_path.exists():
        return ""
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        return str(data.get("RCFLOW_API_KEY", ""))
    except Exception:
        return ""


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

    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
    except PermissionError:
        print(
            f"WARNING: Cannot read {cfg_path} — permission denied.\n"
            f"Using default settings. Run with appropriate permissions or set "
            f"environment variables directly.",
            file=sys.stderr,
        )
        return

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
            value = stripped[eq_pos + 1 :].strip()
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

    # WebSocket origin validation (F6 remediation).
    # Comma-separated list of allowed Origin header values for WebSocket
    # connections (e.g. "https://app.example.com,http://localhost:3000").
    # Empty string (default) disables the check — native-app clients without
    # an Origin header are always allowed regardless of this setting.
    WS_ALLOWED_ORIGINS: str = ""

    # SSL/TLS (WSS)
    WSS_ENABLED: bool = True
    SSL_CERTFILE: str = ""
    SSL_KEYFILE: str = ""

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/rcflow.db"

    # LLM provider: "anthropic" (direct API), "bedrock" (AWS Bedrock), "openai", or "none" (direct tool mode)
    LLM_PROVIDER: str = "anthropic"

    # Anthropic LLM (used when LLM_PROVIDER = "anthropic")
    ANTHROPIC_API_KEY: str = ""
    ANTHROPIC_MODEL: str = "claude-sonnet-4-6"

    # AWS Bedrock (used when LLM_PROVIDER = "bedrock")
    AWS_REGION: str = "us-east-1"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""

    # OpenAI (used when LLM_PROVIDER = "openai")
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-5.4"

    # Projects (comma-separated list of directories)
    PROJECTS_DIR: str = "~/Projects"

    # Tools
    TOOLS_DIR: Path = Field(default_factory=_default_tools_dir)

    # Codex CLI (OpenAI Codex)
    CODEX_API_KEY: str = ""

    # Utility models for background operations.
    # Use Anthropic model ID for direct API, Bedrock model ID for Bedrock.
    # e.g. "claude-haiku-4-5" or "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    # When blank, each falls back to the main model.
    TITLE_MODEL: str = ""
    TASK_MODEL: str = ""

    # Global prompt (appended to system prompt for all sessions)
    GLOBAL_PROMPT: str = ""

    # Caveman mode — terse output, ~65-75% fewer tokens
    CAVEMAN_MODE: bool = False
    CAVEMAN_LEVEL: str = "full"  # "lite" | "full" | "ultra"

    # Tool Management
    TOOL_AUTO_UPDATE: bool = True
    TOOL_UPDATE_INTERVAL_HOURS: float = 6.0

    # Session token limits (0 = unlimited)
    SESSION_INPUT_TOKEN_LIMIT: int = 0
    SESSION_OUTPUT_TOKEN_LIMIT: int = 0

    # Artifacts
    ARTIFACT_INCLUDE_PATTERN: str = "*.md"
    ARTIFACT_EXCLUDE_PATTERN: str = (
        "node_modules/**,__pycache__/**,.git/**,.venv/**,venv/**,.env/**,build/**,dist/**,target/**,*.pyc"
    )
    ARTIFACT_AUTO_SCAN: bool = True
    ARTIFACT_MAX_FILE_SIZE: int = 5242880  # 5MB in bytes

    # Linear integration
    LINEAR_API_KEY: str = ""
    LINEAR_TEAM_ID: str = ""
    LINEAR_SYNC_ON_STARTUP: bool = False

    # Telemetry
    TELEMETRY_RETENTION_DAYS: int = 90

    # UPnP IGD port forwarding (off by default; non-fatal if router lacks UPnP)
    UPNP_ENABLED: bool = False
    UPNP_LEASE_SECONDS: int = 3600
    UPNP_DISCOVERY_TIMEOUT_MS: int = 2000

    # NAT-PMP (RFC 6886) for VPN-provided port forwarding (ProtonVPN Plus, Mullvad, etc.)
    # Lets workers behind ISP CGNAT expose a public port via the VPN gateway.
    NATPMP_ENABLED: bool = False
    NATPMP_GATEWAY: str = "auto"  # "auto" | IPv4 literal (e.g. "10.2.0.1")
    NATPMP_LEASE_SECONDS: int = 60  # ProtonVPN default; renewed at 50%
    NATPMP_INITIAL_TIMEOUT_MS: int = 250  # RFC 6886 retry base (doubles each attempt)

    # Logging
    LOG_LEVEL: str = "INFO"

    # Worker GUI auto-update (GitHub Releases)
    RCFLOW_UPDATE_AUTO_CHECK: bool = True
    RCFLOW_UPDATE_LAST_CHECK: str = ""
    RCFLOW_UPDATE_CACHED_VERSION: str = ""
    RCFLOW_UPDATE_CACHED_RELEASE_URL: str = ""
    RCFLOW_UPDATE_CACHED_DOWNLOAD_URL: str = ""
    RCFLOW_UPDATE_CACHED_ASSET_NAME: str = ""
    RCFLOW_UPDATE_DISMISSED_VERSION: str = ""

    @property
    def projects_dirs(self) -> list[Path]:
        """Parse PROJECTS_DIR into a list of expanded, resolved Path objects."""
        raw = self.PROJECTS_DIR.strip()
        if not raw:
            return []
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return [Path(p).expanduser().resolve() for p in parts]


def _populate_missing_defaults(settings: Settings) -> None:
    """Write any Settings fields absent from settings.json with their current values.

    Called from :func:`get_settings` after security keys have been generated so
    that a fresh install produces a fully-populated settings.json on first run.
    Only keys that are not already present in the file are written; existing
    values are never overwritten.
    """
    path = _get_settings_path()
    existing: dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (PermissionError, json.JSONDecodeError):
            return

    missing: dict[str, str] = {}
    for field_name in Settings.model_fields:
        key = field_name.upper()
        if key not in existing:
            value = getattr(settings, field_name)
            if isinstance(value, Path):
                missing[key] = str(value)
            elif isinstance(value, bool):
                missing[key] = "true" if value else "false"
            else:
                missing[key] = str(value) if value is not None else ""

    if missing:
        update_settings_file(missing)


def get_settings() -> Settings:
    logger = logging.getLogger(__name__)
    settings = Settings()
    if not settings.RCFLOW_API_KEY:
        api_key = secrets.token_urlsafe(32)
        update_settings_file({"RCFLOW_API_KEY": api_key})
        settings.RCFLOW_API_KEY = api_key
        logger.info("Generated new RCFLOW_API_KEY: %s", api_key)
    if not settings.RCFLOW_BACKEND_ID:
        backend_id = str(uuid.uuid4())
        update_settings_file({"RCFLOW_BACKEND_ID": backend_id})
        settings.RCFLOW_BACKEND_ID = backend_id
    _populate_missing_defaults(settings)
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
            {"value": "none", "label": "None (Direct Tool Mode)"},
        ],
        "group": "LLM",
        "description": "LLM backend for inference. 'None' bypasses the LLM — use #tool_name to invoke tools directly.",
        "required": True,
        "restart_required": False,
    },
    {
        "key": "ANTHROPIC_API_KEY",
        "label": "Anthropic API Key",
        "type": "secret",
        "group": "LLM",
        "description": "API key for direct Anthropic API access",
        "required": False,
        "restart_required": False,
        "visible_when": {"key": "LLM_PROVIDER", "value": "anthropic"},
    },
    {
        "key": "ANTHROPIC_MODEL",
        "label": "Anthropic Model",
        "type": "model_select",
        "group": "LLM",
        "description": "Model ID (e.g. claude-sonnet-4-6). For Bedrock use Bedrock model IDs.",
        "required": False,
        "restart_required": False,
        "visible_when": {"key": "LLM_PROVIDER", "value_in": ["anthropic", "bedrock"]},
        "provider_key": "LLM_PROVIDER",
        "models": {
            "anthropic": PROVIDER_MODELS["anthropic"],
            "bedrock": PROVIDER_MODELS["bedrock"],
        },
        "dynamic": True,
        "fetch_endpoint": "/api/models",
        "fetch_scope": "global",
    },
    {
        "key": "AWS_REGION",
        "label": "AWS Region",
        "type": "string",
        "group": "LLM",
        "description": "AWS region for Bedrock (e.g. us-east-1)",
        "required": False,
        "restart_required": False,
        "visible_when": {"key": "LLM_PROVIDER", "value": "bedrock"},
    },
    {
        "key": "AWS_ACCESS_KEY_ID",
        "label": "AWS Access Key ID",
        "type": "secret",
        "group": "LLM",
        "description": "AWS access key for Bedrock authentication",
        "required": False,
        "restart_required": False,
        "visible_when": {"key": "LLM_PROVIDER", "value": "bedrock"},
    },
    {
        "key": "AWS_SECRET_ACCESS_KEY",
        "label": "AWS Secret Access Key",
        "type": "secret",
        "group": "LLM",
        "description": "AWS secret key for Bedrock authentication",
        "required": False,
        "restart_required": False,
        "visible_when": {"key": "LLM_PROVIDER", "value": "bedrock"},
    },
    {
        "key": "OPENAI_API_KEY",
        "label": "OpenAI API Key",
        "type": "secret",
        "group": "LLM",
        "description": "API key for OpenAI API access",
        "required": False,
        "restart_required": False,
        "visible_when": {"key": "LLM_PROVIDER", "value": "openai"},
    },
    {
        "key": "OPENAI_MODEL",
        "label": "OpenAI Model",
        "type": "model_select",
        "group": "LLM",
        "description": "OpenAI model ID (e.g. gpt-5.4, gpt-4.1, o3)",
        "required": False,
        "restart_required": False,
        "visible_when": {"key": "LLM_PROVIDER", "value": "openai"},
        "provider_key": "LLM_PROVIDER",
        "models": {
            "openai": PROVIDER_MODELS["openai"],
        },
        "dynamic": True,
        "fetch_endpoint": "/api/models",
        "fetch_scope": "global",
    },
    {
        "key": "TITLE_MODEL",
        "label": "Title Model",
        "type": "model_select",
        "group": "LLM",
        "description": "Model for session title generation (blank = use main model)",
        "required": False,
        "restart_required": False,
        "visible_when": {"key": "LLM_PROVIDER", "value_not": "none"},
        "provider_key": "LLM_PROVIDER",
        "models": {
            "anthropic": PROVIDER_MODELS["anthropic"],
            "bedrock": PROVIDER_MODELS["bedrock"],
            "openai": PROVIDER_MODELS["openai"],
        },
        "dynamic": True,
        "fetch_endpoint": "/api/models",
        "fetch_scope": "global",
    },
    {
        "key": "TASK_MODEL",
        "label": "Task Model",
        "type": "model_select",
        "group": "LLM",
        "description": "Model for task extraction and status evaluation (blank = use main model)",
        "required": False,
        "restart_required": False,
        "visible_when": {"key": "LLM_PROVIDER", "value_not": "none"},
        "provider_key": "LLM_PROVIDER",
        "models": {
            "anthropic": PROVIDER_MODELS["anthropic"],
            "bedrock": PROVIDER_MODELS["bedrock"],
            "openai": PROVIDER_MODELS["openai"],
        },
        "dynamic": True,
        "fetch_endpoint": "/api/models",
        "fetch_scope": "global",
    },
    # --- Prompt ---
    {
        "key": "GLOBAL_PROMPT",
        "label": "Global Prompt",
        "type": "textarea",
        "group": "Prompt",
        "description": (
            "Custom instructions appended to the system prompt for every session"
            " (e.g. language preferences, behavioral guidelines, domain expertise)"
        ),
        "required": False,
        "restart_required": False,
        "visible_when": {"key": "LLM_PROVIDER", "value_not": "none"},
    },
    {
        "key": "CAVEMAN_MODE",
        "label": "Caveman Mode",
        "type": "boolean",
        "group": "Prompt",
        "description": (
            "Compress LLM responses ~65-75% fewer tokens. Drops filler/articles/hedging; full technical accuracy kept."
        ),
        "required": False,
        "restart_required": False,
        "visible_when": {"key": "LLM_PROVIDER", "value_not": "none"},
    },
    {
        "key": "CAVEMAN_LEVEL",
        "label": "Caveman Level",
        "type": "select",
        "options": [
            {"value": "lite", "label": "Lite — no filler, keeps articles"},
            {"value": "full", "label": "Full — drops articles, fragments OK"},
            {"value": "ultra", "label": "Ultra — max compression, abbreviations"},
        ],
        "group": "Prompt",
        "description": "Compression intensity for caveman mode.",
        "required": False,
        "restart_required": False,
        "visible_when": {"key": "CAVEMAN_MODE", "value": "true"},
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
        "visible_when": {"key": "LLM_PROVIDER", "value_not": "none"},
    },
    {
        "key": "SESSION_OUTPUT_TOKEN_LIMIT",
        "label": "Output Token Limit",
        "type": "number",
        "group": "Session Limits",
        "description": "Maximum output tokens per session (0 = unlimited)",
        "required": False,
        "restart_required": False,
        "visible_when": {"key": "LLM_PROVIDER", "value_not": "none"},
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
    # --- Linear ---
    {
        "key": "LINEAR_API_KEY",
        "label": "Linear API Key",
        "type": "secret",
        "group": "Linear",
        "description": "Personal API token for Linear (create at linear.app → Settings → API)",
        "required": False,
        "restart_required": False,
    },
    {
        "key": "LINEAR_TEAM_ID",
        "label": "Linear Team ID (optional)",
        "type": "string",
        "group": "Linear",
        "description": "ID of the Linear team to sync issues from. Leave blank to sync from all accessible teams.",
        "required": False,
        "restart_required": False,
    },
    {
        "key": "LINEAR_SYNC_ON_STARTUP",
        "label": "Sync Issues on Startup",
        "type": "boolean",
        "group": "Linear",
        "description": "Automatically sync Linear issues when the server starts",
        "required": False,
        "restart_required": False,
    },
    # --- Networking ---
    {
        "key": "UPNP_ENABLED",
        "label": "UPnP Port Forwarding",
        "type": "boolean",
        "group": "Networking",
        "description": (
            "Ask the local router (via UPnP IGD) to forward an external port to this "
            "worker so remote clients can reach it without manual port forwarding. "
            "Silently skipped if the router does not support UPnP."
        ),
        "required": False,
        "restart_required": True,
    },
    {
        "key": "UPNP_LEASE_SECONDS",
        "label": "UPnP Lease Duration (seconds)",
        "type": "number",
        "group": "Networking",
        "description": (
            "How long the router should hold the mapping before expiry. "
            "0 = permanent (not all routers accept 0). Default 3600. "
            "The mapping is auto-renewed at 50% of this value."
        ),
        "required": False,
        "restart_required": True,
    },
    {
        "key": "NATPMP_ENABLED",
        "label": "VPN Port Forwarding (NAT-PMP)",
        "type": "boolean",
        "group": "Networking",
        "description": (
            "Ask the VPN gateway (e.g. ProtonVPN Plus on a P2P server, Mullvad) to forward "
            "an external port to this worker via NAT-PMP (RFC 6886).  Lets workers behind "
            "ISP CGNAT expose a public address through the VPN.  Silently skipped if no "
            "gateway responds."
        ),
        "required": False,
        "restart_required": True,
    },
    {
        "key": "NATPMP_GATEWAY",
        "label": "NAT-PMP Gateway",
        "type": "string",
        "group": "Networking",
        "description": (
            "VPN gateway IP that speaks NAT-PMP.  'auto' tries the ProtonVPN default "
            "(10.2.0.1), then the system default route.  Override with an explicit IPv4 "
            "for other providers (e.g. Mullvad)."
        ),
        "required": False,
        "restart_required": True,
    },
    {
        "key": "NATPMP_LEASE_SECONDS",
        "label": "NAT-PMP Lease Duration (seconds)",
        "type": "number",
        "group": "Networking",
        "description": (
            "How long the gateway should hold the mapping before expiry.  ProtonVPN "
            "enforces 60 s; the service renews at 50% of this value."
        ),
        "required": False,
        "restart_required": True,
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
        elif isinstance(raw_value, bool | int | float):
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

    If the settings file cannot be read or written due to permission errors,
    the environment variables are still updated in-memory but the file is
    left unchanged.
    """
    import os  # noqa: PLC0415

    path = _get_settings_path()
    data: dict[str, str] = {}

    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
    except PermissionError:
        pass

    for key, value in updates.items():
        data[key] = value
        os.environ[key.upper()] = value

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: write to a temp file first, then rename so a crash
        # mid-write cannot corrupt the settings file (F16 remediation).
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)
    except PermissionError:
        print(
            f"WARNING: Cannot write to {path} — permission denied. Settings applied in-memory only.",
            file=sys.stderr,
        )


_load_settings_into_env()
