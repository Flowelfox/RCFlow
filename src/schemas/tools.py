"""Pydantic schemas for tool definitions and executor configurations."""

from __future__ import annotations

import sys
from typing import Any

from pydantic import BaseModel, Field

_DEFAULT_SHELL = "powershell.exe" if sys.platform == "win32" else "/bin/bash"

# Map sys.platform values to the canonical os names used in tool definitions.
_PLATFORM_TO_OS: dict[str, str] = {
    "win32": "windows",
    "linux": "linux",
    "darwin": "darwin",
}
CURRENT_OS = _PLATFORM_TO_OS.get(sys.platform, sys.platform)


class ShellExecutorConfig(BaseModel):
    command_template: str
    shell: str = _DEFAULT_SHELL
    capture_stderr: bool = True
    stream_output: bool = True
    interactive: bool = False
    stdin_enabled: bool = False


class HttpExecutorConfig(BaseModel):
    method: str = "GET"
    url_template: str
    headers: dict[str, str] = Field(default_factory=dict)
    body_template: str | None = None
    timeout: int = 30
    response_path: str | None = None


class ClaudeCodeExecutorConfig(BaseModel):
    binary_path: str = "claude"
    default_permission_mode: str = "bypassPermissions"
    max_turns: int = 200
    timeout: int = 1800


class CodexExecutorConfig(BaseModel):
    binary_path: str = "codex"
    approval_mode: str = "full-auto"
    model: str = ""
    timeout: int = 600


class OpenCodeExecutorConfig(BaseModel):
    binary_path: str = "opencode"
    model: str = ""
    timeout: int = 600


class WorktreeExecutorConfig(BaseModel):
    default_base_branch: str = "main"
    validate_branch_type: bool = True


class ToolDefinition(BaseModel):
    name: str
    display_name: str = ""
    description: str
    version: str = "1.0.0"
    os: list[str] = Field(default_factory=list)
    session_type: str
    llm_context: str
    executor: str
    parameters: dict[str, Any]
    executor_config: dict[str, Any]

    @property
    def mention_name(self) -> str:
        """Human-readable mention name (PascalCase, no spaces).

        Derived from display_name by removing spaces. Falls back to name
        if display_name is empty. E.g. "Claude Code" -> "ClaudeCode".
        """
        if self.display_name:
            return self.display_name.replace(" ", "")
        return self.name

    def get_shell_config(self) -> ShellExecutorConfig:
        return ShellExecutorConfig(**self.executor_config["shell"])

    def get_http_config(self) -> HttpExecutorConfig:
        return HttpExecutorConfig(**self.executor_config["http"])

    def get_claude_code_config(self) -> ClaudeCodeExecutorConfig:
        return ClaudeCodeExecutorConfig(**self.executor_config["claude_code"])

    def get_codex_config(self) -> CodexExecutorConfig:
        return CodexExecutorConfig(**self.executor_config["codex"])

    def get_opencode_config(self) -> OpenCodeExecutorConfig:
        return OpenCodeExecutorConfig(**self.executor_config["opencode"])

    def get_worktree_config(self) -> WorktreeExecutorConfig:
        return WorktreeExecutorConfig(**self.executor_config.get("worktree", {}))
