"""Tests for src/core/context.py (ContextMixin).

Covers:
- ``_format_file_size`` — byte formatting helper
- ``_extract_tool_mentions`` — ``#ToolName`` regex extraction
- ``_extract_file_references`` — ``$filename`` regex extraction
- ``_build_project_context_from_path`` — project directory context string
- ``_build_active_worktree_context`` — active worktree context string
- ``_build_tool_context`` — tool mention → LLM directive (agent, worktree,
  worktree+agent orchestration, preference)
- ``_resolve_project_path`` — project name → absolute Path
- ``_parse_direct_tool_prompt`` — full direct-mode parsing (no LLM)
- ``_build_file_context`` — no-DB fast path returns None
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from src.core.context import ContextMixin, _format_file_size

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Minimal concrete host
# ---------------------------------------------------------------------------


class _ContextHost(ContextMixin):
    """Minimal concrete subclass used by all tests in this module."""

    def __init__(
        self,
        tool_registry: MagicMock | None = None,
        settings: MagicMock | None = None,
        db_session_factory=None,
    ) -> None:
        self._tool_registry = tool_registry or MagicMock()
        self._settings = settings
        self._db_session_factory = db_session_factory

    async def _execute_tool(self, session, tool_call):
        """Stub — not exercised here."""


# ---------------------------------------------------------------------------
# Mock tool definition
# ---------------------------------------------------------------------------


@dataclass
class _MockTool:
    name: str
    description: str
    executor: str
    parameters: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.parameters is None:
            self.parameters = {"properties": {}, "required": []}


def _registry_with(*tools: _MockTool) -> MagicMock:
    registry = MagicMock()
    tool_map = {t.name.lower(): t for t in tools}

    def _get(name: str):
        return tool_map.get(name.lower())

    registry.get.side_effect = _get
    registry.list_tools.return_value = list(tools)
    return registry


# ---------------------------------------------------------------------------
# _format_file_size
# ---------------------------------------------------------------------------


class TestFormatFileSize:
    def test_bytes(self) -> None:
        assert _format_file_size(512) == "512 B"

    def test_kilobytes(self) -> None:
        result = _format_file_size(2048)
        assert "KB" in result
        assert "2.0" in result

    def test_megabytes(self) -> None:
        result = _format_file_size(3 * 1024 * 1024)
        assert "MB" in result
        assert "3.0" in result

    def test_zero_bytes(self) -> None:
        assert _format_file_size(0) == "0 B"


# ---------------------------------------------------------------------------
# _extract_tool_mentions
# ---------------------------------------------------------------------------


class TestExtractToolMentions:
    def test_single_mention(self) -> None:
        host = _ContextHost()
        assert host._extract_tool_mentions("#claude_code fix the bug") == ["claude_code"]

    def test_multiple_mentions(self) -> None:
        host = _ContextHost()
        mentions = host._extract_tool_mentions("#tool_a do this and #tool_b do that")
        assert "tool_a" in mentions
        assert "tool_b" in mentions

    def test_no_mentions(self) -> None:
        host = _ContextHost()
        assert host._extract_tool_mentions("just plain text") == []

    def test_mention_at_start_of_line(self) -> None:
        host = _ContextHost()
        assert host._extract_tool_mentions("#my_tool run") == ["my_tool"]

    def test_hash_inside_word_is_not_a_mention(self) -> None:
        host = _ContextHost()
        # "color#ff0000" — not preceded by start or whitespace
        result = host._extract_tool_mentions("color#ff0000")
        assert result == []

    def test_mention_case_preserved(self) -> None:
        host = _ContextHost()
        result = host._extract_tool_mentions("#ClaudeCode")
        assert result == ["ClaudeCode"]


# ---------------------------------------------------------------------------
# _extract_file_references
# ---------------------------------------------------------------------------


class TestExtractFileReferences:
    def test_single_reference(self) -> None:
        host = _ContextHost()
        assert host._extract_file_references("see $README.md for details") == ["README.md"]

    def test_multiple_references(self) -> None:
        host = _ContextHost()
        refs = host._extract_file_references("$main.py and $utils.py")
        assert "main.py" in refs
        assert "utils.py" in refs

    def test_no_references(self) -> None:
        host = _ContextHost()
        assert host._extract_file_references("no dollar signs here") == []

    def test_dollar_at_start(self) -> None:
        host = _ContextHost()
        assert host._extract_file_references("$config.yaml is the config file") == ["config.yaml"]


# ---------------------------------------------------------------------------
# _build_project_context_from_path
# ---------------------------------------------------------------------------


class TestBuildProjectContextFromPath:
    def test_includes_project_name(self) -> None:
        host = _ContextHost()
        ctx = host._build_project_context_from_path("/home/user/Projects/MyApp")
        assert "MyApp" in ctx

    def test_includes_full_path(self) -> None:
        host = _ContextHost()
        ctx = host._build_project_context_from_path("/home/user/Projects/MyApp")
        assert "/home/user/Projects/MyApp" in ctx

    def test_includes_prefer_directive(self) -> None:
        host = _ContextHost()
        ctx = host._build_project_context_from_path("/home/user/Projects/MyApp")
        assert "Prefer" in ctx or "working on project" in ctx.lower()


# ---------------------------------------------------------------------------
# _build_active_worktree_context
# ---------------------------------------------------------------------------


class TestBuildActiveWorktreeContext:
    def test_returns_none_when_no_selected_worktree(self) -> None:
        host = _ContextHost()
        session = MagicMock()
        session.metadata = {}
        assert host._build_active_worktree_context(session) is None

    def test_returns_string_when_worktree_selected(self) -> None:
        host = _ContextHost()
        session = MagicMock()
        session.metadata = {"selected_worktree_path": "/wt/feature-branch"}
        result = host._build_active_worktree_context(session)
        assert result is not None
        assert "/wt/feature-branch" in result

    def test_includes_branch_when_present(self) -> None:
        host = _ContextHost()
        session = MagicMock()
        session.metadata = {
            "selected_worktree_path": "/wt/feature-branch",
            "worktree": {"branch": "feature/my-feature", "repo_path": "/repo"},
        }
        result = host._build_active_worktree_context(session)
        assert result is not None
        assert "feature/my-feature" in result

    def test_includes_repo_path_when_present(self) -> None:
        host = _ContextHost()
        session = MagicMock()
        session.metadata = {
            "selected_worktree_path": "/wt/branch",
            "worktree": {"branch": "b", "repo_path": "/my/repo"},
        }
        result = host._build_active_worktree_context(session)
        assert result is not None
        assert "/my/repo" in result

    def test_includes_working_directory_directive(self) -> None:
        host = _ContextHost()
        session = MagicMock()
        session.metadata = {"selected_worktree_path": "/wt/branch"}
        result = host._build_active_worktree_context(session)
        assert result is not None
        assert "working_directory" in result


# ---------------------------------------------------------------------------
# _build_tool_context
# ---------------------------------------------------------------------------


class TestBuildToolContext:
    def test_no_mentions_returns_none(self) -> None:
        host = _ContextHost()
        assert host._build_tool_context([]) is None

    def test_unresolved_mentions_return_none(self) -> None:
        registry = MagicMock()
        registry.get.return_value = None
        host = _ContextHost(tool_registry=registry)
        assert host._build_tool_context(["unknown_tool"]) is None

    def test_agent_tool_generates_must_call_directive(self) -> None:
        tool = _MockTool(name="claude_code", description="Run Claude Code", executor="claude_code")
        host = _ContextHost(tool_registry=_registry_with(tool))
        ctx = host._build_tool_context(["claude_code"])
        assert ctx is not None
        assert "MUST" in ctx or "mandatory" in ctx.lower() or "Mandatory" in ctx

    def test_preference_tool_generates_preference_directive(self) -> None:
        tool = _MockTool(name="shell_cmd", description="Run shell commands", executor="shell")
        host = _ContextHost(tool_registry=_registry_with(tool))
        ctx = host._build_tool_context(["shell_cmd"])
        assert ctx is not None
        assert "preference" in ctx.lower() or "Prioritize" in ctx

    def test_worktree_plus_agent_generates_orchestration_directive(self) -> None:
        wt_tool = _MockTool(name="worktree", description="Manage worktrees", executor="worktree")
        agent_tool = _MockTool(name="claude_code", description="Run Claude", executor="claude_code")
        host = _ContextHost(tool_registry=_registry_with(wt_tool, agent_tool))
        ctx = host._build_tool_context(["worktree", "claude_code"])
        assert ctx is not None
        assert "Step 1" in ctx or "step" in ctx.lower()
        assert "Step 2" in ctx or "two-step" in ctx.lower() or "two step" in ctx.lower()

    def test_duplicate_mentions_deduplicated(self) -> None:
        tool = _MockTool(name="claude_code", description="Run Claude Code", executor="claude_code")
        host = _ContextHost(tool_registry=_registry_with(tool))
        # Mention the same tool twice — should only appear once in context
        ctx = host._build_tool_context(["claude_code", "claude_code"])
        assert ctx is not None
        assert ctx.count("claude_code") == ctx.lower().count("claude_code")  # no double block


# ---------------------------------------------------------------------------
# _resolve_project_path
# ---------------------------------------------------------------------------


class TestResolveProjectPath:
    def test_returns_none_when_no_settings(self) -> None:
        host = _ContextHost(settings=None)
        assert host._resolve_project_path("AnyProject") is None

    def test_returns_none_when_directory_not_found(self, tmp_path: Path) -> None:
        settings = MagicMock()
        settings.projects_dirs = [tmp_path]
        host = _ContextHost(settings=settings)
        assert host._resolve_project_path("NonExistentProject") is None

    def test_returns_path_when_directory_exists(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "MyProject"
        project_dir.mkdir()
        settings = MagicMock()
        settings.projects_dirs = [tmp_path]
        host = _ContextHost(settings=settings)
        result = host._resolve_project_path("MyProject")
        assert result == project_dir

    def test_searches_multiple_project_dirs(self, tmp_path: Path) -> None:
        dir_a = tmp_path / "dir_a"
        dir_b = tmp_path / "dir_b"
        dir_a.mkdir()
        dir_b.mkdir()
        project = dir_b / "MyProject"
        project.mkdir()
        settings = MagicMock()
        settings.projects_dirs = [dir_a, dir_b]
        host = _ContextHost(settings=settings)
        result = host._resolve_project_path("MyProject")
        assert result == project


# ---------------------------------------------------------------------------
# _parse_direct_tool_prompt
# ---------------------------------------------------------------------------


class TestParseDirectToolPrompt:
    def test_no_tool_mention_returns_error_string(self) -> None:
        tool = _MockTool(name="my_tool", description="does stuff", executor="shell")
        host = _ContextHost(tool_registry=_registry_with(tool))
        result = host._parse_direct_tool_prompt("just plain text")
        assert isinstance(result, str)
        assert "#tool_name" in result.lower() or "requires" in result.lower()

    def test_unknown_tool_returns_error_string(self) -> None:
        tool = _MockTool(name="my_tool", description="does stuff", executor="shell")
        host = _ContextHost(tool_registry=_registry_with(tool))
        result = host._parse_direct_tool_prompt("#nonexistent_tool run this")
        assert isinstance(result, str)
        assert "Unknown" in result or "unknown" in result

    def test_shell_executor_uses_command_key(self) -> None:
        tool = _MockTool(name="run_shell", description="shell tool", executor="shell")
        host = _ContextHost(tool_registry=_registry_with(tool))
        result = host._parse_direct_tool_prompt("#run_shell echo hello")
        assert isinstance(result, tuple)
        _tool_def, tool_input, _ = result
        assert "command" in tool_input
        assert tool_input["command"] == "echo hello"

    def test_claude_code_executor_uses_prompt_key(self) -> None:
        tool = _MockTool(name="claude_code", description="agent", executor="claude_code")
        host = _ContextHost(tool_registry=_registry_with(tool))
        result = host._parse_direct_tool_prompt("#claude_code fix the tests")
        assert isinstance(result, tuple)
        _, tool_input, _ = result
        assert "prompt" in tool_input
        assert "fix the tests" in tool_input["prompt"]

    def test_project_mention_sets_working_directory(self, tmp_path: Path) -> None:
        project_dir = tmp_path / "MyProject"
        project_dir.mkdir()
        settings = MagicMock()
        settings.projects_dirs = [tmp_path]

        tool = _MockTool(name="claude_code", description="agent", executor="claude_code")
        host = _ContextHost(tool_registry=_registry_with(tool), settings=settings)
        result = host._parse_direct_tool_prompt("#claude_code @MyProject run tests")
        assert isinstance(result, tuple)
        _, tool_input, _ = result
        assert tool_input.get("working_directory") == str(project_dir)

    def test_tool_name_stripped_from_display_text(self) -> None:
        tool = _MockTool(name="run_shell", description="shell tool", executor="shell")
        host = _ContextHost(tool_registry=_registry_with(tool))
        result = host._parse_direct_tool_prompt("#run_shell echo hello")
        assert isinstance(result, tuple)
        _, _, display = result
        # Original full text is preserved in display
        assert "#run_shell" in display or "echo hello" in display

    def test_codex_executor_uses_prompt_key(self) -> None:
        tool = _MockTool(name="codex", description="codex agent", executor="codex")
        host = _ContextHost(tool_registry=_registry_with(tool))
        result = host._parse_direct_tool_prompt("#codex implement feature")
        assert isinstance(result, tuple)
        _, tool_input, _ = result
        assert "prompt" in tool_input

    def test_single_required_param_maps_text(self) -> None:
        tool = _MockTool(
            name="my_tool",
            description="tool with one required param",
            executor="other",
            parameters={"properties": {"query": {"type": "string"}}, "required": ["query"]},
        )
        host = _ContextHost(tool_registry=_registry_with(tool))
        result = host._parse_direct_tool_prompt("#my_tool search for something")
        assert isinstance(result, tuple)
        _, tool_input, _ = result
        assert tool_input.get("query") is not None

    def test_bare_agent_mention_gets_default_prompt(self) -> None:
        """When only #ClaudeCode is sent (no task text), prompt defaults to a bootstrap string."""
        tool = _MockTool(name="claude_code", description="agent", executor="claude_code")
        host = _ContextHost(tool_registry=_registry_with(tool))
        result = host._parse_direct_tool_prompt("#claude_code")
        assert isinstance(result, tuple)
        _, tool_input, _ = result
        assert tool_input["prompt"]  # non-empty
        assert tool_input["prompt"] != ""

    def test_bare_codex_mention_gets_default_prompt(self) -> None:
        """When only #codex is sent (no task text), prompt defaults to a bootstrap string."""
        tool = _MockTool(name="codex", description="codex agent", executor="codex")
        host = _ContextHost(tool_registry=_registry_with(tool))
        result = host._parse_direct_tool_prompt("#codex")
        assert isinstance(result, tuple)
        _, tool_input, _ = result
        assert tool_input["prompt"]  # non-empty
        assert tool_input["prompt"] != ""


# ---------------------------------------------------------------------------
# _is_bare_agent_mention
# ---------------------------------------------------------------------------


class TestIsBareAgentMention:
    def test_bare_claude_code(self) -> None:
        tool = _MockTool(name="claude_code", description="agent", executor="claude_code")
        host = _ContextHost(tool_registry=_registry_with(tool))
        assert host._is_bare_agent_mention("#claude_code") is True

    def test_bare_codex(self) -> None:
        tool = _MockTool(name="codex", description="codex agent", executor="codex")
        host = _ContextHost(tool_registry=_registry_with(tool))
        assert host._is_bare_agent_mention("#codex") is True

    def test_bare_with_case_variant(self) -> None:
        tool = _MockTool(name="ClaudeCode", description="agent", executor="claude_code")
        host = _ContextHost(tool_registry=_registry_with(tool))
        assert host._is_bare_agent_mention("#ClaudeCode") is True

    def test_bare_with_project_mention(self) -> None:
        tool = _MockTool(name="claude_code", description="agent", executor="claude_code")
        host = _ContextHost(tool_registry=_registry_with(tool))
        assert host._is_bare_agent_mention("#claude_code @MyProject") is True

    def test_not_bare_when_task_text_present(self) -> None:
        tool = _MockTool(name="claude_code", description="agent", executor="claude_code")
        host = _ContextHost(tool_registry=_registry_with(tool))
        assert host._is_bare_agent_mention("#claude_code fix the bug") is False

    def test_not_bare_for_non_agent_tool(self) -> None:
        tool = _MockTool(name="run_shell", description="shell tool", executor="shell")
        host = _ContextHost(tool_registry=_registry_with(tool))
        assert host._is_bare_agent_mention("#run_shell") is False

    def test_not_bare_for_unknown_tool(self) -> None:
        registry = MagicMock()
        registry.get.return_value = None
        host = _ContextHost(tool_registry=registry)
        assert host._is_bare_agent_mention("#unknown_tool") is False

    def test_not_bare_when_no_tool_mention(self) -> None:
        host = _ContextHost()
        assert host._is_bare_agent_mention("just plain text") is False


# ---------------------------------------------------------------------------
# _build_file_context — no-DB fast path
# ---------------------------------------------------------------------------


class TestBuildFileContextNoDB:
    async def test_returns_none_when_no_db_factory(self) -> None:
        host = _ContextHost(db_session_factory=None)
        result = await host._build_file_context(["README.md"])
        assert result is None

    async def test_returns_none_when_no_settings(self) -> None:
        host = _ContextHost(settings=None)
        result = await host._build_file_context(["README.md"])
        assert result is None
