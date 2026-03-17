import json
import logging
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

VALID_EXECUTORS = {"shell", "http", "claude_code", "codex", "worktree"}
VALID_SESSION_TYPES = {"one-shot", "long-running"}
VALID_LLM_CONTEXTS = {"stateless", "session-scoped"}
VALID_OS = {"windows", "linux", "darwin"}

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

    def get_worktree_config(self) -> WorktreeExecutorConfig:
        return WorktreeExecutorConfig(**self.executor_config.get("worktree", {}))


def load_tool_file(path: Path) -> ToolDefinition:
    with open(path) as f:
        data = json.load(f)

    tool = ToolDefinition(**data)

    if tool.executor not in VALID_EXECUTORS:
        raise ValueError(f"Tool '{tool.name}': invalid executor '{tool.executor}'. Must be one of {VALID_EXECUTORS}")
    if tool.session_type not in VALID_SESSION_TYPES:
        raise ValueError(
            f"Tool '{tool.name}': invalid session_type '{tool.session_type}'. Must be one of {VALID_SESSION_TYPES}"
        )
    if tool.llm_context not in VALID_LLM_CONTEXTS:
        raise ValueError(
            f"Tool '{tool.name}': invalid llm_context '{tool.llm_context}'. Must be one of {VALID_LLM_CONTEXTS}"
        )
    if tool.executor not in tool.executor_config:
        raise ValueError(f"Tool '{tool.name}': executor_config must contain a key matching executor '{tool.executor}'")
    for os_val in tool.os:
        if os_val not in VALID_OS:
            raise ValueError(f"Tool '{tool.name}': invalid os value '{os_val}'. Must be one of {VALID_OS}")

    return tool


def load_tools_from_directory(tools_dir: Path) -> list[ToolDefinition]:
    tools: list[ToolDefinition] = []

    if not tools_dir.is_dir():
        logger.warning("Tools directory '%s' does not exist, no tools loaded", tools_dir)
        return tools

    for path in sorted(tools_dir.glob("*.json")):
        try:
            tool = load_tool_file(path)
            if tool.os and CURRENT_OS not in tool.os:
                logger.info("Skipping tool '%s' from %s (requires os=%s, current=%s)", tool.name, path.name, tool.os, CURRENT_OS)
                continue
            tools.append(tool)
            logger.info("Loaded tool '%s' from %s", tool.name, path.name)
        except Exception:
            logger.exception("Failed to load tool from %s", path)

    logger.info("Loaded %d tool(s) from %s", len(tools), tools_dir)
    return tools
