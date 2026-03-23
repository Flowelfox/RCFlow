"""Context building and direct tool mode methods for PromptRouter.

Extracted from prompt_router.py to reduce file size. These methods handle
extracting #tool and $file mentions from user text, building LLM context
blocks, resolving file references against the artifact database, and parsing
direct-mode ``#tool_name`` prompts.

Project context is now built from the session's ``main_project_path`` (set
explicitly via the ``project_name`` field in the WS prompt message) rather
than from ``@mention`` extraction.

Used as a mixin class — ``PromptRouter`` inherits from
``ContextMixin`` to gain these methods.
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from src.core.buffer import MessageType
from src.core.session import ActiveSession, ActivityState
from src.models.db import Artifact as ArtifactModel

if TYPE_CHECKING:
    from src.tools.loader import ToolDefinition

logger = logging.getLogger(__name__)

# Text file extensions that support content inclusion in $file references
_TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".md",
        ".txt",
        ".log",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".cfg",
        ".ini",
        ".csv",
        ".xml",
        ".html",
        ".css",
        ".js",
        ".ts",
        ".py",
        ".sh",
        ".bash",
        ".sql",
        ".rs",
        ".go",
        ".java",
        ".c",
        ".cpp",
        ".h",
        ".hpp",
        ".cs",
        ".rb",
        ".php",
        ".jsx",
        ".tsx",
        ".vue",
        ".dart",
        ".swift",
        ".kt",
        ".r",
        ".m",
        ".mm",
    }
)


def _format_file_size(size_bytes: int) -> str:
    """Format a byte count to a human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


class ContextMixin:
    """Mixin providing context building and direct tool mode methods for PromptRouter."""

    # ------------------------------------------------------------------
    # Mention extraction
    # ------------------------------------------------------------------

    # _MENTION_RE is kept for direct tool mode (@project parsing in #tool prompts).
    _MENTION_RE = re.compile(r"(?:^|(?<=\s))@(\S+)")
    _TOOL_MENTION_RE = re.compile(r"(?:^|(?<=\s))#(\S+)")
    _FILE_REF_RE = re.compile(r"(?:^|(?<=\s))\$(\S+)")

    def _extract_tool_mentions(self, text: str) -> list[str]:
        """Extract #ToolName mentions from user text."""
        return self._TOOL_MENTION_RE.findall(text)

    def _extract_file_references(self, text: str) -> list[str]:
        """Extract $filename references from user text."""
        return self._FILE_REF_RE.findall(text)

    # ------------------------------------------------------------------
    # Project context
    # ------------------------------------------------------------------

    def _build_project_context_from_path(self, abs_path: str) -> str:
        """Build the LLM project context string from an already-resolved absolute path.

        Called every turn when ``session.main_project_path`` is set, so the LLM
        always has the project directory in context regardless of which turn
        the project was first selected.
        """
        name = Path(abs_path).name
        return (
            f'[Context: This session is working on project "{name}" '
            f"located at {abs_path}. "
            f"Prefer reading and writing files under this directory.]"
        )

    # ------------------------------------------------------------------
    # Tool context
    # ------------------------------------------------------------------

    def _build_tool_context(self, mentions: list[str]) -> str | None:
        """Resolve #tool mentions against the tool registry and build a context string.

        Returns None if no mentions resolve to valid tools.

        Special case — worktree + agent: when the user mentions both a worktree
        tool and an agent tool (claude_code / codex), a two-step orchestration
        directive is generated instead of the normal split weak/MUST blocks.
        The LLM is told to call the worktree tool first (action="new"), extract
        the resulting ``path`` from the JSON response, and then invoke the agent
        tool with that path as ``working_directory``.  The agentic loop already
        handles this correctly: the worktree tool does not set ``agent_started``,
        so the loop continues and picks up the agent call on the next turn.
        """
        resolved: list[tuple[str, str, str]] = []  # (name, description, executor)
        seen: set[str] = set()
        for name in mentions:
            tool = self._tool_registry.get(name)
            if tool is not None and tool.name not in seen:
                seen.add(tool.name)
                resolved.append((tool.name, tool.description, tool.executor))

        if not resolved:
            return None

        agent_tools = [(n, d) for n, d, e in resolved if e in ("claude_code", "codex")]
        worktree_tools = [(n, d) for n, d, e in resolved if e == "worktree"]
        other_tools = [(n, d) for n, d, e in resolved if e not in ("claude_code", "codex", "worktree")]

        parts: list[str] = []

        # Worktree + agent: emit a single orchestration directive so the LLM
        # calls worktree first, then hands its path to the agent.
        if worktree_tools and agent_tools:
            worktree_name = worktree_tools[0][0]
            agent_lines = "\n".join(f'- "{n}": {d}' for n, d in agent_tools)
            parts.append(
                f"[IMPORTANT — Worktree + Agent orchestration: The user has explicitly "
                f"requested to run an agent inside a new git worktree. "
                f"You MUST follow this exact two-step sequence:\n\n"
                f'Step 1 — Call the "{worktree_name}" tool EXACTLY ONCE (never in parallel '
                f"with another tool call, never retried with a different branch name):\n"
                f'  - action: "new"\n'
                f"  - repo_path: the absolute path of the referenced project or repository\n"
                f"  - branch: a short kebab-case branch name derived from the user's task\n\n"
                f"Step 2 — From the worktree result JSON, extract the \"path\" field. "
                f"Then call the agent tool with that path as working_directory:\n"
                f"{agent_lines}\n\n"
                f"Do NOT call the agent tool before the worktree is created. "
                f"Do NOT skip or reorder these steps. "
                f"If the worktree call fails, report the error to the user and stop — "
                f"do NOT retry with a different branch name.]"
            )
        else:
            # Agent tools (claude_code, codex) need a hard directive — the user is
            # choosing which agent binary to launch, so the LLM must not substitute
            # a different agent tool.
            if agent_tools:
                agent_lines = "\n".join(f'- "{name}": {desc}' for name, desc in agent_tools)
                parts.append(
                    f"[IMPORTANT — Mandatory tool selection: The user has explicitly "
                    f"requested the following agent tool(s):\n{agent_lines}\n"
                    f"You MUST call exactly this tool. Do NOT substitute a different "
                    f"agent tool even if it has a similar description.]"
                )
            # Non-agent preference tools: include worktree_tools here when there is
            # no agent to pair with (solo worktree mention → preference block).
            preference_tools = other_tools + (worktree_tools if not agent_tools else [])
            if preference_tools:
                tool_lines = "\n".join(f'- "{name}": {desc}' for name, desc in preference_tools)
                parts.append(
                    f"[Tool preference: The user has explicitly requested that you use "
                    f"the following tool(s) to accomplish this task:\n{tool_lines}\n"
                    f"Prioritize using these tools. If the task can be accomplished with "
                    f"the mentioned tools, use them rather than alternatives.]"
                )

        # Any non-worktree, non-agent tools mentioned alongside a worktree+agent
        # combo still get a preference block.
        if worktree_tools and agent_tools and other_tools:
            tool_lines = "\n".join(f'- "{name}": {desc}' for name, desc in other_tools)
            parts.append(
                f"[Tool preference: The user has also requested:\n{tool_lines}\n"
                f"Use these tools where appropriate.]"
            )

        return "\n\n".join(parts)

    def _resolve_project_path(self, name: str) -> Path | None:
        """Resolve a project folder name to an absolute directory path, or None.

        Searches each configured projects_dir for a subdirectory matching ``name``.
        Returns None when no match is found.
        """
        if not self._settings:
            return None
        for projects_dir in self._settings.projects_dirs:
            project_path = projects_dir / name
            if project_path.is_dir():
                return project_path
        return None

    # ------------------------------------------------------------------
    # File context
    # ------------------------------------------------------------------

    _MAX_FILE_CONTEXT_SIZE = 100_000  # ~100KB max file content to include in context

    async def _build_file_context(self, references: list[str]) -> str | None:
        """Resolve $filename references against the artifact database and build file context.

        For text files: includes the file content in a fenced code block.
        For non-text files: includes file metadata.

        Returns None if no references resolve to valid artifacts.
        """
        if not self._db_session_factory or not self._settings:
            return None

        context_parts: list[str] = []
        seen: set[str] = set()

        async with self._db_session_factory() as db:
            for ref_name in references:
                lower_ref = ref_name.lower()
                if lower_ref in seen:
                    continue

                # Look up artifact by file_name (case-insensitive)
                stmt = (
                    select(ArtifactModel)
                    .where(ArtifactModel.backend_id == self._settings.RCFLOW_BACKEND_ID)
                    .where(func.lower(ArtifactModel.file_name) == lower_ref)
                    .order_by(ArtifactModel.modified_at.desc())
                    .limit(1)
                )
                result = await db.execute(stmt)
                artifact = result.scalar_one_or_none()

                if artifact is None:
                    continue

                seen.add(lower_ref)
                file_path = Path(artifact.file_path)

                if not file_path.exists():
                    context_parts.append(
                        f"[File: {artifact.file_name} -- File not found on disk at {artifact.file_path}]"
                    )
                    continue

                if artifact.file_extension.lower() in _TEXT_EXTENSIONS:
                    # Text file: include content
                    try:
                        content = file_path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        try:
                            content = file_path.read_text(encoding="latin-1")
                        except Exception as e:
                            context_parts.append(f"[File: {artifact.file_name} -- Error reading: {e}]")
                            continue
                    except OSError as e:
                        context_parts.append(f"[File: {artifact.file_name} -- Error reading: {e}]")
                        continue

                    lang_hint = artifact.file_extension.lstrip(".")
                    if len(content) > self._MAX_FILE_CONTEXT_SIZE:
                        content = content[: self._MAX_FILE_CONTEXT_SIZE]
                        context_parts.append(
                            f"[File: {artifact.file_name} ({artifact.file_path}) -- "
                            f"truncated to {self._MAX_FILE_CONTEXT_SIZE // 1024}KB]\n"
                            f"```{lang_hint}\n{content}\n```"
                        )
                    else:
                        context_parts.append(
                            f"[File: {artifact.file_name} ({artifact.file_path})]\n```{lang_hint}\n{content}\n```"
                        )
                else:
                    # Non-text file: include metadata only
                    size_str = _format_file_size(artifact.file_size)
                    modified = artifact.modified_at.isoformat() if artifact.modified_at else "unknown"
                    context_parts.append(
                        f"[File: {artifact.file_name} ({artifact.file_path})\n"
                        f"  Type: {artifact.mime_type or 'unknown'}\n"
                        f"  Extension: {artifact.file_extension}\n"
                        f"  Size: {size_str}\n"
                        f"  Modified: {modified}\n"
                        f"  Note: Binary/non-text file -- content not included]"
                    )

        if not context_parts:
            return None

        return "\n\n".join(context_parts)

    # ------------------------------------------------------------------
    # Active worktree context
    # ------------------------------------------------------------------

    def _build_active_worktree_context(self, session: ActiveSession) -> str | None:
        """Build an LLM context block describing the actively selected worktree.

        Returns a directive string when ``selected_worktree_path`` is set in
        session metadata, or ``None`` when no worktree is explicitly selected.
        The directive instructs the LLM to pass the worktree path as
        ``working_directory`` when it calls any agent tool (claude_code / codex).
        """
        selected_wt: str | None = session.metadata.get("selected_worktree_path")
        if not selected_wt:
            return None

        wt_info: dict[str, Any] = session.metadata.get("worktree") or {}
        branch: str = wt_info.get("branch", "")
        repo_path: str = wt_info.get("repo_path", "")

        parts: list[str] = [
            f"[Active worktree: The user has selected a git worktree at '{selected_wt}'."
        ]
        if branch:
            parts.append(f" Branch: '{branch}'.")
        if repo_path:
            parts.append(f" Repository: '{repo_path}'.")
        parts.append(
            f" When invoking any agent tool (claude_code, codex), you MUST pass"
            f" working_directory='{selected_wt}' unless the user explicitly requests"
            f" a different directory.]"
        )
        return "".join(parts)

    # ------------------------------------------------------------------
    # Bare agent mention detection
    # ------------------------------------------------------------------

    def _is_bare_agent_mention(self, text: str) -> bool:
        """Return True when *text* is only ``#AgentTool`` (plus optional ``@Project``).

        Used in normal (LLM) mode to short-circuit directly into agent
        subprocess startup when the user types just ``#ClaudeCode`` or
        ``#Codex`` without any task description.
        """
        tool_mentions = self._TOOL_MENTION_RE.findall(text)
        if not tool_mentions:
            return False

        # Resolve the first valid tool mention
        tool_def: ToolDefinition | None = None
        for mention in tool_mentions:
            candidate = self._tool_registry.get(mention)
            if candidate is not None:
                tool_def = candidate
                break

        if tool_def is None or tool_def.executor not in ("claude_code", "codex"):
            return False

        # Strip all #mentions and @mentions — if nothing meaningful remains,
        # it is a bare agent mention.
        clean = self._TOOL_MENTION_RE.sub("", text)
        clean = self._MENTION_RE.sub("", clean).strip()
        return clean == ""

    # ------------------------------------------------------------------
    # Direct tool mode
    # ------------------------------------------------------------------

    def _parse_direct_tool_prompt(self, text: str) -> tuple[ToolDefinition, dict[str, Any], str] | str:
        """Parse a direct-mode prompt into (tool_def, tool_input, display_text) or an error string.

        The ``#tool_name`` and ``@ProjectName`` mentions can appear anywhere in the
        text and in any order.  Everything else becomes the prompt/command.

        Examples that all produce the same result::

            #claude_code @RCFlow fix the bug
            @RCFlow #ClaudeCode fix the bug
            fix the bug @RCFlow #claude_code
        """
        # Find #tool mention anywhere in text
        tool_mentions = self._TOOL_MENTION_RE.findall(text)
        if not tool_mentions:
            available = [t.name for t in self._tool_registry.list_tools()]
            return f"Direct tool mode requires #tool_name syntax. Available tools: {', '.join(available)}"

        # Resolve the first valid tool mention
        tool_def: ToolDefinition | None = None
        tool_mention_used: str = ""
        for mention in tool_mentions:
            candidate = self._tool_registry.get(mention)
            if candidate is not None:
                tool_def = candidate
                tool_mention_used = mention
                break

        if tool_def is None:
            available = [t.name for t in self._tool_registry.list_tools()]
            return f"Unknown tool: #{tool_mentions[0]}. Available tools: {', '.join(available)}"

        # Strip the matched #tool from text
        clean = re.sub(rf"(?:^|\s)#{re.escape(tool_mention_used)}(?:\s|$)", " ", text, count=1).strip()

        # Extract @ProjectName mentions for working directory
        working_dir: str | None = None
        project_mentions = self._MENTION_RE.findall(clean)
        for mention in project_mentions:
            resolved_path = self._resolve_project_path(mention)
            if resolved_path is not None:
                working_dir = str(resolved_path)
                clean = re.sub(rf"(?:^|\s)@{re.escape(mention)}(?:\s|$)", " ", clean, count=1).strip()
                break

        display_text = clean

        # Build tool_input based on executor type
        tool_input: dict[str, Any] = {}
        if tool_def.executor in ("claude_code", "codex"):
            tool_input["prompt"] = display_text or "Ready for instructions."
            if working_dir:
                tool_input["working_directory"] = working_dir
            elif self._settings and self._settings.projects_dirs:
                tool_input["working_directory"] = str(self._settings.projects_dirs[0])
        elif tool_def.executor == "shell":
            tool_input["command"] = display_text
        else:
            params_schema = tool_def.parameters
            properties = params_schema.get("properties", {})
            required_params = params_schema.get("required", [])
            if len(required_params) == 1:
                tool_input[required_params[0]] = display_text
            elif len(properties) == 1:
                tool_input[next(iter(properties))] = display_text
            else:
                return (
                    f"Tool #{tool_mention_used} has multiple required parameters and cannot "
                    f"be used in direct mode. Parameters: {', '.join(properties.keys())}"
                )

        return (tool_def, tool_input, text.strip())

    async def _handle_direct_prompt(self, session: ActiveSession, text: str) -> None:
        """Handle a prompt in direct tool mode (no LLM)."""
        parsed = self._parse_direct_tool_prompt(text)
        if isinstance(parsed, str):
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": parsed,
                    "code": "DIRECT_TOOL_ERROR",
                },
            )
            session.set_activity(ActivityState.IDLE)
            return

        tool_def, tool_input, display_text = parsed

        from src.core.llm import ToolCallRequest

        tool_call = ToolCallRequest(
            tool_use_id=str(uuid.uuid4()),
            tool_name=tool_def.name,
            tool_input=tool_input,
        )

        try:
            await self._execute_tool(session, tool_call)
        except Exception as e:
            logger.exception("Error executing direct tool in session %s", session.id)
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": str(e),
                    "code": "DIRECT_TOOL_ERROR",
                },
            )
            session.set_activity(ActivityState.IDLE)
            return

        # Set title from truncated prompt text
        if session.title is None:
            title = display_text[:50]
            if len(display_text) > 50:
                space_idx = title.rfind(" ")
                if space_idx > 20:
                    title = title[:space_idx]
                title += "..."
            session.title = title

        # If non-agent tool completed, set IDLE
        if session.claude_code_executor is None and session.codex_executor is None:
            session.set_activity(ActivityState.IDLE)
