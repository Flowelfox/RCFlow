"""Routes user prompts through the LLM pipeline with tool execution.

The ``PromptRouter`` class is composed from five mixin modules:

- :class:`~src.core.session_lifecycle.SessionLifecycleMixin` — session
  create/cancel/end/pause/resume/restore, permissions, inactivity reaper
- :class:`~src.core.context.ContextMixin` — #tool, $file mention
  extraction, project context building, direct tool mode
- :class:`~src.core.agent_claude_code.ClaudeCodeAgentMixin` — Claude Code
  subprocess lifecycle
- :class:`~src.core.agent_codex.CodexAgentMixin` — Codex CLI subprocess
  lifecycle
- :class:`~src.core.agent_opencode.OpenCodeAgentMixin` — OpenCode CLI subprocess
  lifecycle
- :class:`~src.core.background_tasks.BackgroundTasksMixin` — fire-and-forget
  background tasks (logging, archiving, summaries, titles, tasks, artifacts)
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Settings
from src.core.agent_claude_code import ClaudeCodeAgentMixin
from src.core.agent_codex import CodexAgentMixin
from src.core.agent_opencode import OpenCodeAgentMixin
from src.core.agent_prompt import format_agent_prompt
from src.core.attachment_store import ResolvedAttachment
from src.core.background_tasks import BackgroundTasksMixin
from src.core.buffer import MessageType
from src.core.context import ContextMixin
from src.core.llm import LLMClient, StreamDone, TextChunk, ToolCallRequest
from src.core.pending_store import SessionPendingMessageStore
from src.core.session import ActiveSession, ActivityState, SessionManager, SessionStatus, SessionType
from src.core.session_lifecycle import SessionLifecycleMixin
from src.database.models import Session as SessionModel
from src.database.models import Task as TaskModel
from src.database.models import TaskSession as TaskSessionModel
from src.executors.base import BaseExecutor, ExecutionChunk
from src.executors.claude_code import ClaudeCodeExecutor
from src.executors.codex import CodexExecutor
from src.executors.http import HttpExecutor
from src.executors.opencode import OpenCodeExecutor
from src.executors.shell import ShellExecutor
from src.executors.worktree import WorktreeExecutor
from src.services.artifact_scanner import ArtifactScanner
from src.services.telemetry_service import InFlightTurn, TelemetryService
from src.services.tool_manager import ToolManager
from src.services.tool_settings import ToolSettingsManager
from src.tools.loader import ToolDefinition
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_MAX_TOOL_OUTPUT_CHARS = 100_000


def _truncate_tool_output(content: str) -> str:
    """Truncate tool output that exceeds the size limit for client delivery."""
    if len(content) > _MAX_TOOL_OUTPUT_CHARS:
        return content[:_MAX_TOOL_OUTPUT_CHARS] + f"\n\n... (truncated, {len(content):,} total chars)"
    return content


def _build_planning_prompt(title: str, description: str, plan_path: Path) -> str:
    """Build the initial user message for a planning session."""
    lines = [
        "You are a software planning assistant. Your job is to produce a detailed",
        "implementation plan — not to implement anything.",
        "",
        "## Task",
        f"**Title:** {title}",
    ]
    if description:
        lines += ["", "**Description:**", description]
    lines += [
        "",
        "## Instructions",
        "1. Explore the codebase thoroughly using Read, Grep, and Glob tools.",
        "2. Identify all files, components, APIs, and data models that need to change.",
        "3. Write a detailed step-by-step implementation plan in Markdown.",
        "4. Include: files to change, data model implications, API changes, UI changes,",
        "   edge cases, testing strategy, and rollout notes.",
        "5. Do NOT implement anything. Do NOT run shell commands or modify any files",
        f"   except to write your final plan to exactly: `{plan_path}`",
        "6. Create the plan directory if it does not exist, then write the plan file.",
    ]
    return "\n".join(lines)


class PromptRouter(
    SessionLifecycleMixin,
    ContextMixin,
    ClaudeCodeAgentMixin,
    CodexAgentMixin,
    OpenCodeAgentMixin,
    BackgroundTasksMixin,
):
    """Routes user prompts through the LLM pipeline with tool execution."""

    def __init__(
        self,
        llm_client: LLMClient | None,
        session_manager: SessionManager,
        tool_registry: ToolRegistry,
        db_session_factory: async_sessionmaker[AsyncSession] | None = None,
        settings: Settings | None = None,
        tool_settings: ToolSettingsManager | None = None,
        tool_manager: ToolManager | None = None,
        artifact_scanner: ArtifactScanner | None = None,
        telemetry_service: TelemetryService | None = None,
        pending_store: SessionPendingMessageStore | None = None,
    ) -> None:
        self._llm = llm_client
        self._session_manager = session_manager
        self._tool_registry = tool_registry
        self._executors: dict[str, BaseExecutor] = {}
        self._db_session_factory = db_session_factory
        self._settings = settings
        self._tool_settings = tool_settings
        self._tool_manager = tool_manager
        self._artifact_scanner = artifact_scanner
        self._telemetry = telemetry_service
        self._pending_store = pending_store
        self._drain_tasks: set[asyncio.Task[None]] = set()
        # Holds strong references to in-flight ``handle_prompt`` tasks created
        # by the WebSocket input handler. Without this, the only reference to
        # the task is a local set in the handler — if the client disconnects
        # (e.g. the e2e helper closes the input channel right after the ack),
        # that set goes out of scope and Python can GC the still-running task
        # mid-execution, leaving subscribers blocked on messages that never
        # arrive. Discarded via ``task.add_done_callback`` so the set only
        # grows with truly active work.
        self._pending_prompt_tasks: set[asyncio.Task[str]] = set()
        self._pending_log_tasks: set[asyncio.Task[None]] = set()
        self._pending_title_tasks: set[asyncio.Task[None]] = set()
        self._pending_archive_tasks: set[asyncio.Task[None]] = set()
        self._pending_summary_tasks: set[asyncio.Task[None]] = set()
        self._pending_task_creation_tasks: set[asyncio.Task[None]] = set()
        self._pending_task_update_tasks: set[asyncio.Task[None]] = set()
        self._pending_plan_finalization_tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------------
    # Executor / config helpers
    # ------------------------------------------------------------------

    def _get_managed_config_overrides(self, tool_name: str) -> dict[str, Any]:
        """Read tool settings and return overrides for managed tools only."""
        if not self._tool_settings or not self._tool_manager:
            return {}
        tool = self._tool_manager._tools.get(tool_name)
        if not tool or not tool.managed:
            return {}
        settings = self._tool_settings.get_settings(tool_name)
        if not settings:
            return {}
        # Extract keys relevant to executor config
        overrides: dict[str, Any] = {}
        for key in ("model", "default_permission_mode", "max_turns", "timeout", "approval_mode"):
            val = settings.get(key)
            if val not in (None, "", []):
                overrides[key] = val

        # Don't pass model override when using Anthropic Login —
        # let the CLI choose the model based on the user's subscription.
        if tool_name == "claude_code" and settings.get("provider") == "anthropic_login":
            overrides.pop("model", None)

        return overrides

    def _get_executor(self, executor_type: str, tool_def: ToolDefinition | None = None) -> BaseExecutor:
        # Claude Code executors are always created fresh (one per session)
        if executor_type == "claude_code":
            binary_path = "claude"
            if tool_def is not None:
                config = tool_def.get_claude_code_config()
                binary_path = config.binary_path
            if self._tool_manager:
                resolved = self._tool_manager.get_binary_path("claude_code")
                if resolved:
                    binary_path = resolved
            return ClaudeCodeExecutor(
                binary_path=binary_path,
                extra_env=self._build_claude_code_extra_env(),
                config_overrides=self._get_managed_config_overrides("claude_code"),
            )

        # Codex executors are always created fresh (one per session)
        if executor_type == "codex":
            binary_path = "codex"
            if tool_def is not None:
                config = tool_def.get_codex_config()
                binary_path = config.binary_path
            if self._tool_manager:
                resolved = self._tool_manager.get_binary_path("codex")
                if resolved:
                    binary_path = resolved
            return CodexExecutor(
                binary_path=binary_path,
                extra_env=self._build_codex_extra_env(),
                config_overrides=self._get_managed_config_overrides("codex"),
            )

        # OpenCode executors are always created fresh (one per session)
        if executor_type == "opencode":
            binary_path = "opencode"
            if tool_def is not None:
                config = tool_def.get_opencode_config()
                binary_path = config.binary_path
            if self._tool_manager:
                resolved = self._tool_manager.get_binary_path("opencode")
                if resolved:
                    binary_path = resolved
            return OpenCodeExecutor(
                binary_path=binary_path,
                extra_env=self._build_opencode_extra_env(),
                config_overrides=self._get_managed_config_overrides("opencode"),
            )

        if executor_type not in self._executors:
            match executor_type:
                case "shell":
                    self._executors[executor_type] = ShellExecutor()
                case "http":
                    self._executors[executor_type] = HttpExecutor()
                case "worktree":
                    self._executors[executor_type] = WorktreeExecutor()
                case _:
                    raise ValueError(f"Unknown executor type: {executor_type}")
        return self._executors[executor_type]

    def _resolve_working_directory(self, working_dir: str) -> Path:
        """Resolve a working directory path, using configured PROJECTS_DIR for ~ expansion.

        The system prompt tells the LLM to use the absolute PROJECTS_DIR path, so
        most calls will already be absolute. This also handles the legacy ~/Projects
        prefix in case the LLM still uses it.
        """
        if self._settings is not None:
            for projects_dir in self._settings.projects_dirs:
                projects_dir_str = str(projects_dir)
                # Handle absolute path that already matches a configured projects dir
                if working_dir.startswith(projects_dir_str):
                    return Path(working_dir)
            # Handle ~/Projects prefix (legacy or fallback) — resolve against first dir
            if working_dir.startswith("~/Projects") and self._settings.projects_dirs:
                first_dir = self._settings.projects_dirs[0]
                suffix = working_dir[len("~/Projects") :]
                return first_dir / suffix.lstrip("/")
        return Path(working_dir).expanduser()

    # ------------------------------------------------------------------
    # Attachment helpers
    # ------------------------------------------------------------------

    # MIME types whose raw bytes can be decoded as UTF-8 text
    _TEXT_MIME_PREFIXES = ("text/",)
    _TEXT_MIME_TYPES = frozenset(
        {
            "application/json",
            "application/xml",
            "application/javascript",
            "application/typescript",
            "application/toml",
            "application/x-yaml",
            "application/yaml",
        }
    )
    _TEXT_EXTENSIONS = frozenset(
        {
            ".txt",
            ".md",
            ".rst",
            ".log",
            ".csv",
            ".py",
            ".js",
            ".ts",
            ".jsx",
            ".tsx",
            ".dart",
            ".java",
            ".kt",
            ".swift",
            ".go",
            ".rs",
            ".rb",
            ".c",
            ".cpp",
            ".h",
            ".hpp",
            ".cs",
            ".php",
            ".html",
            ".css",
            ".scss",
            ".less",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".xml",
            ".sh",
            ".bash",
            ".zsh",
            ".fish",
            ".ps1",
            ".sql",
            ".graphql",
            ".proto",
            ".gitignore",
            ".env",
            "Dockerfile",
        }
    )
    _IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})

    def _is_text_attachment(self, att: ResolvedAttachment) -> bool:
        """Return True if this attachment should be inlined as plain text."""
        if any(att.mime_type.startswith(p) for p in self._TEXT_MIME_PREFIXES):
            return True
        if att.mime_type in self._TEXT_MIME_TYPES:
            return True
        import os  # noqa: PLC0415

        _, ext = os.path.splitext(att.file_name.lower())
        return ext in self._TEXT_EXTENSIONS

    def _build_attachment_blocks(self, attachments: list[ResolvedAttachment]) -> list[dict[str, Any]]:
        """Convert resolved attachments into LLM content blocks.

        - Images → image content blocks (Anthropic base64 or OpenAI image_url),
          or a text placeholder when the model does not support vision.
        - Text files → inline text blocks with a filename header
        - Other binary → a brief placeholder text block
        """
        import base64  # noqa: PLC0415

        provider = self._llm.provider if self._llm else "anthropic"
        vision_ok = self._llm.supports_vision if self._llm else False
        blocks: list[dict[str, Any]] = []

        for att in attachments:
            if att.mime_type in self._IMAGE_MIME_TYPES:
                if not vision_ok:
                    # Model does not support images — send a descriptive placeholder
                    blocks.append(
                        {
                            "type": "text",
                            "text": (
                                f"[Attached image: {att.file_name} "
                                f"({att.mime_type}, {len(att.data):,} bytes) — "
                                "image content not supported by current model]"
                            ),
                        }
                    )
                elif provider == "openai":
                    b64 = base64.standard_b64encode(att.data).decode()
                    blocks.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{att.mime_type};base64,{b64}"},
                        }
                    )
                else:
                    b64 = base64.standard_b64encode(att.data).decode()
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": att.mime_type,
                                "data": b64,
                            },
                        }
                    )
            elif self._is_text_attachment(att):
                try:
                    decoded = att.data.decode("utf-8", errors="replace")
                except Exception:
                    decoded = att.data.decode("latin-1", errors="replace")
                blocks.append(
                    {
                        "type": "text",
                        "text": f"[Attached file: {att.file_name}]\n{decoded}",
                    }
                )
            else:
                # Binary file — include a metadata placeholder
                blocks.append(
                    {
                        "type": "text",
                        "text": (
                            f"[Attached file: {att.file_name} "
                            f"({att.mime_type}, {len(att.data):,} bytes) — binary content not shown]"
                        ),
                    }
                )

        return blocks

    # ------------------------------------------------------------------
    # Worktree metadata helpers
    # ------------------------------------------------------------------

    def _update_session_worktree_meta(
        self, session: ActiveSession, tool_call: ToolCallRequest, result: str = ""
    ) -> None:
        """Store the most recent worktree context in session metadata and broadcast.

        For ``action=new``, also auto-selects the newly created worktree path from
        the tool result JSON so that a subsequent ``#ClaudeCode`` request immediately
        uses the new worktree as its working directory without requiring an explicit
        UI selection step.
        """
        tool_input = tool_call.tool_input
        repo_path = tool_input.get("repo_path", "")
        action = tool_input.get("action", "")

        match action:
            case "new":
                session.metadata["worktree"] = {
                    "repo_path": repo_path,
                    "last_action": "new",
                    "branch": tool_input.get("branch", ""),
                    "base": tool_input.get("base", "main"),
                }
                # Auto-select the created worktree path so subsequent agent tool
                # calls (e.g. #ClaudeCode) land in the new worktree immediately.
                try:
                    data = json.loads(result)
                    wt_path: str = data.get("created", {}).get("path", "")
                    if wt_path:
                        session.metadata["selected_worktree_path"] = wt_path
                except (json.JSONDecodeError, AttributeError, TypeError):
                    pass
            case "attach":
                session.metadata["worktree"] = {
                    "repo_path": repo_path,
                    "last_action": "attach",
                }
                # Auto-select the attached worktree path so subsequent agent tool
                # calls (e.g. #ClaudeCode) use it as the working directory.
                try:
                    data = json.loads(result)
                    wt_path = data.get("attached", {}).get("path", "")
                    if wt_path:
                        session.metadata["selected_worktree_path"] = wt_path
                except (json.JSONDecodeError, AttributeError, TypeError):
                    pass
            case "merge" | "rm":
                session.metadata["worktree"] = {
                    "repo_path": repo_path,
                    "last_action": action,
                }
                # Clear stale selected path when the worktree has been merged or removed.
                session.metadata.pop("selected_worktree_path", None)
            case "list":
                session.metadata["worktree"] = {
                    "repo_path": repo_path,
                    "last_action": "list",
                }

        # Broadcast so the client can update the worktree panel immediately
        if self._session_manager:
            self._session_manager.broadcast_session_update(session)

    # ------------------------------------------------------------------
    # Project name validation
    # ------------------------------------------------------------------

    def _apply_project_name(self, session: ActiveSession, project_name: str) -> None:
        """Resolve, validate, and apply a project_name received from the client picker.

        On success: sets ``session.main_project_path``, clears any previous error,
        and broadcasts a session_update so the client chip reflects the accepted path.

        On failure: pushes an error buffer entry and sets ``session.project_name_error``
        so the client chip shows a red error state via the next session_update.
        """
        resolved = self._resolve_project_path(project_name)
        if resolved is None:
            self._push_project_error(
                session,
                f'Project not found: "{project_name}". Check that it exists under a configured projects directory.',
            )
            return

        if not os.access(resolved, os.R_OK | os.X_OK):
            self._push_project_error(
                session,
                f'Permission denied accessing project "{project_name}" at {resolved}.',
            )
            return

        abs_path = str(resolved)
        if abs_path != session.main_project_path:
            session.main_project_path = abs_path
            session.project_name_error = None
            if self._session_manager:
                self._session_manager.broadcast_session_update(session)
        elif session.project_name_error is not None:
            # Path unchanged but error flag needs clearing
            session.project_name_error = None
            if self._session_manager:
                self._session_manager.broadcast_session_update(session)

    def _push_project_error(self, session: ActiveSession, message: str) -> None:
        """Push a project validation error to the session buffer and broadcast it."""
        session.project_name_error = message
        session.buffer.push_text(
            MessageType.ERROR,
            {
                "session_id": session.id,
                "content": message,
                "code": "PROJECT_ERROR",
            },
        )
        if self._session_manager:
            self._session_manager.broadcast_session_update(session)

    # ------------------------------------------------------------------
    # Plan session setup
    # ------------------------------------------------------------------

    async def prepare_plan_session(
        self,
        task_id: str,
        project_name: str | None = None,
        selected_worktree_path: str | None = None,
    ) -> tuple[str, str]:
        """Set up a read-only planning session for a task.

        Returns ``(session_id, planning_prompt)``. The caller is responsible for
        firing ``handle_prompt(planning_prompt, session_id, ...)`` as a background
        task. Does NOT start the agentic loop.

        Raises:
            ValueError: If the task does not exist.
            RuntimeError: If the database is not configured or no project is
                available to determine the plan output path.
        """
        if self._db_session_factory is None:
            raise RuntimeError("Database not configured")

        task_uuid = uuid.UUID(task_id)
        async with self._db_session_factory() as db:
            task = await db.get(TaskModel, task_uuid)
            if task is None:
                raise ValueError(f"Task not found: {task_id}")
            task_title = task.title
            task_description = task.description or ""

        # Create a ONE_SHOT session (one prompt → plan → auto-ends).
        session = self._session_manager.create_session(SessionType.ONE_SHOT)

        # Apply project context if provided.
        if project_name:
            self._apply_project_name(session, project_name)
        if selected_worktree_path:
            session.metadata["selected_worktree_path"] = selected_worktree_path

        # Determine the plan output path.
        project_root = session.main_project_path
        if not project_root and self._settings and self._settings.projects_dirs:
            project_root = str(self._settings.projects_dirs[0])
        if not project_root:
            raise RuntimeError(
                "No project configured — cannot determine plan output path. "
                "Select a project before starting a plan session."
            )

        plan_dir = Path(project_root) / ".rcflow" / "plans"
        plan_path = plan_dir / f"{task_id}.md"

        session.metadata["session_purpose"] = "plan"
        session.metadata["task_id"] = task_id
        session.metadata["plan_output_path"] = str(plan_path)

        # Pre-seed restrictive permission rules.
        # Rule ordering: PermissionManager.check_cached() iterates self._rules in
        # REVERSE (most-recently-added rules checked first). Deny-all rules are
        # added first (checked last as fallback); the specific Write-allow for the
        # plan directory is added last (checked first, overrides the Write deny).
        session.metadata["permission_rules"] = [
            # Per-tool denies (added first → checked last as fallback).
            # Must use "tool_session" scope (not "all_session") so that
            # check_cached() matches them in its TOOL_SESSION branch.
            # "all_session" is only matched when tool_name == "*".
            {"tool_name": "Bash", "decision": "deny", "scope": "tool_session", "path_prefix": None},
            {"tool_name": "Edit", "decision": "deny", "scope": "tool_session", "path_prefix": None},
            {"tool_name": "Agent", "decision": "deny", "scope": "tool_session", "path_prefix": None},
            {"tool_name": "Write", "decision": "deny", "scope": "tool_session", "path_prefix": None},
            # Specific allow (added last → checked first, overrides Write deny for plan dir)
            {"tool_name": "Write", "decision": "allow", "scope": "tool_path", "path_prefix": str(plan_dir)},
        ]

        # Enforce the permission rules on the live session immediately.
        # (restore_rules() is normally only called during restore_session();
        # for new plan sessions we must seed the PermissionManager directly.)
        from src.core.permissions import PermissionManager  # noqa: PLC0415

        pm = PermissionManager()
        pm.restore_rules(session.metadata["permission_rules"])
        session.permission_manager = pm

        # Attach session to task in DB so task.sessions list is populated.
        backend_id = self._settings.RCFLOW_BACKEND_ID if self._settings else ""
        async with self._db_session_factory() as db:
            session_uuid = uuid.UUID(session.id)
            existing = await db.get(SessionModel, session_uuid)
            if existing is None:
                db.add(
                    SessionModel(
                        id=session_uuid,
                        backend_id=backend_id,
                        created_at=session.created_at,
                        ended_at=session.ended_at,
                        session_type=session.session_type.value,
                        status=session.status.value,
                    )
                )
                await db.flush()
            link = TaskSessionModel(task_id=task_uuid, session_id=session_uuid)
            db.add(link)
            try:
                await db.commit()
            except Exception:
                await db.rollback()
                # Link may already exist — non-fatal

        planning_prompt = _build_planning_prompt(task_title, task_description, plan_path)
        return session.id, planning_prompt

    # ------------------------------------------------------------------
    # Main prompt handler
    # ------------------------------------------------------------------

    async def enqueue_user_prompt(
        self,
        session: ActiveSession,
        *,
        text: str,
        display_text: str | None,
        attachments: list[ResolvedAttachment] | None,
        project_name: str | None,
        selected_worktree_path: str | None,
        task_id: str | None,
    ) -> str | None:
        """Persist a user prompt in the queue when the session is busy.

        Returns the new ``queued_id`` on success, or ``None`` when the session
        is idle (caller should deliver the prompt via :meth:`handle_prompt`
        instead) or the pending store is unavailable.  See ``Queued User
        Messages`` in ``Design.md``.
        """
        if self._pending_store is None:
            return None
        if not session.is_busy_for_queue():
            return None
        display = display_text if display_text is not None else self._TOOL_MENTION_RE.sub("", text).strip()
        entry = await self._pending_store.enqueue(
            session,
            content=text,
            display_content=display,
            attachments=attachments,
            project_name=project_name,
            selected_worktree_path=selected_worktree_path,
            task_id=task_id,
        )
        return entry.queued_id

    def schedule_pending_drain(self, session: ActiveSession) -> None:
        """Schedule a pass over the queued messages for *session*.

        Called at the end of each turn (Claude Code / Codex / OpenCode result
        event, LLM ``_prompt_lock`` release).  Delivers the oldest pending
        message by invoking :meth:`handle_prompt`; any subsequent queued
        messages are picked up by the next turn-end hook.
        """
        if self._pending_store is None:
            return
        if not session.pending_user_messages:
            return
        if session.status in (
            SessionStatus.PAUSED,
            SessionStatus.COMPLETED,
            SessionStatus.FAILED,
            SessionStatus.CANCELLED,
        ):
            return
        task = asyncio.create_task(self._drain_one(session))
        self._drain_tasks.add(task)
        task.add_done_callback(self._drain_tasks.discard)

    async def _drain_one(self, session: ActiveSession) -> None:
        """Deliver a single queued message (internal — called by ``schedule_pending_drain``)."""
        if self._pending_store is None or not session.pending_user_messages:
            return
        head = session.pending_user_messages[0]
        try:
            attachments = await asyncio.to_thread(SessionPendingMessageStore.rehydrate_attachments, head)
        except OSError as e:
            logger.warning("Failed to rehydrate queued attachments for %s: %s", head.queued_id, e)
            attachments = []
        # Remove the row + disk bytes; emits ``message_dequeued``.
        await self._pending_store.pop_head(session)
        try:
            await self.handle_prompt(
                text=head.content,
                session_id=session.id,
                attachments=attachments or None,
                project_name=head.project_name,
                selected_worktree_path=head.selected_worktree_path,
                task_id=head.task_id,
                display_text=head.display_content,
                queued_id=head.queued_id,
            )
        except Exception:
            logger.exception(
                "Failed to deliver drained queued message %s for session %s",
                head.queued_id,
                session.id,
            )

    async def handle_prompt(
        self,
        text: str,
        session_id: str | None = None,
        attachments: list[ResolvedAttachment] | None = None,
        project_name: str | None = None,
        selected_worktree_path: str | None = None,
        task_id: str | None = None,
        display_text: str | None = None,
        queued_id: str | None = None,
    ) -> str:
        """Handle a user prompt. Creates a new session or resumes an existing one.

        Args:
            text: The routing prompt, may include prepended agent tags (e.g.
                ``"#claude_code Are feature ready?"``).  Used for routing and
                tool-mention extraction.  Never stored in the buffer directly.
            display_text: The clean user message without agent-tag prefixes.
                Stored in the session buffer and shown in chat history.  Falls
                back to ``text`` when not provided (e.g. internal callers).
            session_id: Existing session UUID, or None to create a new session.
            attachments: Optional list of resolved file attachments whose content
                will be included as multimodal content blocks sent to the LLM.
            project_name: Folder name of the project selected in the client picker
                (e.g. ``"RCFlow"``). Resolved to an absolute path via configured
                ``projects_dirs``. When provided, sets ``session.main_project_path``
                before the DB row is written so the initial INSERT already includes it.
            selected_worktree_path: Absolute path of a worktree pre-selected by the
                client before the first message was sent. Applied to the session if
                the session does not already have an explicit worktree selection.
                Has no effect when the session already has ``selected_worktree_path``
                set (e.g. via the PATCH endpoint or a previous call).

        Returns:
            The session ID.
        """
        resolved_id = self.ensure_session(session_id)
        session = self._session_manager.get_session(resolved_id)
        assert session is not None  # ensure_session guarantees this

        # Store task_id in session metadata so _build_plan_context can inject
        # the plan on the first LLM turn. Only set once to avoid overwriting if
        # this is a subsequent prompt in the same session.
        if task_id and "primary_task_id" not in session.metadata:
            session.metadata["primary_task_id"] = task_id

        # Resolve and validate the project_name from the client picker BEFORE the
        # DB row write, so the initial INSERT already contains main_project_path.
        if project_name:
            self._apply_project_name(session, project_name)

        # Apply the pre-selected worktree path (sent by the client before the first
        # message). Only set it when the session doesn't already have one — this
        # preserves any subsequent selection made via the PATCH endpoint or from a
        # worktree tool call (action=new/attach) that auto-selects the path.
        if selected_worktree_path and not session.metadata.get("selected_worktree_path"):
            session.metadata["selected_worktree_path"] = selected_worktree_path
            if self._session_manager:
                self._session_manager.broadcast_session_update(session)

        # Ensure the sessions row exists in the DB before any telemetry inserts
        # (session_turns and tool_calls FK-reference sessions.id, but sessions are
        # normally only archived to the DB after completion).
        await self._ensure_session_row_in_db(session)

        session.touch()

        # Auto-resume paused sessions when a new prompt arrives
        if session.status == SessionStatus.PAUSED:
            await self.resume_session(resolved_id)

        # Check session token limits before processing
        if self._check_token_limit_exceeded(session):
            return session.id

        # Clean text for buffer storage and agent forwarding.  The routing
        # text may carry a prepended "#agent_name" tag inserted by the client
        # for backend routing; display_text strips that prefix so chat history
        # never shows the tag.
        #
        # When display_text is not provided (e.g. user typed the #mention
        # directly instead of selecting it via the chip), derive display text
        # by stripping all #tool_mention markers so they never appear in chat.
        # The empty-string case (chip + empty input) must be preserved as-is.
        _display = display_text if display_text is not None else self._TOOL_MENTION_RE.sub("", text).strip()

        def _make_user_buffer_data() -> dict[str, Any]:
            """Build the TEXT_CHUNK(role=user) payload, optionally tagged with queued_id."""
            data: dict[str, Any] = {"content": _display, "role": "user"}
            if attachments:
                data["attachments"] = [
                    {"name": a.file_name, "mime_type": a.mime_type, "size": len(a.data)} for a in attachments
                ]
            if queued_id is not None:
                data["queued_id"] = queued_id
            return data

        # If session has an active Claude Code executor, forward message directly
        if session.claude_code_executor is not None:
            session.buffer.push_text(MessageType.TEXT_CHUNK, _make_user_buffer_data())
            await self._forward_to_claude_code(session, _display)
            return session.id

        # If session has an active Codex executor, forward message directly
        if session.codex_executor is not None:
            session.buffer.push_text(MessageType.TEXT_CHUNK, _make_user_buffer_data())
            await self._forward_to_codex(session, _display)
            return session.id

        # If session has an active OpenCode executor, forward message directly
        if session.opencode_executor is not None:
            session.buffer.push_text(MessageType.TEXT_CHUNK, _make_user_buffer_data())
            await self._forward_to_opencode(session, _display)
            return session.id

        # Direct tool mode: bypass LLM entirely, parse #tool_name syntax
        if self.is_direct_tool_mode:
            session.set_active()
            session.buffer.push_text(MessageType.TEXT_CHUNK, _make_user_buffer_data())
            await self._handle_direct_prompt(session, text)
            return session.id

        # Bare agent mention: when the user sends only "#ClaudeCode" or "#Codex"
        # (with no task description), bypass the LLM and start the agent subprocess
        # directly so it is ready for follow-up instructions.
        if self._is_bare_agent_mention(text):
            session.set_active()
            session.buffer.push_text(MessageType.TEXT_CHUNK, _make_user_buffer_data())
            await self._handle_direct_prompt(session, text)
            return session.id

        # Serialize prompt processing per session to prevent concurrent writes
        # to conversation_history, which would break tool_use/tool_result pairing.
        async with session._prompt_lock:
            session.set_active()
            session.set_activity(ActivityState.PROCESSING_LLM)

            # Build project context from the session's confirmed project path.
            # Injected every turn so the LLM always has the project in context,
            # not only on the first turn where the picker selection was made.
            project_context = (
                self._build_project_context_from_path(session.main_project_path) if session.main_project_path else None
            )

            tool_mentions = self._extract_tool_mentions(text)
            tool_context = self._build_tool_context(tool_mentions) if tool_mentions else None

            file_refs = self._extract_file_references(text)
            file_context = await self._build_file_context(file_refs) if file_refs else None

            # Active worktree context: injected when a worktree is explicitly
            # selected for this session so the LLM knows which working directory
            # to pass when it calls an agent tool (claude_code / codex).
            worktree_context = self._build_active_worktree_context(session)

            # Plan context: injected on the first turn of implementation sessions
            # that have a task with a completed plan artifact. Skipped for planning
            # sessions themselves.
            plan_context = await self._build_plan_context(session)

            context_blocks: list[dict[str, Any]] = []
            if project_context:
                context_blocks.append(
                    {"type": "text", "text": project_context, "cache_control": {"type": "ephemeral"}},
                )
            if tool_context:
                context_blocks.append(
                    {"type": "text", "text": tool_context, "cache_control": {"type": "ephemeral"}},
                )
            if file_context:
                context_blocks.append(
                    {"type": "text", "text": file_context, "cache_control": {"type": "ephemeral"}},
                )
            if worktree_context:
                context_blocks.append(
                    {"type": "text", "text": worktree_context, "cache_control": {"type": "ephemeral"}},
                )
            if plan_context:
                context_blocks.append(
                    {"type": "text", "text": plan_context, "cache_control": {"type": "ephemeral"}},
                )

            # Build attachment content blocks (images, text files, etc.)
            attachment_blocks: list[dict[str, Any]] = self._build_attachment_blocks(attachments) if attachments else []

            if context_blocks or attachment_blocks:
                content: str | list[dict[str, Any]] = [
                    *context_blocks,
                    *attachment_blocks,
                    {"type": "text", "text": text},
                ]
            else:
                content = text

            # Add user message to conversation history
            session.conversation_history.append(
                {
                    "role": "user",
                    "content": content,
                }
            )

            # Push the original user prompt to the buffer (no injected context).
            # Attachment metadata is included so clients can display file names.
            session.buffer.push_text(MessageType.TEXT_CHUNK, _make_user_buffer_data())

            # Define tool execution callback
            agent_started = False

            async def execute_tool(tool_call: ToolCallRequest) -> str:
                nonlocal agent_started
                result = await self._execute_tool(session, tool_call)
                if (
                    session.claude_code_executor is not None
                    or session.codex_executor is not None
                    or session.opencode_executor is not None
                ):
                    agent_started = True
                return result

            # Per-turn accumulators for logging
            turn_text = ""
            turn_has_tool_calls = False
            # Snapshot messages before this turn (for request_messages logging)
            turn_messages_snapshot: list[dict[str, Any]] = list(session.conversation_history)

            # Telemetry: track per-LLM-turn timing and token usage.
            # _tel_turn holds the current in-flight SessionTurn row; reset after each StreamDone.
            _tel_turn: InFlightTurn | None = None
            _tel_turn_idx: int = 0
            _tel_tool_idx: int = 0  # tool call index within the current LLM turn

            if self._telemetry is not None:
                _tel_turn = await self._telemetry.record_turn_start(
                    session_id=session.id,
                    turn_index=_tel_turn_idx,
                )

            # Run the agentic loop
            try:
                assert self._llm is not None, "LLM client not initialized"
                async for event in self._llm.run_agentic_loop(
                    messages=session.conversation_history,
                    execute_tool_fn=execute_tool,
                    should_stop_after_tools=lambda: agent_started,
                ):
                    match event:
                        case TextChunk(content=text_content):
                            turn_text += text_content
                            session.buffer.push_text(
                                MessageType.TEXT_CHUNK,
                                {
                                    "content": text_content,
                                    "session_id": session.id,
                                    "finished": False,
                                },
                            )
                            # Telemetry: first text chunk = first token
                            if self._telemetry is not None and _tel_turn is not None:
                                await self._telemetry.record_first_token(_tel_turn)

                        case ToolCallRequest():
                            turn_has_tool_calls = True
                            # Telemetry: first tool_start also counts as first token
                            if self._telemetry is not None and _tel_turn is not None:
                                await self._telemetry.record_first_token(_tel_turn)

                        case StreamDone(usage=usage) if usage is not None:
                            self._fire_log_task(
                                session_id=session.id,
                                usage=usage,
                                has_tool_calls=turn_has_tool_calls,
                                request_messages=turn_messages_snapshot,
                                response_text=turn_text or None,
                            )
                            # Accumulate token usage on session
                            session.input_tokens += usage.input_tokens
                            session.output_tokens += usage.output_tokens
                            session.cache_creation_input_tokens += usage.cache_creation_input_tokens
                            session.cache_read_input_tokens += usage.cache_read_input_tokens
                            if session._on_update:
                                session._on_update()
                            # Telemetry: close turn, open next if loop continues
                            if self._telemetry is not None and _tel_turn is not None:
                                await self._telemetry.record_turn_end(_tel_turn, usage)
                                _tel_turn = None
                                _tel_turn_idx += 1
                                _tel_tool_idx = 0
                                if not agent_started:
                                    _tel_turn = await self._telemetry.record_turn_start(
                                        session_id=session.id,
                                        turn_index=_tel_turn_idx,
                                    )
                            # Reset per-turn accumulators for the next turn
                            turn_text = ""
                            turn_has_tool_calls = False
                            turn_messages_snapshot = list(session.conversation_history)

                        case StreamDone():
                            pass

                # Find the last assistant message for post-loop checks
                last_assistant = next(
                    (m for m in reversed(session.conversation_history) if m["role"] == "assistant"),
                    None,
                )

                # Auto-generate title from the first exchange
                if session.title is None and last_assistant is not None:
                    # Extract assistant text from content blocks
                    assistant_text = ""
                    _raw = last_assistant.get("content")
                    if isinstance(_raw, str):
                        assistant_text = _raw
                    elif isinstance(_raw, list):
                        assistant_text = " ".join(
                            b.get("text", "") for b in _raw if isinstance(b, dict) and b.get("type") == "text"
                        )
                    # Fall back to user prompt alone if assistant had no text (e.g. only tool_use)
                    self._fire_title_task(session, text, assistant_text or "")
                    self._fire_task_creation_task(session, text, assistant_text or "")

                # Real-time artifact extraction from the updated conversation history
                if self._artifact_scanner and self._settings and self._settings.ARTIFACT_AUTO_SCAN:
                    self._fire_realtime_artifact_scan(session)

                # Session stays ACTIVE — only end_session() or cancel_session() will complete it
                if (
                    session.claude_code_executor is None
                    and session.codex_executor is None
                    and session.opencode_executor is None
                ):
                    session.set_activity(ActivityState.IDLE)
                    # Emit a turn-complete signal so clients know the response
                    # stream has ended. Pushed synchronously to avoid async task
                    # scheduling races in the drain path.
                    session.buffer.push_text(
                        MessageType.SUMMARY,
                        {"session_id": session.id, "content": ""},
                    )

            except Exception as e:
                logger.exception("Error processing prompt in session %s", session.id)
                if self._telemetry is not None and _tel_turn is not None:
                    await self._telemetry.mark_turn_interrupted(_tel_turn)
                session.buffer.push_text(
                    MessageType.ERROR,
                    {
                        "session_id": session.id,
                        "content": str(e),
                        "code": "PROMPT_PROCESSING_ERROR",
                    },
                )
                session.fail(str(e))
                self._fire_plan_finalization_task(session)
                self._fire_archive_task(session.id)

        # LLM-path turn complete: deliver any queued user message.
        self.schedule_pending_drain(session)
        return session.id

    # ------------------------------------------------------------------
    # Tool execution
    # ------------------------------------------------------------------

    async def _execute_tool(self, session: ActiveSession, tool_call: ToolCallRequest) -> str:
        """Execute a tool call and stream output to the session buffer."""
        tool_def = self._tool_registry.get(tool_call.tool_name)

        # Format agent prompts into the structured Task/Description/Additional Content
        # sections BEFORE pushing AGENT_SESSION_START so the banner and the executor
        # both receive the same structured text.  Raw code blocks from the user's
        # message are preserved in the Additional Content section.
        if tool_def is not None and tool_def.executor in ("claude_code", "codex", "opencode"):
            raw_prompt = tool_call.tool_input.get("prompt", "")
            if raw_prompt:
                tool_call.tool_input["prompt"] = format_agent_prompt(raw_prompt)

        # For agent tools, push AGENT_SESSION_START (visible banner) then
        # AGENT_GROUP_START (collapsible sub-message group) so the frontend
        # shows "Claude Code started" with the prompt before tool output.
        if tool_def is not None and tool_def.executor in ("claude_code", "codex", "opencode"):
            session.buffer.push_text(
                MessageType.AGENT_SESSION_START,
                {
                    "session_id": session.id,
                    "agent_type": tool_call.tool_name,
                    "display_name": tool_def.display_name or tool_def.name,
                    "prompt": tool_call.tool_input.get("prompt", ""),
                    "working_directory": tool_call.tool_input.get("working_directory", ""),
                },
            )
            session.buffer.push_text(
                MessageType.AGENT_GROUP_START,
                {
                    "session_id": session.id,
                    "tool_name": tool_call.tool_name,
                    "display_name": tool_def.display_name or tool_def.name,
                    "tool_input": tool_call.tool_input,
                },
            )
        else:
            session.buffer.push_text(
                MessageType.TOOL_START,
                {
                    "session_id": session.id,
                    "tool_name": tool_call.tool_name,
                    "display_name": tool_def.display_name or tool_def.name if tool_def else tool_call.tool_name,
                    "tool_input": tool_call.tool_input,
                },
            )
        session.set_executing()
        session.set_activity(ActivityState.EXECUTING_TOOL)
        if tool_def is None:
            error_msg = f"Unknown tool: {tool_call.tool_name}"
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": error_msg,
                    "code": "UNKNOWN_TOOL",
                },
            )
            return error_msg

        # Special handling for agent executors: start streaming in background,
        # return immediately so the outer LLM can finish its turn.
        if tool_def.executor == "claude_code":
            return await self._start_claude_code(session, tool_def, tool_call)
        if tool_def.executor == "codex":
            return await self._start_codex(session, tool_def, tool_call)
        if tool_def.executor == "opencode":
            return await self._start_opencode(session, tool_def, tool_call)

        # Worktree tool always requires explicit user approval before execution,
        # regardless of the session's existing permission mode.  The 'list'
        # action is read-only and is exempted from the approval gate.
        if tool_def.executor == "worktree" and tool_call.tool_input.get("action") != "list":
            if session.permission_manager is None:
                from src.core.permissions import PermissionManager  # noqa: PLC0415

                session.permission_manager = PermissionManager()
            decision = await self._handle_permission_check(session, tool_call.tool_name, tool_call.tool_input)
            if decision.value == "deny":
                deny_msg = f"Worktree operation '{tool_call.tool_input.get('action')}' denied by user."
                session.buffer.push_text(
                    MessageType.TOOL_OUTPUT,
                    {
                        "session_id": session.id,
                        "tool_name": tool_call.tool_name,
                        "content": deny_msg,
                        "stream": "stdout",
                        "is_error": True,
                    },
                )
                session.set_active()
                session.set_activity(ActivityState.PROCESSING_LLM)
                return deny_msg

        executor = self._get_executor(tool_def.executor)

        # Telemetry: record start of this tool call.
        _tel_tool_call = None
        if self._telemetry is not None:
            _tel_tool_call = await self._telemetry.record_tool_start(
                session_id=session.id,
                tool_name=tool_call.tool_name,
                executor_type=tool_def.executor,
            )

        try:
            if tool_def.get_shell_config().stream_output if tool_def.executor == "shell" else False:
                # Streaming execution
                collected_output: list[str] = []
                chunk: ExecutionChunk
                async for chunk in executor.execute_streaming(tool_def, tool_call.tool_input):
                    collected_output.append(chunk.content)
                    session.buffer.push_text(
                        MessageType.TOOL_OUTPUT,
                        {
                            "session_id": session.id,
                            "tool_name": tool_call.tool_name,
                            "content": chunk.content,
                            "stream": chunk.stream,
                        },
                    )
                result_text = "".join(collected_output)
            else:
                # Non-streaming execution
                result = await executor.execute(tool_def, tool_call.tool_input)
                failed = bool(result.exit_code)
                if result.output and result.error:
                    result_text = f"{result.output}\n[error] {result.error}"
                elif result.error:
                    result_text = result.error
                else:
                    result_text = result.output

                session.buffer.push_text(
                    MessageType.TOOL_OUTPUT,
                    {
                        "session_id": session.id,
                        "tool_name": tool_call.tool_name,
                        "content": result_text,
                        "stream": "stdout",
                        "is_error": failed,
                    },
                )

            if self._telemetry is not None and _tel_tool_call is not None:
                await self._telemetry.record_tool_end(_tel_tool_call, status="ok")

        except Exception as e:
            if self._telemetry is not None and _tel_tool_call is not None:
                await self._telemetry.record_tool_end(_tel_tool_call, status="error", error=str(e))
            result_text = f"Tool execution failed: {e}"
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": result_text,
                    "code": "TOOL_EXEC_ERROR",
                },
            )

        session.set_active()
        session.set_activity(ActivityState.PROCESSING_LLM)

        # Real-time artifact scan for tool output and input values
        if self._artifact_scanner and self._settings and self._settings.ARTIFACT_AUTO_SCAN:
            scan_texts = [result_text]
            for v in tool_call.tool_input.values():
                if isinstance(v, str):
                    scan_texts.append(v)
            self._fire_text_artifact_scan(session, scan_texts)

        # Track active worktree context in session metadata so clients can show
        # worktree controls.  Updated on every successful mutating worktree call.
        if tool_def is not None and tool_def.executor == "worktree":
            self._update_session_worktree_meta(session, tool_call, result_text)

        return result_text
