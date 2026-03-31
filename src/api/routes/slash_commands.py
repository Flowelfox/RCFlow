"""Slash-commands API route — GET /slash-commands."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Query, Request

from src.api.deps import verify_http_api_key
from src.api.routes.rcflow_plugins import PluginStateManager
from src.paths import get_managed_cc_plugins_dir, get_managed_tools_dir

if TYPE_CHECKING:
    from src.config import Settings

router = APIRouter()
logger = logging.getLogger(__name__)

_description_re = re.compile(r"^description\s*:\s*(.+)$", re.MULTILINE)
_hide_re = re.compile(r"^hide-from-slash-command-tool\s*:\s*(.+)$", re.MULTILINE)

# ---------------------------------------------------------------------------
# Claude Code built-in command descriptions
# ---------------------------------------------------------------------------

# Fallback descriptions used when the claude binary is unavailable or the
# subprocess call fails.  These are kept in sync with Claude Code's own help
# text but are intentionally secondary — live descriptions sourced from Claude
# itself take precedence when available.
_FALLBACK_CC_BUILTINS: list[dict[str, str]] = [
    {"name": "help", "description": "Get help with using Claude Code", "source": "claude_code_builtin"},
    {"name": "clear", "description": "Clear conversation history", "source": "claude_code_builtin"},
    {"name": "compact", "description": "Compact conversation to save context", "source": "claude_code_builtin"},
    {"name": "cost", "description": "Show token usage and cost for session", "source": "claude_code_builtin"},
    {"name": "resume", "description": "Resume a previous Claude Code session", "source": "claude_code_builtin"},
    {"name": "init", "description": "Initialize project with CLAUDE.md", "source": "claude_code_builtin"},
    {"name": "bug", "description": "Report a bug in Claude Code", "source": "claude_code_builtin"},
    {"name": "pr-comments", "description": "Review and address PR comments", "source": "claude_code_builtin"},
    {"name": "permissions", "description": "Manage Claude Code permissions", "source": "claude_code_builtin"},
    {"name": "doctor", "description": "Run diagnostics on Claude Code setup", "source": "claude_code_builtin"},
    {"name": "vim", "description": "Toggle vim keybindings", "source": "claude_code_builtin"},
    {"name": "btw", "description": "Add an inline note or comment to the context", "source": "claude_code_builtin"},
]

# In-process cache: populated on the first successful fetch or fallback.
_cc_builtins_cache: list[dict[str, str]] | None = None

# Disk-cache path (version-keyed to stay fresh when Claude Code updates).
_CC_BUILTINS_CACHE_FILE = "cc_builtins_cache.json"


def _get_cc_version() -> str | None:
    """Return the installed Claude Code version string, or None."""
    binary = shutil.which("claude")
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def _load_disk_cache() -> list[dict[str, str]] | None:
    """Load the on-disk cache if it exists and matches the current CC version."""
    try:
        cache_path = get_managed_tools_dir() / _CC_BUILTINS_CACHE_FILE
        if not cache_path.is_file():
            return None
        data: dict[str, Any] = json.loads(cache_path.read_text(encoding="utf-8"))
        current_version = _get_cc_version()
        # Invalidate if the CC version has changed.
        if current_version and data.get("version") != current_version:
            return None
        commands: list[dict[str, str]] = data.get("commands", [])
        return commands if commands else None
    except Exception:
        return None


def _save_disk_cache(commands: list[dict[str, str]], version: str | None) -> None:
    """Persist descriptions to disk so they survive server restarts."""
    try:
        cache_path = get_managed_tools_dir() / _CC_BUILTINS_CACHE_FILE
        cache_path.write_text(
            json.dumps({"version": version, "commands": commands}, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


async def _fetch_from_claude(binary: str) -> list[dict[str, str]]:
    """Invoke ``claude -p`` to retrieve built-in slash-command descriptions.

    Sends a tightly scoped prompt asking Claude to output only a JSON object
    mapping command names to descriptions.  Returns an empty list if the
    subprocess fails, times out, or returns unparseable output.
    """
    prompt = (
        "Output ONLY a valid JSON object — no markdown, no prose, no code fences. "
        "Each key must be a built-in slash command name (without the leading /) "
        "and each value must be a concise one-line description of what that command does. "
        "Include every built-in slash command: "
        "help, clear, compact, cost, resume, init, bug, pr-comments, permissions, doctor, vim, btw."
    )
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            "-p",
            "--no-session-persistence",
            "--output-format", "text",
            "--max-budget-usd", "0.05",
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=45.0)
        text = stdout.decode("utf-8", errors="replace").strip()
        if not text:
            return []

        # Claude may wrap its response in a markdown code fence — strip it.
        text = re.sub(r"^```[^\n]*\n", "", text)
        text = re.sub(r"\n```$", "", text)
        text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to extract the first JSON object embedded in surrounding text.
            m = re.search(r"\{[^{}]+\}", text, re.DOTALL)
            if not m:
                logger.debug("cc_builtins: could not find JSON object in claude output")
                return []
            data = json.loads(m.group())

        if not isinstance(data, dict):
            return []

        commands: list[dict[str, str]] = []
        for name, desc in data.items():
            if isinstance(name, str) and isinstance(desc, str) and name.strip():
                commands.append({
                    "name": name.strip().lstrip("/"),
                    "description": desc.strip(),
                    "source": "claude_code_builtin",
                })
        return commands
    except (TimeoutError, Exception) as exc:
        logger.debug("cc_builtins: fetch failed — %s", exc)
        return []


async def _get_cc_builtins() -> list[dict[str, str]]:
    """Return Claude Code built-in command descriptions.

    Resolution order:
    1. In-process memory cache (fastest — set after first resolution).
    2. On-disk cache keyed by the installed Claude Code version.
    3. Live fetch via ``claude -p`` (makes one API call, then caches).
    4. Hard-coded fallback (used when the binary is absent or unreachable).
    """
    global _cc_builtins_cache

    if _cc_builtins_cache is not None:
        return _cc_builtins_cache

    # Disk cache — avoids an API call across server restarts.
    cached = _load_disk_cache()
    if cached:
        _cc_builtins_cache = cached
        return _cc_builtins_cache

    # Live fetch via claude -p.
    binary = shutil.which("claude")
    if binary:
        fetched = await _fetch_from_claude(binary)
        if fetched:
            version = _get_cc_version()
            _save_disk_cache(fetched, version)
            _cc_builtins_cache = fetched
            return _cc_builtins_cache

    # Final fallback: hard-coded descriptions.
    _cc_builtins_cache = _FALLBACK_CC_BUILTINS
    return _cc_builtins_cache


# ---------------------------------------------------------------------------
# Plugin-level Claude Code commands
# ---------------------------------------------------------------------------

# Canonical paths for the Claude Code plugin registry.
_INSTALLED_PLUGINS_FILE = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
_CC_SETTINGS_FILE = Path.home() / ".claude" / "settings.json"


def _get_enabled_plugin_keys() -> set[str]:
    """Return the set of plugin keys enabled in ~/.claude/settings.json.

    Reads the ``enabledPlugins`` map (``{ "name@marketplace": true/false }``) and
    returns only the keys whose value is ``true``.  Returns an empty set when the
    file is missing, unreadable, or has no ``enabledPlugins`` entry.
    """
    try:
        data: dict = json.loads(_CC_SETTINGS_FILE.read_text(encoding="utf-8"))
        enabled: dict[str, bool] = data.get("enabledPlugins", {})
        return {k for k, v in enabled.items() if v}
    except Exception:
        return set()


def _get_installed_plugin_paths() -> dict[str, Path]:
    """Return a mapping of plugin key → ``installPath`` from installed_plugins.json.

    The registry can hold multiple version entries per plugin; the last entry
    (most recently installed/updated) is used.  Returns an empty dict when the
    file is missing or cannot be parsed.
    """
    try:
        data: dict = json.loads(_INSTALLED_PLUGINS_FILE.read_text(encoding="utf-8"))
        plugins: dict[str, list[dict]] = data.get("plugins", {})
        result: dict[str, Path] = {}
        for key, entries in plugins.items():
            if not entries:
                continue
            install_path = entries[-1].get("installPath")
            if install_path:
                result[key] = Path(install_path)
        return result
    except Exception:
        return {}


def _parse_plugin_command(path: Path, plugin_name: str) -> dict[str, str] | None:
    """Parse a plugin skill ``.md`` file into a command dict.

    Returns ``None`` if:
    - the file cannot be read, or
    - the frontmatter contains ``hide-from-slash-command-tool: "true"`` (Claude
      Code uses this flag to suppress internal helper commands from autocomplete).

    The ``description`` value has surrounding quotes stripped so that both
    ``description: My skill`` and ``description: "My skill"`` render the same.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        description = ""
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                frontmatter = text[3:end]
                h = _hide_re.search(frontmatter)
                if h and h.group(1).strip().strip('"\'').lower() == "true":
                    return None
                m = _description_re.search(frontmatter)
                if m:
                    description = m.group(1).strip().strip('"\'')
        return {
            "name": path.stem,
            "description": description,
            "source": "claude_code_plugin",
            "plugin": plugin_name,
        }
    except OSError:
        return None


def _get_plugin_commands() -> list[dict[str, str]]:
    """Return slash commands contributed by installed and enabled Claude Code plugins.

    Resolution:
    1. Read ``enabledPlugins`` from ``~/.claude/settings.json`` — plugins not
       listed there (or listed as ``false``) are skipped.
    2. Read ``~/.claude/plugins/installed_plugins.json`` — maps each plugin key to
       its ``installPath`` on disk.
    3. For each enabled plugin, enumerate ``<installPath>/commands/*.md`` and parse
       each file with :func:`_parse_plugin_command`.  Files whose frontmatter
       contains ``hide-from-slash-command-tool: "true"`` are excluded.
    4. Command names are deduplicated across plugins (first occurrence wins).
    """
    enabled_keys = _get_enabled_plugin_keys()
    if not enabled_keys:
        return []

    install_paths = _get_installed_plugin_paths()
    commands: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for key in sorted(enabled_keys):
        install_path = install_paths.get(key)
        if not install_path:
            continue
        commands_dir = install_path / "commands"
        if not commands_dir.is_dir():
            continue

        # Human-readable plugin name from the key (e.g. "code-review@marketplace" → "code-review").
        plugin_name = key.split("@")[0]

        for md_file in sorted(commands_dir.glob("*.md")):
            if md_file.name.endswith(":Zone.Identifier"):
                continue
            if md_file.stem in seen_names:
                continue
            cmd = _parse_plugin_command(md_file, plugin_name)
            if cmd:
                seen_names.add(md_file.stem)
                commands.append(cmd)

    return commands


def _get_rcflow_plugin_commands() -> list[dict[str, str]]:
    """Return slash commands from RCFlow-managed Claude Code plugins.

    Scans ``<managed_tools_dir>/claude-code/plugins/`` for plugin subdirectories,
    each of which may contain a ``commands/*.md`` folder.  Commands are parsed
    with :func:`_parse_plugin_command` (so ``hide-from-slash-command-tool`` and
    quote-stripping apply), and are returned with ``"source": "rcflow_plugin"``.

    Plugins listed as disabled in the tool's ``plugins_state.json`` file are
    excluded entirely from the returned command list.

    This directory is distinct from the user's own Claude Code plugin registry
    (``~/.claude/plugins/``) and is intended for plugins curated or installed by
    RCFlow itself.
    """
    plugins_dir = get_managed_cc_plugins_dir()
    if not plugins_dir.is_dir():
        return []

    # Read the disabled-plugin set once before iterating.
    disabled = PluginStateManager(plugins_dir).get_disabled()

    commands: list[dict[str, str]] = []
    seen_names: set[str] = set()

    for plugin_dir in sorted(plugins_dir.iterdir()):
        if not plugin_dir.is_dir():
            continue
        if plugin_dir.name in disabled:
            continue
        commands_dir = plugin_dir / "commands"
        if not commands_dir.is_dir():
            continue
        for md_file in sorted(commands_dir.glob("*.md")):
            if md_file.name.endswith(":Zone.Identifier"):
                continue
            if md_file.stem in seen_names:
                continue
            cmd = _parse_plugin_command(md_file, plugin_dir.name)
            if cmd:
                cmd["source"] = "rcflow_plugin"
                seen_names.add(md_file.stem)
                commands.append(cmd)

    return commands


# ---------------------------------------------------------------------------
# User / project skill parsing
# ---------------------------------------------------------------------------

def _parse_cc_command(path: Path, source: str) -> dict[str, str] | None:
    """Parse a Claude Code skill .md file and return a command dict."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        description = ""
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                frontmatter = text[3:end]
                m = _description_re.search(frontmatter)
                if m:
                    description = m.group(1).strip()
        return {"name": path.stem, "description": description, "source": source}
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.get(
    "/slash-commands",
    summary="List available slash commands",
    description=(
        "Returns all slash commands available in the message input, combining RCFlow "
        "built-in commands and Claude Code commands (built-ins plus user/project-level "
        "skills from ~/.claude/commands/ and .claude/commands/, plus commands contributed "
        "by installed and enabled Claude Code plugins). "
        "Descriptions for Claude Code built-in commands are sourced live from Claude "
        "itself via a one-time subprocess call and cached on disk; a hard-coded fallback "
        "is used when the binary is unavailable. "
        "Each command has a 'source' field: 'rcflow', 'claude_code_builtin', "
        "'claude_code_user', 'claude_code_project', 'claude_code_plugin', or 'rcflow_plugin'. "
        "Plugin commands (both sources) carry a 'plugin' field with the plugin's short name. "
        "Optionally filters by a case-insensitive substring match on the command name."
    ),
    dependencies=[Depends(verify_http_api_key)],
)
async def list_slash_commands(
    request: Request,
    q: str | None = Query(None, description="Case-insensitive substring filter for command names"),
) -> dict[str, Any]:
    """Return all slash commands grouped by source."""
    settings: Settings = request.app.state.settings

    commands: list[dict[str, str]] = []

    # --- RCFlow built-in commands ---
    rcflow_commands = [
        {"name": "clear",   "description": "Clear chat messages in this pane",           "source": "rcflow"},
        {"name": "new",     "description": "Start a new session",                        "source": "rcflow"},
        {"name": "help",    "description": "Show RCFlow tips and help",                  "source": "rcflow"},
        {"name": "pause",   "description": "Pause the current session",                  "source": "rcflow"},
        {"name": "resume",  "description": "Resume the paused session",                  "source": "rcflow"},
        {"name": "plugins", "description": "Open plugin settings for the active coding agent", "source": "rcflow"},
    ]
    commands.extend(rcflow_commands)

    # --- Claude Code built-in slash commands ---
    # Descriptions are sourced from Claude itself (via a cached claude -p call)
    # rather than being hardcoded here.
    commands.extend(await _get_cc_builtins())

    # --- User-level Claude Code commands: ~/.claude/commands/*.md ---
    user_commands_dir = Path.home() / ".claude" / "commands"
    if user_commands_dir.is_dir():
        for md_file in sorted(user_commands_dir.glob("*.md")):
            if md_file.name.endswith(":Zone.Identifier"):
                continue
            cmd = _parse_cc_command(md_file, "claude_code_user")
            if cmd:
                commands.append(cmd)

    # --- Project-level Claude Code commands: <projects_dir>/*/.claude/commands/*.md ---
    seen_project_commands: set[str] = set()
    for projects_dir in settings.projects_dirs:
        if not projects_dir.is_dir():
            continue
        project_commands_dir = projects_dir.parent / ".claude" / "commands"
        if project_commands_dir.is_dir():
            for md_file in sorted(project_commands_dir.glob("*.md")):
                if md_file.stem in seen_project_commands:
                    continue
                if md_file.name.endswith(":Zone.Identifier"):
                    continue
                cmd = _parse_cc_command(md_file, "claude_code_project")
                if cmd:
                    seen_project_commands.add(md_file.stem)
                    commands.append(cmd)

    # --- Installed Claude Code plugin commands (from ~/.claude/plugins/) ---
    commands.extend(_get_plugin_commands())

    # --- RCFlow-managed Claude Code plugin commands ---
    commands.extend(_get_rcflow_plugin_commands())

    # --- Apply query filter ---
    if q:
        q_lower = q.lower()
        commands = [c for c in commands if q_lower in c["name"].lower()]

    return {"commands": commands}
