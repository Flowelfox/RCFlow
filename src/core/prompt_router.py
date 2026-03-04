import asyncio
import contextlib
import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config import Settings
from src.core.buffer import MessageType
from src.core.llm import LLMClient, StreamDone, TextChunk, ToolCallRequest, TurnUsage
from src.core.permissions import (
    PermissionDecision,
    PermissionManager,
    PermissionScope,
    classify_risk,
    describe_tool_action,
    get_scope_options,
)
from src.core.session import ActiveSession, ActivityState, SessionManager, SessionStatus, SessionType
from src.executors.base import BaseExecutor, ExecutionChunk
from src.executors.claude_code import ClaudeCodeExecutor
from src.executors.codex import CodexExecutor
from src.executors.http import HttpExecutor
from src.executors.shell import ShellExecutor
from src.models.db import LLMCall
from src.services.tool_manager import ToolManager
from src.services.tool_settings import ToolSettingsManager
from src.tools.loader import ToolDefinition
from src.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class PromptRouter:
    """Routes user prompts through the LLM pipeline with tool execution."""

    def __init__(
        self,
        llm_client: LLMClient,
        session_manager: SessionManager,
        tool_registry: ToolRegistry,
        db_session_factory: async_sessionmaker[AsyncSession] | None = None,
        settings: Settings | None = None,
        tool_settings: ToolSettingsManager | None = None,
        tool_manager: ToolManager | None = None,
    ) -> None:
        self._llm = llm_client
        self._session_manager = session_manager
        self._tool_registry = tool_registry
        self._executors: dict[str, BaseExecutor] = {}
        self._db_session_factory = db_session_factory
        self._settings = settings
        self._tool_settings = tool_settings
        self._tool_manager = tool_manager
        self._pending_log_tasks: set[asyncio.Task[None]] = set()
        self._pending_summary_tasks: set[asyncio.Task[None]] = set()
        self._pending_title_tasks: set[asyncio.Task[None]] = set()
        self._pending_archive_tasks: set[asyncio.Task[None]] = set()

    def ensure_session(self, session_id: str | None = None) -> str:
        """Get an existing session or create a new one. Returns the session ID."""
        session: ActiveSession | None = None
        if session_id:
            session = self._session_manager.get_session(session_id)
            if session is None:
                logger.warning("Session %s not found, will create new session", session_id)
        if session is None:
            session = self._session_manager.create_session(SessionType.CONVERSATIONAL)
        return session.id

    async def cancel_session(self, session_id: str) -> ActiveSession:
        """Cancel a running session, killing any active subprocess.

        Returns the cancelled session.

        Raises:
            ValueError: If the session does not exist.
            RuntimeError: If the session is already in a terminal state.
        """
        session = self._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        terminal_states = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
        if session.status in terminal_states:
            raise RuntimeError(f"Session already in terminal state: {session.status.value}")

        # Kill Claude Code subprocess if running
        had_claude_code = session.claude_code_executor is not None
        if session.claude_code_executor is not None:
            await session.claude_code_executor.cancel()
            session.claude_code_executor = None

        # Cancel the background stream task if running
        if session._claude_code_stream_task is not None and not session._claude_code_stream_task.done():
            session._claude_code_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._claude_code_stream_task
        session._claude_code_stream_task = None

        # Kill Codex subprocess if running
        had_codex = session.codex_executor is not None
        if session.codex_executor is not None:
            await session.codex_executor.cancel()
            session.codex_executor = None

        if session._codex_stream_task is not None and not session._codex_stream_task.done():
            session._codex_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._codex_stream_task
        session._codex_stream_task = None

        # Auto-deny any pending permission requests
        if session.permission_manager is not None:
            session.permission_manager.cancel_all_pending()

        # Close any open agent group before ending the session
        if had_claude_code or had_codex:
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )

        # Push SESSION_END before cancel() closes the buffer
        session.buffer.push_text(
            MessageType.SESSION_END,
            {
                "session_id": session.id,
                "reason": "cancelled",
            },
        )

        session.cancel()
        self._fire_archive_task(session_id)
        logger.info("Cancelled session %s", session_id)
        return session

    async def end_session(self, session_id: str) -> ActiveSession:
        """Gracefully end a session (user-confirmed completion).

        Returns the ended session.

        Raises:
            ValueError: If the session does not exist.
            RuntimeError: If the session is already in a terminal state.
        """
        session = self._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        terminal_states = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
        if session.status in terminal_states:
            raise RuntimeError(f"Session already in terminal state: {session.status.value}")

        # Kill Claude Code subprocess if running
        had_claude_code = session.claude_code_executor is not None
        if session.claude_code_executor is not None:
            await session.claude_code_executor.cancel()
            session.claude_code_executor = None

        # Cancel the background stream task if running
        if session._claude_code_stream_task is not None and not session._claude_code_stream_task.done():
            session._claude_code_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._claude_code_stream_task
        session._claude_code_stream_task = None

        # Kill Codex subprocess if running
        had_codex = session.codex_executor is not None
        if session.codex_executor is not None:
            await session.codex_executor.cancel()
            session.codex_executor = None

        if session._codex_stream_task is not None and not session._codex_stream_task.done():
            session._codex_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._codex_stream_task
        session._codex_stream_task = None

        self._resolve_session_end_ask(session, accepted=True)

        # Close any open agent group before ending the session
        if had_claude_code or had_codex:
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )

        session.buffer.push_text(
            MessageType.SESSION_END,
            {
                "session_id": session.id,
                "reason": "user_ended",
            },
        )

        # Clear paused state so complete() proceeds (explicit user action)
        if session.status == SessionStatus.PAUSED:
            session.status = SessionStatus.ACTIVE
            session.paused_at = None

        session.complete()
        self._fire_archive_task(session_id)
        logger.info("Ended session %s (user confirmed)", session_id)
        return session

    def dismiss_session_end_ask(self, session_id: str) -> None:
        """Mark the last unresolved SESSION_END_ASK as declined (user clicked Continue).

        This persists the dismissal in the buffer so that replaying the history
        does not show the widget as pending again.

        Raises:
            ValueError: If the session does not exist.
        """
        session = self._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        self._resolve_session_end_ask(session, accepted=False)

    def resolve_permission(
        self,
        session_id: str,
        request_id: str,
        decision: str,
        scope: str,
        path_prefix: str | None = None,
    ) -> None:
        """Resolve a pending permission request from the client.

        Raises:
            ValueError: If the session does not exist.
            RuntimeError: If the session does not have interactive permissions
                enabled, or the request_id is unknown.
        """
        session = self._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")
        if session.permission_manager is None:
            raise RuntimeError("Session does not have interactive permissions enabled")

        resolved = session.permission_manager.resolve_request(
            request_id=request_id,
            decision=PermissionDecision(decision),
            scope=PermissionScope(scope),
            path_prefix=path_prefix,
        )
        if not resolved:
            raise RuntimeError(f"Unknown or already-resolved permission request: {request_id}")

    async def pause_session(self, session_id: str) -> ActiveSession:
        """Pause an active session.

        If a Claude Code or Codex subprocess is running, it is killed and the
        background stream task is cancelled.  New prompts are rejected
        until the session is resumed.

        Returns the paused session.

        Raises:
            ValueError: If the session does not exist.
            RuntimeError: If the session cannot be paused (terminal or already paused).
        """
        session = self._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        # Kill Claude Code subprocess if running
        had_claude_code = session.claude_code_executor is not None
        if session.claude_code_executor is not None:
            await session.claude_code_executor.cancel()
            session.claude_code_executor = None

        # Cancel the background stream task if running
        if session._claude_code_stream_task is not None and not session._claude_code_stream_task.done():
            session._claude_code_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._claude_code_stream_task
        session._claude_code_stream_task = None

        # Kill Codex subprocess if running
        had_codex = session.codex_executor is not None
        if session.codex_executor is not None:
            await session.codex_executor.cancel()
            session.codex_executor = None

        if session._codex_stream_task is not None and not session._codex_stream_task.done():
            session._codex_stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await session._codex_stream_task
        session._codex_stream_task = None

        had_agent = had_claude_code or had_codex

        # Auto-deny any pending permission requests
        if session.permission_manager is not None:
            session.permission_manager.cancel_all_pending()

        session.pause()

        # Close any open agent group
        if had_agent:
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )

        session.buffer.push_text(
            MessageType.SESSION_PAUSED,
            {
                "session_id": session.id,
                "paused_at": session.paused_at.isoformat() if session.paused_at else None,
                "claude_code_interrupted": had_agent,
            },
        )

        logger.info("Paused session %s (agent_interrupted=%s)", session_id, had_agent)
        return session

    async def resume_session(self, session_id: str) -> ActiveSession:
        """Resume a paused session.

        The client can subscribe to the session's output channel to receive
        all buffered messages produced while paused, then send new prompts.

        Returns the resumed session.

        Raises:
            ValueError: If the session does not exist.
            RuntimeError: If the session is not paused.
        """
        session = self._session_manager.get_session(session_id)
        if session is None:
            raise ValueError(f"Session not found: {session_id}")

        session.resume()

        session.buffer.push_text(
            MessageType.SESSION_RESUMED,
            {"session_id": session.id},
        )

        # If the underlying work completed while paused, finalize now.
        if session.metadata.pop("completed_while_paused", False):
            if session.codex_executor is not None:
                await self._end_codex_session(session)
            else:
                await self._end_claude_code_session(session)

        logger.info("Resumed session %s", session_id)
        return session

    async def restore_session(self, session_id: str) -> ActiveSession:
        """Restore an archived session from the database back to active state.

        Loads session metadata, conversation history, and buffer messages.
        For Claude Code sessions, prepares the executor state for lazy restart
        on the next user message.

        Returns the restored session.

        Raises:
            ValueError: If the session is not found in the DB.
            RuntimeError: If the session is already active or DB is unavailable.
        """
        if self._db_session_factory is None:
            raise RuntimeError("Database is not configured; cannot restore sessions")

        async with self._db_session_factory() as db:
            session = await self._session_manager.restore_session(session_id, db)

        # If this was a Claude Code session, set up executor for lazy restart
        cc_session_id = session.metadata.get("claude_code_session_id")
        cc_tool_name = session.metadata.get("claude_code_tool_name")
        if cc_session_id and cc_tool_name:
            tool_def = self._tool_registry.get(cc_tool_name)
            if tool_def is not None and tool_def.executor == "claude_code":
                binary_path = "claude"
                config = tool_def.get_claude_code_config()
                binary_path = config.binary_path
                if self._tool_manager:
                    resolved = self._tool_manager.get_binary_path("claude_code")
                    if resolved:
                        binary_path = resolved

                executor = ClaudeCodeExecutor(
                    binary_path=binary_path,
                    session_id=cc_session_id,
                    extra_env=self._build_claude_code_extra_env(),
                    config_overrides=self._get_managed_config_overrides("claude_code"),
                )
                # Pre-set the tool_def and last_parameters so restart_with_prompt works
                executor._tool_def = tool_def
                cc_params = session.metadata.get("claude_code_parameters", {})
                executor._last_parameters = cc_params

                session.claude_code_executor = executor
                session.session_type = SessionType.LONG_RUNNING

        # Restore permission rules if they were saved
        saved_rules = session.metadata.get("permission_rules")
        if saved_rules:
            pm = PermissionManager()
            pm.restore_rules(saved_rules)
            session.permission_manager = pm

        # If this was a Codex session, set up executor for lazy restart
        codex_thread_id = session.metadata.get("codex_thread_id")
        codex_tool_name = session.metadata.get("codex_tool_name")
        if codex_thread_id and codex_tool_name:
            tool_def = self._tool_registry.get(codex_tool_name)
            if tool_def is not None and tool_def.executor == "codex":
                binary_path = "codex"
                config = tool_def.get_codex_config()
                binary_path = config.binary_path
                if self._tool_manager:
                    resolved = self._tool_manager.get_binary_path("codex")
                    if resolved:
                        binary_path = resolved

                codex_executor = CodexExecutor(
                    binary_path=binary_path,
                    thread_id=codex_thread_id,
                    extra_env=self._build_codex_extra_env(),
                    config_overrides=self._get_managed_config_overrides("codex"),
                )
                codex_executor._tool_def = tool_def
                codex_params = session.metadata.get("codex_parameters", {})
                codex_executor._last_parameters = codex_params

                session.codex_executor = codex_executor
                session.session_type = SessionType.LONG_RUNNING

        session.buffer.push_text(
            MessageType.SESSION_RESTORED,
            {"session_id": session.id},
        )

        logger.info("Restored session %s via prompt router", session_id)
        return session

    _SESSION_END_ASK_TAG = "[SessionEndAsk]"

    @staticmethod
    def _resolve_session_end_ask(session: ActiveSession, *, accepted: bool) -> None:
        """Mark the last unresolved SESSION_END_ASK in the buffer as accepted/declined."""
        for msg in reversed(session.buffer.text_history):
            if msg.message_type == MessageType.SESSION_END_ASK and "accepted" not in msg.data:
                msg.data["accepted"] = accepted
                return

    @staticmethod
    def _contains_session_end_ask(assistant_message: dict[str, Any]) -> bool:
        """Check if an assistant message contains the [SessionEndAsk] tag."""
        tag = PromptRouter._SESSION_END_ASK_TAG
        content = assistant_message.get("content")
        if isinstance(content, str):
            return tag in content
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and tag in block.get("text", ""):
                    return True
        return False

    def _build_claude_code_extra_env(self) -> dict[str, str]:
        """Build extra environment variables for Claude Code subprocesses."""
        extra_env: dict[str, str] = {}

        # Check if the tool has its own provider configured — if so, skip
        # injecting the global ANTHROPIC_API_KEY so the settings.json env
        # section takes precedence.
        tool_provider = ""
        if self._tool_settings:
            tool_provider = self._tool_settings.get_settings("claude_code").get("provider", "")

        if not tool_provider and self._settings and self._settings.ANTHROPIC_API_KEY:
            extra_env["ANTHROPIC_API_KEY"] = self._settings.ANTHROPIC_API_KEY

        if self._tool_settings:
            config_dir = self._tool_settings.get_config_dir("claude_code")
            config_dir.mkdir(parents=True, exist_ok=True)
            extra_env["CLAUDE_CONFIG_DIR"] = str(config_dir)
        return extra_env

    def _build_codex_extra_env(self) -> dict[str, str]:
        """Build extra environment variables for Codex CLI subprocesses."""
        extra_env: dict[str, str] = {}

        # Check if the tool has its own provider configured — if so,
        # inject env vars from the per-tool settings instead of the
        # global CODEX_API_KEY.  Unlike Claude Code (which natively reads
        # settings.json via CLAUDE_CONFIG_DIR), Codex CLI only reads API
        # keys from actual environment variables, so we must inject them.
        tool_settings: dict[str, Any] = {}
        if self._tool_settings:
            tool_settings = self._tool_settings.get_settings("codex")

        tool_provider = tool_settings.get("provider", "")

        if tool_provider == "chatgpt":
            # ChatGPT subscription auth — no API key needed; Codex CLI
            # reads OAuth tokens from $CODEX_HOME/auth.json instead.
            pass
        elif tool_provider:
            # Inject env vars from the per-tool settings env section
            tool_env = tool_settings.get("env", {})
            if isinstance(tool_env, dict):
                extra_env.update(tool_env)
        elif self._settings and self._settings.CODEX_API_KEY:
            extra_env["CODEX_API_KEY"] = self._settings.CODEX_API_KEY

        if self._tool_settings:
            config_dir = self._tool_settings.get_config_dir("codex")
            config_dir.mkdir(parents=True, exist_ok=True)
            extra_env["CODEX_HOME"] = str(config_dir)

            # For ChatGPT auth, symlink the default auth.json into CODEX_HOME
            # so the isolated Codex instance can use the user's OAuth tokens.
            if tool_provider == "chatgpt":
                self._ensure_codex_auth_symlink(config_dir)

        return extra_env

    @staticmethod
    def _ensure_codex_auth_symlink(codex_home: Path) -> None:
        """Symlink ``~/.codex/auth.json`` into *codex_home* if not already present.

        Uses a symlink so that token refreshes by the Codex CLI are
        automatically visible to both the user's default install and RCFlow.
        """
        default_auth = Path.home() / ".codex" / "auth.json"
        target_auth = codex_home / "auth.json"

        if target_auth.is_file() and not target_auth.is_symlink():
            # A real file already exists (e.g. user ran codex login with
            # this CODEX_HOME) — don't overwrite it.
            return

        if target_auth.is_symlink() and not target_auth.exists():
            # Broken symlink — remove it so we can recreate.
            target_auth.unlink()

        if target_auth.exists():
            return

        if not default_auth.is_file():
            logger.warning(
                "ChatGPT auth selected but ~/.codex/auth.json not found. "
                "Run 'codex login' first to authenticate."
            )
            return

        try:
            target_auth.symlink_to(default_auth)
            logger.info("Symlinked %s -> %s", target_auth, default_auth)
        except OSError:
            logger.warning("Failed to symlink auth.json", exc_info=True)

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

        if executor_type not in self._executors:
            match executor_type:
                case "shell":
                    self._executors[executor_type] = ShellExecutor()
                case "http":
                    self._executors[executor_type] = HttpExecutor()
                case _:
                    raise ValueError(f"Unknown executor type: {executor_type}")
        return self._executors[executor_type]

    _MENTION_RE = re.compile(r"(?:^|(?<=\s))@(\S+)")

    def _extract_project_mentions(self, text: str) -> list[str]:
        """Extract @ProjectName mentions from user text."""
        return self._MENTION_RE.findall(text)

    def _build_project_context(self, mentions: list[str]) -> str | None:
        """Resolve mentions to project directories and build a context string.

        Returns None if no mentions resolve to valid directories.
        """
        if not self._settings:
            return None

        projects_dir = self._settings.PROJECTS_DIR.expanduser().resolve()
        resolved: list[tuple[str, Path]] = []
        for name in mentions:
            project_path = projects_dir / name
            if project_path.is_dir():
                resolved.append((name, project_path))

        if not resolved:
            return None

        if len(resolved) == 1:
            name, path = resolved[0]
            return (
                f'[Context: This message references project "{name}" '
                f"located at {path}. All instructions in this message "
                f"relate to this project.]"
            )

        lines = ", ".join(f'"{name}" ({path})' for name, path in resolved)
        return (
            f"[Context: This message references projects: {lines}. "
            f"All instructions in this message relate to these projects.]"
        )

    async def handle_prompt(self, text: str, session_id: str | None = None) -> str:
        """Handle a user prompt. Creates a new session or resumes an existing one.

        Returns the session ID.
        """
        resolved_id = self.ensure_session(session_id)
        session = self._session_manager.get_session(resolved_id)
        assert session is not None  # ensure_session guarantees this

        session.touch()

        # Auto-resume paused sessions when a new prompt arrives
        if session.status == SessionStatus.PAUSED:
            await self.resume_session(resolved_id)

        # If session has an active Claude Code executor, forward message directly
        if session.claude_code_executor is not None:
            session.buffer.push_text(
                MessageType.TEXT_CHUNK,
                {"content": text, "role": "user"},
            )
            await self._forward_to_claude_code(session, text)
            return session.id

        # If session has an active Codex executor, forward message directly
        if session.codex_executor is not None:
            session.buffer.push_text(
                MessageType.TEXT_CHUNK,
                {"content": text, "role": "user"},
            )
            await self._forward_to_codex(session, text)
            return session.id

        # Serialize prompt processing per session to prevent concurrent writes
        # to conversation_history, which would break tool_use/tool_result pairing.
        async with session._prompt_lock:
            session.set_active()
            session.set_activity(ActivityState.PROCESSING_LLM)
            self._resolve_session_end_ask(session, accepted=False)

            # Detect @ProjectName mentions and build LLM context
            mentions = self._extract_project_mentions(text)
            project_context = self._build_project_context(mentions) if mentions else None

            if project_context:
                content: str | list[dict[str, Any]] = [
                    {"type": "text", "text": project_context, "cache_control": {"type": "ephemeral"}},
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

            # Push the original user prompt to the buffer (no injected context)
            session.buffer.push_text(
                MessageType.TEXT_CHUNK,
                {
                    "content": text,
                    "role": "user",
                },
            )

            # Define tool execution callback
            agent_started = False

            async def execute_tool(tool_call: ToolCallRequest) -> str:
                nonlocal agent_started
                result = await self._execute_tool(session, tool_call)
                if session.claude_code_executor is not None or session.codex_executor is not None:
                    agent_started = True
                return result

            # Per-turn accumulators for logging
            turn_text = ""
            turn_has_tool_calls = False
            # Snapshot messages before this turn (for request_messages logging)
            turn_messages_snapshot: list[dict[str, Any]] = list(session.conversation_history)

            # Run the agentic loop
            try:
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

                        case ToolCallRequest():
                            turn_has_tool_calls = True

                        case StreamDone(usage=usage) if usage is not None:
                            self._fire_log_task(
                                session_id=session.id,
                                usage=usage,
                                has_tool_calls=turn_has_tool_calls,
                                request_messages=turn_messages_snapshot,
                                response_text=turn_text or None,
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

                # Check if the LLM included <SessionEndAsk> in its last response
                if (
                    session.claude_code_executor is None
                    and session.codex_executor is None
                    and last_assistant
                    and self._contains_session_end_ask(last_assistant)
                ):
                    session.buffer.push_text(
                        MessageType.SESSION_END_ASK,
                        {"session_id": session.id},
                    )

                # Auto-generate title from the first exchange
                if session.title is None and last_assistant is not None:
                    # Extract assistant text from content blocks
                    assistant_text = ""
                    content = last_assistant.get("content")
                    if isinstance(content, str):
                        assistant_text = content
                    elif isinstance(content, list):
                        assistant_text = " ".join(
                            b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"
                        )
                    # Fall back to user prompt alone if assistant had no text (e.g. only tool_use)
                    self._fire_title_task(session, text, assistant_text or "")

                # Session stays ACTIVE — only end_session() or cancel_session() will complete it
                if session.claude_code_executor is None and session.codex_executor is None:
                    session.set_activity(ActivityState.IDLE)

            except Exception as e:
                logger.exception("Error processing prompt in session %s", session.id)
                session.buffer.push_text(
                    MessageType.ERROR,
                    {
                        "session_id": session.id,
                        "content": str(e),
                        "code": "PROMPT_PROCESSING_ERROR",
                    },
                )
                session.fail(str(e))
                self._fire_archive_task(session.id)

        return session.id

    async def _execute_tool(self, session: ActiveSession, tool_call: ToolCallRequest) -> str:
        """Execute a tool call and stream output to the session buffer."""
        tool_def = self._tool_registry.get(tool_call.tool_name)

        # For agent tools, push AGENT_GROUP_START instead of TOOL_START
        # so the frontend can group all sub-messages under one collapsible block.
        if tool_def is not None and tool_def.executor in ("claude_code", "codex"):
            session.buffer.push_text(
                MessageType.AGENT_GROUP_START,
                {
                    "session_id": session.id,
                    "tool_name": tool_call.tool_name,
                    "tool_input": tool_call.tool_input,
                },
            )
        else:
            session.buffer.push_text(
                MessageType.TOOL_START,
                {
                    "session_id": session.id,
                    "tool_name": tool_call.tool_name,
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

        executor = self._get_executor(tool_def.executor)

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
                result_text = result.output
                if result.error:
                    result_text += f"\n[error] {result.error}"

                session.buffer.push_text(
                    MessageType.TOOL_OUTPUT,
                    {
                        "session_id": session.id,
                        "tool_name": tool_call.tool_name,
                        "content": result_text,
                        "stream": "stdout",
                    },
                )

        except Exception as e:
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
        return result_text

    def _resolve_working_directory(self, working_dir: str) -> Path:
        """Resolve a working directory path, using configured PROJECTS_DIR for ~ expansion.

        The system prompt tells the LLM to use the absolute PROJECTS_DIR path, so
        most calls will already be absolute. This also handles the legacy ~/Projects
        prefix in case the LLM still uses it.
        """
        if self._settings is not None:
            projects_dir = self._settings.PROJECTS_DIR.expanduser().resolve()
            projects_dir_str = str(projects_dir)
            # Handle absolute path that already matches configured PROJECTS_DIR
            if working_dir.startswith(projects_dir_str):
                return Path(working_dir)
            # Handle ~/Projects prefix (legacy or fallback)
            if working_dir.startswith("~/Projects"):
                suffix = working_dir[len("~/Projects") :]
                return projects_dir / suffix.lstrip("/")
        return Path(working_dir).expanduser()

    async def _start_claude_code(
        self,
        session: ActiveSession,
        tool_def: ToolDefinition,
        tool_call: ToolCallRequest,
    ) -> str:
        """Start a Claude Code session: spawn subprocess, begin background streaming."""
        working_dir = tool_call.tool_input.get("working_directory", ".")
        working_path = self._resolve_working_directory(working_dir)
        try:
            is_dir = working_path.is_dir()
        except OSError as e:
            error_msg = f"Cannot access directory {working_dir}: {e}"
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": error_msg,
                    "code": "INVALID_WORKING_DIRECTORY",
                },
            )
            return error_msg
        if not is_dir:
            error_msg = f"Directory does not exist: {working_dir}"
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": error_msg,
                    "code": "INVALID_WORKING_DIRECTORY",
                },
            )
            return error_msg

        # Replace the working_directory in tool_input with the resolved absolute path
        tool_call.tool_input["working_directory"] = str(working_path)

        executor = self._get_executor(tool_def.executor, tool_def)
        assert isinstance(executor, ClaudeCodeExecutor)

        session.claude_code_executor = executor
        session.session_type = SessionType.LONG_RUNNING
        session.set_activity(ActivityState.RUNNING_SUBPROCESS)

        # Enable interactive permissions if configured
        effective_config = {**tool_def.executor_config.get("claude_code", {})}
        for k, v in self._get_managed_config_overrides("claude_code").items():
            if v not in (None, ""):
                effective_config[k] = v
        if effective_config.get("default_permission_mode") == "interactive":
            session.permission_manager = PermissionManager()

        # Store CC metadata for potential session restore
        session.metadata["claude_code_session_id"] = executor.session_id
        session.metadata["claude_code_working_directory"] = str(working_path)
        session.metadata["claude_code_tool_name"] = tool_def.name
        session.metadata["claude_code_parameters"] = tool_call.tool_input

        # Start streaming in a background task that reads events and pushes to buffer
        task = asyncio.create_task(self._stream_claude_code_events(session, executor, tool_def, tool_call))
        session._claude_code_stream_task = task

        return f"Claude Code session started in {working_path}"

    async def _handle_permission_check(
        self,
        session: ActiveSession,
        tool_name: str,
        tool_input: dict[str, Any],
    ) -> PermissionDecision:
        """Check permissions for a tool use, potentially asking the user.

        If the session has a :class:`PermissionManager` and no cached rule
        covers the request, a ``PERMISSION_REQUEST`` message is pushed to the
        buffer and the coroutine blocks until the user responds or the timeout
        expires.

        Returns the final decision (``ALLOW`` or ``DENY``).
        """
        pm = session.permission_manager
        if pm is None:
            return PermissionDecision.ALLOW

        # 1. Check cached rules first
        cached = pm.check_cached(tool_name, tool_input)
        if cached is not None:
            logger.debug(
                "Permission cache hit: %s for %s (session=%s)",
                cached.value,
                tool_name,
                session.id,
            )
            return cached

        # 2. No cached rule — ask the user
        pending = pm.create_request(tool_name, tool_input)
        risk_level = classify_risk(tool_name, tool_input)
        description = describe_tool_action(tool_name, tool_input)

        session.set_activity(ActivityState.AWAITING_PERMISSION)

        session.buffer.push_text(
            MessageType.PERMISSION_REQUEST,
            {
                "session_id": session.id,
                "request_id": pending.request_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "description": description,
                "risk_level": risk_level,
                "scope_options": get_scope_options(tool_name),
            },
        )

        # 3. Wait for user response (blocks the stream reading)
        resolved = await pm.wait_for_response(pending.request_id)

        session.set_activity(ActivityState.RUNNING_SUBPROCESS)

        if resolved.timed_out:
            logger.warning(
                "Permission request timed out for %s (session=%s, request=%s)",
                tool_name,
                session.id,
                pending.request_id,
            )

        return resolved.decision if resolved.decision else PermissionDecision.DENY

    async def _relay_claude_code_stream(
        self,
        session: ActiveSession,
        stream: AsyncGenerator[ExecutionChunk, None],
    ) -> None:
        """Parse Claude Code stream-json events and push structured buffer messages.

        Translates raw JSON event lines into the same message types used by the
        RCFlow LLM pipeline (TEXT_CHUNK, TOOL_START) instead of forwarding
        opaque TOOL_OUTPUT blobs.

        When the session has a :class:`PermissionManager`, ``tool_use`` blocks
        in ``assistant`` events are intercepted for permission approval before
        execution proceeds.
        """
        async for chunk in stream:
            line = chunk.content.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Non-JSON output (e.g. stderr leaking to stdout) — relay as text
                session.buffer.push_text(
                    MessageType.TEXT_CHUNK,
                    {
                        "session_id": session.id,
                        "content": line,
                        "finished": False,
                    },
                )
                continue

            event_type = event.get("type")

            if event_type == "assistant":
                message = event.get("message", {})
                for block in message.get("content", []):
                    block_type = block.get("type")
                    if block_type == "text":
                        session.buffer.push_text(
                            MessageType.TEXT_CHUNK,
                            {
                                "session_id": session.id,
                                "content": block["text"],
                                "finished": False,
                            },
                        )
                    elif block_type == "tool_use":
                        tool_name = block.get("name", "unknown")
                        tool_input = block.get("input", {})

                        # Permission check for interactive sessions
                        if session.permission_manager is not None:
                            decision = await self._handle_permission_check(
                                session, tool_name, tool_input
                            )
                            if decision == PermissionDecision.DENY:
                                session.buffer.push_text(
                                    MessageType.TOOL_START,
                                    {
                                        "session_id": session.id,
                                        "tool_name": tool_name,
                                        "tool_input": tool_input,
                                        "permission_denied": True,
                                    },
                                )
                                continue

                        if tool_name == "EnterPlanMode":
                            session.buffer.push_text(
                                MessageType.PLAN_MODE_ASK,
                                {"session_id": session.id},
                            )
                        elif tool_name == "ExitPlanMode":
                            session.buffer.push_text(
                                MessageType.PLAN_REVIEW_ASK,
                                {"session_id": session.id},
                            )
                        else:
                            session.buffer.push_text(
                                MessageType.TOOL_START,
                                {
                                    "session_id": session.id,
                                    "tool_name": tool_name,
                                    "tool_input": tool_input,
                                },
                            )
            elif event_type == "result":
                session.set_activity(ActivityState.IDLE)
                result_text = event.get("result", "")
                result_subtype = event.get("subtype", "")

                if result_subtype == "max_turns":
                    # Claude Code hit --max-turns limit; always notify the user
                    summary_text = result_text or "Claude Code reached the maximum number of turns for this invocation."
                    self._fire_summary_task(session, summary_text, push_session_end_ask=True)
                elif result_text:
                    self._fire_summary_task(session, result_text, push_session_end_ask=True)
                else:
                    # Result event with no text and no subtype — still notify the user
                    session.buffer.push_text(
                        MessageType.SESSION_END_ASK,
                        {"session_id": session.id},
                    )
            else:
                logger.debug("Skipping unknown Claude Code event type: %s", event_type)

    async def _stream_claude_code_events(
        self,
        session: ActiveSession,
        executor: ClaudeCodeExecutor,
        tool_def: ToolDefinition,
        tool_call: ToolCallRequest,
    ) -> None:
        """Background task: read Claude Code events and push to session buffer."""
        try:
            await self._relay_claude_code_stream(session, executor.execute_streaming(tool_def, tool_call.tool_input))
        except Exception as e:
            logger.exception("Claude Code streaming error in session %s", session.id)
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": f"Claude Code error: {e}",
                    "code": "CLAUDE_CODE_ERROR",
                },
            )
            await self._end_claude_code_session(session)
            return

        # Detect unexpected exit (no result event received)
        if not executor.got_result:
            exit_code = executor.exit_code
            logger.warning(
                "Claude Code exited without result event (session=%s, exit_code=%s)",
                session.id,
                exit_code,
            )
            session.set_activity(ActivityState.IDLE)
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": (
                        f"Claude Code process exited unexpectedly (exit code: {exit_code}). "
                        "This may be caused by a timeout, out-of-memory condition, or internal error. "
                        "You can send another message to restart it."
                    ),
                    "code": "CLAUDE_CODE_UNEXPECTED_EXIT",
                },
            )
            await executor.stop_process()
            return

        await executor.stop_process()

        session.buffer.push_text(
            MessageType.AGENT_GROUP_END,
            {"session_id": session.id},
        )

        # The process is killed after each turn to prevent memory exhaustion.
        # Follow-up messages use restart_with_prompt (--resume) to respawn.
        logger.info(
            "Claude Code initial streaming finished (session=%s)",
            session.id,
        )

    async def _end_claude_code_session(self, session: ActiveSession) -> None:
        """Clean up Claude Code state when the session ends."""
        if session.claude_code_executor is not None:
            await session.claude_code_executor.stop_process()
        session.claude_code_executor = None
        session._claude_code_stream_task = None

        if session.status == SessionStatus.PAUSED:
            # Defer end/archive — user will see output on resume
            session.complete()  # sets completed_while_paused flag
            return

        session.buffer.push_text(
            MessageType.SESSION_END,
            {
                "session_id": session.id,
                "reason": "claude_code_finished",
            },
        )
        session.complete()
        self._fire_archive_task(session.id)

    # --- Codex CLI methods ---

    async def _start_codex(
        self,
        session: ActiveSession,
        tool_def: ToolDefinition,
        tool_call: ToolCallRequest,
    ) -> str:
        """Start a Codex CLI session: spawn subprocess, begin background streaming."""
        working_dir = tool_call.tool_input.get("working_directory", ".")
        working_path = self._resolve_working_directory(working_dir)
        try:
            is_dir = working_path.is_dir()
        except OSError as e:
            error_msg = f"Cannot access directory {working_dir}: {e}"
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": error_msg,
                    "code": "INVALID_WORKING_DIRECTORY",
                },
            )
            return error_msg
        if not is_dir:
            error_msg = f"Directory does not exist: {working_dir}"
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": error_msg,
                    "code": "INVALID_WORKING_DIRECTORY",
                },
            )
            return error_msg

        # Replace the working_directory in tool_input with the resolved absolute path
        tool_call.tool_input["working_directory"] = str(working_path)

        executor = self._get_executor(tool_def.executor, tool_def)
        assert isinstance(executor, CodexExecutor)

        session.codex_executor = executor
        session.session_type = SessionType.LONG_RUNNING
        session.set_activity(ActivityState.RUNNING_SUBPROCESS)

        # Store metadata for potential session restore (thread_id set after first event)
        session.metadata["codex_working_directory"] = str(working_path)
        session.metadata["codex_tool_name"] = tool_def.name
        session.metadata["codex_parameters"] = tool_call.tool_input

        # Start streaming in a background task that reads events and pushes to buffer
        task = asyncio.create_task(self._stream_codex_events(session, executor, tool_def, tool_call))
        session._codex_stream_task = task

        return f"Codex session started in {working_path}"

    async def _relay_codex_stream(
        self,
        session: ActiveSession,
        stream: AsyncGenerator[ExecutionChunk, None],
    ) -> None:
        """Parse Codex CLI JSONL events and push structured buffer messages.

        Translates Codex event types (item.started/updated/completed,
        turn.completed/failed) into the same message types used by the
        RCFlow LLM pipeline.
        """
        # Track last emitted text for agent_message items to enable incremental updates
        last_agent_text: dict[str, str] = {}
        # Collect agent_message text after the last tool call for the summary
        post_tool_text_chunks: list[str] = []

        async for chunk in stream:
            line = chunk.content.strip()
            if not line:
                continue

            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                session.buffer.push_text(
                    MessageType.TEXT_CHUNK,
                    {
                        "session_id": session.id,
                        "content": line,
                        "finished": False,
                    },
                )
                continue

            if not isinstance(event, dict):
                continue

            event_type = event.get("type")

            # Persist thread_id once we receive it
            if event_type == "thread.started":
                thread_id = event.get("thread_id")
                if thread_id and session.codex_executor:
                    session.metadata["codex_thread_id"] = thread_id

            elif event_type == "item.started":
                item = event.get("item", {})
                item_type = item.get("type")
                if item_type == "command_execution":
                    post_tool_text_chunks.clear()
                    session.buffer.push_text(
                        MessageType.TOOL_START,
                        {
                            "session_id": session.id,
                            "tool_name": "command_execution",
                            "tool_input": {"command": item.get("command", "")},
                        },
                    )
                elif item_type == "file_change":
                    post_tool_text_chunks.clear()
                    session.buffer.push_text(
                        MessageType.TOOL_START,
                        {
                            "session_id": session.id,
                            "tool_name": "file_change",
                            "tool_input": {},
                        },
                    )
                elif item_type == "mcp_tool_call":
                    post_tool_text_chunks.clear()
                    session.buffer.push_text(
                        MessageType.TOOL_START,
                        {
                            "session_id": session.id,
                            "tool_name": f"mcp:{item.get('server', '')}:{item.get('tool', '')}",
                            "tool_input": item.get("arguments", {}),
                        },
                    )

            elif event_type == "item.updated":
                item = event.get("item", {})
                item_type = item.get("type")
                item_id = item.get("id", "")
                if item_type == "agent_message":
                    # Emit only the new text since last update
                    full_text = item.get("text", "")
                    prev = last_agent_text.get(item_id, "")
                    delta = full_text[len(prev) :]
                    if delta:
                        post_tool_text_chunks.append(delta)
                        session.buffer.push_text(
                            MessageType.TEXT_CHUNK,
                            {
                                "session_id": session.id,
                                "content": delta,
                                "finished": False,
                            },
                        )
                    last_agent_text[item_id] = full_text

            elif event_type == "item.completed":
                item = event.get("item", {})
                item_type = item.get("type")
                item_id = item.get("id", "")
                if item_type == "agent_message":
                    # Emit any remaining delta
                    full_text = item.get("text", "")
                    prev = last_agent_text.get(item_id, "")
                    delta = full_text[len(prev) :]
                    if delta:
                        post_tool_text_chunks.append(delta)
                        session.buffer.push_text(
                            MessageType.TEXT_CHUNK,
                            {
                                "session_id": session.id,
                                "content": delta,
                                "finished": False,
                            },
                        )
                    last_agent_text.pop(item_id, None)
                elif item_type == "command_execution":
                    output = item.get("aggregated_output", "")
                    if output:
                        session.buffer.push_text(
                            MessageType.TOOL_OUTPUT,
                            {
                                "session_id": session.id,
                                "tool_name": "command_execution",
                                "content": output,
                                "stream": "stdout",
                            },
                        )

            elif event_type == "turn.completed":
                session.set_activity(ActivityState.IDLE)
                summary_text = "".join(post_tool_text_chunks).strip() or "Codex task completed"
                self._fire_summary_task(session, summary_text, push_session_end_ask=True)

            elif event_type == "turn.failed":
                error = event.get("error", {})
                session.buffer.push_text(
                    MessageType.ERROR,
                    {
                        "session_id": session.id,
                        "content": error.get("message", "Codex turn failed"),
                        "code": "CODEX_TURN_FAILED",
                    },
                )

            elif event_type == "error":
                session.buffer.push_text(
                    MessageType.ERROR,
                    {
                        "session_id": session.id,
                        "content": event.get("message", "Codex error"),
                        "code": "CODEX_ERROR",
                    },
                )

            else:
                logger.debug("Skipping unknown Codex event type: %s", event_type)

    async def _stream_codex_events(
        self,
        session: ActiveSession,
        executor: CodexExecutor,
        tool_def: ToolDefinition,
        tool_call: ToolCallRequest,
    ) -> None:
        """Background task: read Codex CLI events and push to session buffer."""
        try:
            await self._relay_codex_stream(session, executor.execute_streaming(tool_def, tool_call.tool_input))
        except Exception as e:
            logger.exception("Codex streaming error in session %s", session.id)
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": f"Codex error: {e}",
                    "code": "CODEX_ERROR",
                },
            )
            await self._end_codex_session(session)
            return

        await executor.stop_process()

        session.buffer.push_text(
            MessageType.AGENT_GROUP_END,
            {"session_id": session.id},
        )

        # Codex process exits after each turn (one-shot model).
        # Follow-up messages use restart_with_prompt (codex exec resume) to respawn.
        logger.info(
            "Codex initial streaming finished (session=%s)",
            session.id,
        )

    async def _end_codex_session(self, session: ActiveSession) -> None:
        """Clean up Codex state when the session ends."""
        if session.codex_executor is not None:
            await session.codex_executor.stop_process()
        session.codex_executor = None
        session._codex_stream_task = None

        if session.status == SessionStatus.PAUSED:
            session.complete()
            return

        session.buffer.push_text(
            MessageType.SESSION_END,
            {
                "session_id": session.id,
                "reason": "codex_finished",
            },
        )
        session.complete()
        self._fire_archive_task(session.id)

    async def _forward_to_codex(self, session: ActiveSession, text: str) -> None:
        """Forward a follow-up message to the active Codex session.

        Codex CLI uses one-shot processes, so follow-ups always spawn a new
        process with ``codex exec resume THREAD_ID``.
        """
        executor = session.codex_executor
        if executor is None:
            return

        if session.status == SessionStatus.PAUSED:
            return

        session.set_activity(ActivityState.RUNNING_SUBPROCESS)

        # Open a new agent group for this follow-up turn
        session.buffer.push_text(
            MessageType.AGENT_GROUP_START,
            {
                "session_id": session.id,
                "tool_name": "codex",
            },
        )

        # Codex always spawns a new process for follow-ups (no persistent stdin)
        session._codex_stream_task = asyncio.create_task(self._restart_codex_with_prompt(session, executor, text))

    async def _restart_codex_with_prompt(
        self,
        session: ActiveSession,
        executor: CodexExecutor,
        prompt: str,
    ) -> None:
        """Spawn a new Codex resume process and stream events for a follow-up."""
        try:
            await self._relay_codex_stream(session, executor.restart_with_prompt(prompt))
        except Exception as e:
            logger.exception("Codex restart error in session %s", session.id)
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": f"Codex error: {e}",
                    "code": "CODEX_ERROR",
                },
            )
            await self._end_codex_session(session)
            return

        await executor.stop_process()

        session.buffer.push_text(
            MessageType.AGENT_GROUP_END,
            {"session_id": session.id},
        )

    def _fire_title_task(self, session: ActiveSession, user_text: str, assistant_text: str) -> None:
        """Schedule a background task to generate a session title."""
        task = asyncio.create_task(self._generate_and_set_title(session, user_text, assistant_text))
        self._pending_title_tasks.add(task)
        task.add_done_callback(self._pending_title_tasks.discard)

    async def _generate_and_set_title(self, session: ActiveSession, user_text: str, assistant_text: str) -> None:
        """Generate a title and assign it to the session. Never raises."""
        try:
            title = await self._llm.generate_title(user_text, assistant_text)
            session.title = title
            logger.info("Generated title for session %s: %s", session.id, title)
        except Exception:
            logger.exception("Failed to generate title for session %s", session.id)

    def _fire_summary_task(self, session: ActiveSession, text: str, *, push_session_end_ask: bool = False) -> None:
        """Schedule a background task to summarize Claude Code result text."""
        task = asyncio.create_task(self._summarize_and_push(session, text, push_session_end_ask=push_session_end_ask))
        self._pending_summary_tasks.add(task)
        task.add_done_callback(self._pending_summary_tasks.discard)

    async def _summarize_and_push(
        self, session: ActiveSession, text: str, *, push_session_end_ask: bool = False
    ) -> None:
        """Generate a TTS-friendly summary and push it to the session buffer."""
        try:
            summary = await self._llm.summarize(text)
            session.buffer.push_text(
                MessageType.SUMMARY,
                {
                    "session_id": session.id,
                    "content": summary,
                },
            )
        except Exception:
            logger.exception("Failed to generate summary for session %s", session.id)
        finally:
            if push_session_end_ask:
                session.buffer.push_text(
                    MessageType.SESSION_END_ASK,
                    {"session_id": session.id},
                )

    async def _forward_to_claude_code(self, session: ActiveSession, text: str) -> None:
        """Forward a follow-up message to the active Claude Code subprocess.

        The process normally stays alive between turns, so messages are sent
        via stdin.  If the process has unexpectedly exited, fall back to
        restarting it with the same ``--session-id``.
        """
        executor = session.claude_code_executor
        if executor is None:
            return

        if session.status == SessionStatus.PAUSED:
            return

        session.set_activity(ActivityState.RUNNING_SUBPROCESS)

        # Open a new agent group for this follow-up turn
        session.buffer.push_text(
            MessageType.AGENT_GROUP_START,
            {
                "session_id": session.id,
                "tool_name": "claude_code",
            },
        )

        if executor.is_running:
            # Process still alive — send directly via stdin
            try:
                await executor.send_input(text)
            except RuntimeError:
                logger.warning("Failed to send input to Claude Code (session=%s), restarting", session.id)
                # Kill the old process explicitly — it may still be alive with broken stdin
                await executor.cancel()
                session._claude_code_stream_task = asyncio.create_task(
                    self._restart_claude_code_with_prompt(session, executor, text)
                )
                return

            # Read response events in background
            session._claude_code_stream_task = asyncio.create_task(self._read_claude_code_followup(session, executor))
        else:
            # Process unexpectedly exited — restart with same session-id as fallback
            session._claude_code_stream_task = asyncio.create_task(
                self._restart_claude_code_with_prompt(session, executor, text)
            )

    async def _restart_claude_code_with_prompt(
        self,
        session: ActiveSession,
        executor: ClaudeCodeExecutor,
        prompt: str,
    ) -> None:
        """Restart Claude Code process and stream events for a follow-up."""
        try:
            await self._relay_claude_code_stream(session, executor.restart_with_prompt(prompt))
        except Exception as e:
            logger.exception("Claude Code restart error in session %s", session.id)
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": f"Claude Code error: {e}",
                    "code": "CLAUDE_CODE_ERROR",
                },
            )
            await self._end_claude_code_session(session)
            return

        # Detect unexpected exit (no result event received)
        if not executor.got_result:
            exit_code = executor.exit_code
            logger.warning(
                "Claude Code (restart) exited without result event (session=%s, exit_code=%s)",
                session.id,
                exit_code,
            )
            session.set_activity(ActivityState.IDLE)
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": (
                        f"Claude Code process exited unexpectedly (exit code: {exit_code}). "
                        "This may be caused by a timeout, out-of-memory condition, or internal error. "
                        "You can send another message to restart it."
                    ),
                    "code": "CLAUDE_CODE_UNEXPECTED_EXIT",
                },
            )
            await executor.stop_process()
            return

        await executor.stop_process()

        session.buffer.push_text(
            MessageType.AGENT_GROUP_END,
            {"session_id": session.id},
        )

    async def _read_claude_code_followup(
        self,
        session: ActiveSession,
        executor: ClaudeCodeExecutor,
    ) -> None:
        """Read follow-up events from a still-running Claude Code process."""
        try:
            await self._relay_claude_code_stream(session, executor.read_more_events())
        except Exception as e:
            logger.exception("Claude Code follow-up error in session %s", session.id)
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": f"Claude Code error: {e}",
                    "code": "CLAUDE_CODE_ERROR",
                },
            )
            await self._end_claude_code_session(session)
            return

        # Detect unexpected exit (no result event received)
        if not executor.got_result:
            exit_code = executor.exit_code
            logger.warning(
                "Claude Code (follow-up) exited without result event (session=%s, exit_code=%s)",
                session.id,
                exit_code,
            )
            session.set_activity(ActivityState.IDLE)
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": (
                        f"Claude Code process exited unexpectedly (exit code: {exit_code}). "
                        "This may be caused by a timeout, out-of-memory condition, or internal error. "
                        "You can send another message to restart it."
                    ),
                    "code": "CLAUDE_CODE_UNEXPECTED_EXIT",
                },
            )
            await executor.stop_process()
            return

        await executor.stop_process()

        session.buffer.push_text(
            MessageType.AGENT_GROUP_END,
            {"session_id": session.id},
        )

    def _fire_log_task(
        self,
        *,
        session_id: str,
        usage: TurnUsage,
        has_tool_calls: bool,
        request_messages: list[dict[str, Any]],
        response_text: str | None,
    ) -> None:
        """Schedule a fire-and-forget background task to log an LLM call to the database."""
        if self._db_session_factory is None:
            return
        task = asyncio.create_task(
            self._log_llm_call(
                session_id=session_id,
                usage=usage,
                has_tool_calls=has_tool_calls,
                request_messages=request_messages,
                response_text=response_text,
            )
        )
        self._pending_log_tasks.add(task)
        task.add_done_callback(self._pending_log_tasks.discard)

    async def _log_llm_call(
        self,
        *,
        session_id: str,
        usage: TurnUsage,
        has_tool_calls: bool,
        request_messages: list[dict[str, Any]],
        response_text: str | None,
    ) -> None:
        """Write a single LLM call record to the database. Never raises."""
        assert self._db_session_factory is not None
        try:
            async with self._db_session_factory() as db:
                row = LLMCall(
                    session_id=uuid.UUID(session_id),
                    message_id=usage.message_id,
                    model=usage.model,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_creation_input_tokens=usage.cache_creation_input_tokens,
                    cache_read_input_tokens=usage.cache_read_input_tokens,
                    started_at=usage.started_at,
                    ended_at=usage.ended_at,
                    stop_reason=usage.stop_reason,
                    has_tool_calls=has_tool_calls,
                    request_messages=request_messages,
                    response_text=response_text,
                    service_tier=usage.service_tier,
                    inference_geo=usage.inference_geo,
                )
                db.add(row)
                await db.commit()
                logger.debug("Logged LLM call %s for session %s", usage.message_id, session_id)
        except Exception:
            logger.exception("Failed to log LLM call for session %s", session_id)

    def _fire_archive_task(self, session_id: str) -> None:
        """Schedule a fire-and-forget background task to archive a session to the database."""
        # Snapshot permission rules into metadata before archiving
        session = self._session_manager.get_session(session_id)
        if session is not None and session.permission_manager is not None:
            session.metadata["permission_rules"] = session.permission_manager.get_rules_snapshot()

        if self._db_session_factory is None:
            return
        task = asyncio.create_task(self._archive_session(session_id))
        self._pending_archive_tasks.add(task)
        task.add_done_callback(self._pending_archive_tasks.discard)

    async def _archive_session(self, session_id: str) -> None:
        """Archive a completed session to the database. Never raises."""
        assert self._db_session_factory is not None
        try:
            async with self._db_session_factory() as db:
                await self._session_manager.archive_session(session_id, db)
        except Exception:
            logger.exception("Failed to archive session %s", session_id)

    # --- Inactivity reaper ---

    _INACTIVITY_TIMEOUT = timedelta(hours=6)
    _REAPER_CHECK_INTERVAL = 600  # seconds (10 minutes)

    async def run_inactivity_reaper(self) -> None:
        """Periodically end sessions that have been inactive for too long."""
        try:
            while True:
                await asyncio.sleep(self._REAPER_CHECK_INTERVAL)
                await self._reap_inactive_sessions()
        except asyncio.CancelledError:
            pass

    async def _reap_inactive_sessions(self) -> None:
        """End all active sessions that exceed the inactivity timeout."""
        now = datetime.now(UTC)
        terminal = (SessionStatus.COMPLETED, SessionStatus.FAILED, SessionStatus.CANCELLED)
        for session in self._session_manager.list_all_sessions():
            if session.status in terminal:
                continue
            if session.status == SessionStatus.PAUSED:
                continue  # Never reap paused sessions
            if now - session.last_activity_at > self._INACTIVITY_TIMEOUT:
                logger.info(
                    "Auto-ending inactive session %s (last activity: %s)",
                    session.id,
                    session.last_activity_at.isoformat(),
                )
                with contextlib.suppress(ValueError, RuntimeError):
                    await self.end_session(session.id)
