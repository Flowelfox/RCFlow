"""Slash-commands API route — GET /slash-commands."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from src.api.deps import verify_http_api_key
from src.config import Settings

router = APIRouter()

_description_re = re.compile(r"^description\s*:\s*(.+)$", re.MULTILINE)


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


@router.get(
    "/slash-commands",
    summary="List available slash commands",
    description=(
        "Returns all slash commands available in the message input, combining RCFlow "
        "built-in commands and Claude Code commands (built-ins plus user/project-level "
        "skills from ~/.claude/commands/ and .claude/commands/). "
        "Each command has a 'source' field: 'rcflow', 'claude_code_builtin', "
        "'claude_code_user', or 'claude_code_project'. "
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
        {"name": "clear",  "description": "Clear chat messages in this pane",  "source": "rcflow"},
        {"name": "new",    "description": "Start a new session",               "source": "rcflow"},
        {"name": "help",   "description": "Show RCFlow tips and help",         "source": "rcflow"},
        {"name": "pause",  "description": "Pause the current session",         "source": "rcflow"},
        {"name": "resume", "description": "Resume the paused session",         "source": "rcflow"},
    ]
    commands.extend(rcflow_commands)

    # --- Claude Code built-in slash commands ---
    cc_builtins = [
        {"name": "help",        "description": "Show Claude Code help and documentation", "source": "claude_code_builtin"},
        {"name": "clear",       "description": "Clear conversation history",              "source": "claude_code_builtin"},
        {"name": "compact",     "description": "Compact conversation to save context",    "source": "claude_code_builtin"},
        {"name": "cost",        "description": "Show token usage and cost for session",   "source": "claude_code_builtin"},
        {"name": "resume",      "description": "Resume a previous Claude Code session",   "source": "claude_code_builtin"},
        {"name": "init",        "description": "Initialize project with CLAUDE.md",       "source": "claude_code_builtin"},
        {"name": "bug",         "description": "Report a bug in Claude Code",             "source": "claude_code_builtin"},
        {"name": "pr-comments", "description": "Review and address PR comments",          "source": "claude_code_builtin"},
        {"name": "permissions", "description": "Manage Claude Code permissions",          "source": "claude_code_builtin"},
        {"name": "doctor",      "description": "Run diagnostics on Claude Code setup",    "source": "claude_code_builtin"},
        {"name": "vim",         "description": "Toggle vim keybindings",                  "source": "claude_code_builtin"},
    ]
    commands.extend(cc_builtins)

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

    # --- Apply query filter ---
    if q:
        q_lower = q.lower()
        commands = [c for c in commands if q_lower in c["name"].lower()]

    return {"commands": commands}
