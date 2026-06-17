"""Smoke tests for the Pydantic request/response schemas.

These modules are pure model declarations; importing them executes the class
and field definitions, and instantiating representative models exercises the
defaults and the few helper methods (notably ``ToolDefinition``). The goal is
to catch a broken model definition (bad default, invalid annotation) early.
"""

from __future__ import annotations

import importlib

import pytest

from src.schemas.linear import CreateIssueRequest
from src.schemas.sessions import DraftUpsertRequest
from src.schemas.tasks import CreateTaskRequest
from src.schemas.tools import ToolDefinition
from src.schemas.worktrees import CreateWorktreeRequest

_SCHEMA_MODULES = [
    "src.schemas.artifacts",
    "src.schemas.auth",
    "src.schemas.config",
    "src.schemas.linear",
    "src.schemas.plugins",
    "src.schemas.sessions",
    "src.schemas.tasks",
    "src.schemas.tool_settings",
    "src.schemas.tools",
    "src.schemas.worktrees",
]


@pytest.mark.parametrize("module_path", _SCHEMA_MODULES)
def test_schema_module_imports(module_path):
    """Every schema module imports cleanly (definitions execute without error)."""
    mod = importlib.import_module(module_path)
    assert mod is not None


def test_linear_create_issue_defaults():
    req = CreateIssueRequest(title="Bug")
    assert req.title == "Bug"


def test_tasks_create_request_roundtrip():
    req = CreateTaskRequest.model_validate({"title": "Do thing"})
    assert req.title == "Do thing"


def test_sessions_draft_upsert_request():
    req = DraftUpsertRequest(content="wip note")
    assert req.content == "wip note"


def test_worktrees_create_request():
    req = CreateWorktreeRequest.model_validate({"repo_path": "/repo", "branch": "feat"})
    assert req.repo_path == "/repo"
    assert req.branch == "feat"


# ---------------------------------------------------------------------------
# tools.ToolDefinition helpers
# ---------------------------------------------------------------------------


def _tool_definition(**overrides):
    data = {
        "name": "shell",
        "display_name": "Shell Tool",
        "description": "Runs shell commands",
        "session_type": "tool",
        "llm_context": "ctx",
        "executor": "shell",
        "parameters": {},
        "executor_config": {
            "shell": {"command_template": "{cmd}"},
            "http": {"url_template": "https://x"},
            "claude_code": {},
            "codex": {},
            "opencode": {},
            "worktree": {},
        },
    }
    data.update(overrides)
    return ToolDefinition(**data)


def test_mention_name_strips_spaces_from_display_name():
    assert _tool_definition(display_name="Claude Code").mention_name == "ClaudeCode"


def test_mention_name_falls_back_to_name():
    assert _tool_definition(name="codex", display_name="").mention_name == "codex"


def test_get_executor_configs_parse_their_sections():
    td = _tool_definition()
    assert td.get_shell_config().command_template == "{cmd}"
    assert td.get_http_config().url_template == "https://x"
    # The remaining executor configs build from their (defaulted) sections.
    td.get_claude_code_config()
    td.get_codex_config()
    td.get_opencode_config()
    td.get_worktree_config()
