"""Codex CLI agent methods for PromptRouter.

Extracted from prompt_router.py to reduce file size. These methods handle
starting, streaming, forwarding, restarting, and ending Codex CLI
subprocess sessions.

Used as a mixin class — ``PromptRouter`` inherits from
``CodexAgentMixin`` to gain these methods.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.buffer import MessageType
from src.core.session import ActivityState, SessionStatus, SessionType
from src.executors.codex import CodexExecutor

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from src.core.llm import ToolCallRequest
    from src.core.session import ActiveSession
    from src.executors.base import ExecutionChunk
    from src.tools.loader import ToolDefinition

logger = logging.getLogger(__name__)

_MAX_TOOL_OUTPUT_CHARS = 100_000


def _truncate_tool_output(content: str) -> str:
    """Truncate tool output that exceeds the size limit for client delivery."""
    if len(content) > _MAX_TOOL_OUTPUT_CHARS:
        return content[:_MAX_TOOL_OUTPUT_CHARS] + f"\n\n... (truncated, {len(content):,} total chars)"
    return content


class CodexAgentMixin:
    """Mixin providing Codex CLI agent lifecycle methods for PromptRouter."""

    def _build_codex_extra_env(self) -> dict[str, str]:
        """Build extra environment variables for Codex CLI subprocesses."""
        extra_env: dict[str, str] = {}

        # Check if the tool has its own provider configured — if so,
        # inject env vars from the per-tool settings instead of the
        # global CODEX_API_KEY.  Unlike Claude Code (which natively reads
        # settings.json via CLAUDE_CONFIG_DIR), Codex CLI only reads API
        # keys from actual environment variables, so we must inject them.
        tool_settings: dict[str, Any] = {}
        if self._tool_settings:  # ty:ignore[unresolved-attribute]
            tool_settings = self._tool_settings.get_settings("codex")  # ty:ignore[unresolved-attribute]

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
        elif self._settings and self._settings.CODEX_API_KEY:  # ty:ignore[unresolved-attribute]
            extra_env["CODEX_API_KEY"] = self._settings.CODEX_API_KEY  # ty:ignore[unresolved-attribute]

        if self._tool_settings:  # ty:ignore[unresolved-attribute]
            config_dir = self._tool_settings.get_config_dir("codex")  # ty:ignore[unresolved-attribute]
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
                "ChatGPT auth selected but ~/.codex/auth.json not found. Run 'codex login' first to authenticate."
            )
            return

        try:
            target_auth.symlink_to(default_auth)
            logger.info("Symlinked %s -> %s", target_auth, default_auth)
        except OSError:
            logger.warning("Failed to symlink auth.json", exc_info=True)

    async def _start_codex(
        self,
        session: ActiveSession,
        tool_def: ToolDefinition,
        tool_call: ToolCallRequest,
    ) -> str:
        """Start a Codex CLI session: spawn subprocess, begin background streaming."""
        working_dir = tool_call.tool_input.get("working_directory", ".")
        # Override with the session's selected worktree path when explicitly set.
        selected_wt = session.metadata.get("selected_worktree_path")
        if selected_wt:
            working_dir = selected_wt
        working_path = self._resolve_working_directory(working_dir)  # ty:ignore[unresolved-attribute]
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

        executor = self._get_executor(tool_def.executor, tool_def)  # ty:ignore[unresolved-attribute]
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

        # Record transient subprocess tracking fields and broadcast initial status
        session.subprocess_started_at = datetime.now(UTC)
        session.subprocess_current_tool = None
        session.subprocess_type = "codex"
        session.subprocess_display_name = tool_def.display_name or "Codex"
        session.subprocess_working_directory = str(working_path)
        session.buffer.push_ephemeral(
            MessageType.SUBPROCESS_STATUS,
            {
                "session_id": session.id,
                "subprocess_type": "codex",
                "display_name": session.subprocess_display_name,
                "working_directory": session.subprocess_working_directory,
                "current_tool": None,
                "started_at": session.subprocess_started_at.isoformat(),
            },
        )

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
                    session.subprocess_current_tool = "command_execution"
                    if session.subprocess_started_at is not None:
                        session.buffer.push_ephemeral(
                            MessageType.SUBPROCESS_STATUS,
                            {
                                "session_id": session.id,
                                "subprocess_type": session.subprocess_type,
                                "display_name": session.subprocess_display_name,
                                "working_directory": session.subprocess_working_directory,
                                "current_tool": "command_execution",
                                "started_at": session.subprocess_started_at.isoformat(),
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
                    session.subprocess_current_tool = "file_change"
                    if session.subprocess_started_at is not None:
                        session.buffer.push_ephemeral(
                            MessageType.SUBPROCESS_STATUS,
                            {
                                "session_id": session.id,
                                "subprocess_type": session.subprocess_type,
                                "display_name": session.subprocess_display_name,
                                "working_directory": session.subprocess_working_directory,
                                "current_tool": "file_change",
                                "started_at": session.subprocess_started_at.isoformat(),
                            },
                        )
                elif item_type == "mcp_tool_call":
                    post_tool_text_chunks.clear()
                    mcp_tool_name = f"mcp:{item.get('server', '')}:{item.get('tool', '')}"
                    session.buffer.push_text(
                        MessageType.TOOL_START,
                        {
                            "session_id": session.id,
                            "tool_name": mcp_tool_name,
                            "tool_input": item.get("arguments", {}),
                        },
                    )
                    session.subprocess_current_tool = mcp_tool_name
                    if session.subprocess_started_at is not None:
                        session.buffer.push_ephemeral(
                            MessageType.SUBPROCESS_STATUS,
                            {
                                "session_id": session.id,
                                "subprocess_type": session.subprocess_type,
                                "display_name": session.subprocess_display_name,
                                "working_directory": session.subprocess_working_directory,
                                "current_tool": mcp_tool_name,
                                "started_at": session.subprocess_started_at.isoformat(),
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
                    # Scan the complete agent message text for artifacts
                    if full_text:
                        self._fire_text_artifact_scan(session, [full_text])  # ty:ignore[unresolved-attribute]
                elif item_type == "command_execution":
                    output = _truncate_tool_output(item.get("aggregated_output", ""))
                    exit_code = item.get("exit_code")
                    if output:
                        session.buffer.push_text(
                            MessageType.TOOL_OUTPUT,
                            {
                                "session_id": session.id,
                                "tool_name": "command_execution",
                                "content": output,
                                "stream": "stdout",
                                "is_error": exit_code is not None and exit_code != 0,
                            },
                        )
                        self._fire_text_artifact_scan(session, [output])  # ty:ignore[unresolved-attribute]
                    session.subprocess_current_tool = None
                    if session.subprocess_started_at is not None:
                        session.buffer.push_ephemeral(
                            MessageType.SUBPROCESS_STATUS,
                            {
                                "session_id": session.id,
                                "subprocess_type": session.subprocess_type,
                                "display_name": session.subprocess_display_name,
                                "working_directory": session.subprocess_working_directory,
                                "current_tool": None,
                                "started_at": session.subprocess_started_at.isoformat(),
                            },
                        )
                elif item_type == "file_change":
                    diff = item.get("diff", "")
                    file_path = item.get("file_path", item.get("file", ""))
                    content = _truncate_tool_output(diff) if diff else f"File changed: {file_path}" if file_path else ""
                    if content:
                        session.buffer.push_text(
                            MessageType.TOOL_OUTPUT,
                            {
                                "session_id": session.id,
                                "tool_name": "file_change",
                                "content": content,
                                "stream": "stdout",
                            },
                        )
                        self._fire_text_artifact_scan(session, [content])  # ty:ignore[unresolved-attribute]
                    session.subprocess_current_tool = None
                    if session.subprocess_started_at is not None:
                        session.buffer.push_ephemeral(
                            MessageType.SUBPROCESS_STATUS,
                            {
                                "session_id": session.id,
                                "subprocess_type": session.subprocess_type,
                                "display_name": session.subprocess_display_name,
                                "working_directory": session.subprocess_working_directory,
                                "current_tool": None,
                                "started_at": session.subprocess_started_at.isoformat(),
                            },
                        )
                elif item_type == "mcp_tool_call":
                    mcp_completed_name = f"mcp:{item.get('server', '')}:{item.get('tool', '')}"
                    output = item.get("output", item.get("result", ""))
                    if isinstance(output, dict):
                        output = json.dumps(output, indent=2)
                    output = _truncate_tool_output(str(output)) if output else ""
                    if output:
                        session.buffer.push_text(
                            MessageType.TOOL_OUTPUT,
                            {
                                "session_id": session.id,
                                "tool_name": mcp_completed_name,
                                "content": output,
                                "stream": "stdout",
                            },
                        )
                        self._fire_text_artifact_scan(session, [output])  # ty:ignore[unresolved-attribute]
                    session.subprocess_current_tool = None
                    if session.subprocess_started_at is not None:
                        session.buffer.push_ephemeral(
                            MessageType.SUBPROCESS_STATUS,
                            {
                                "session_id": session.id,
                                "subprocess_type": session.subprocess_type,
                                "display_name": session.subprocess_display_name,
                                "working_directory": session.subprocess_working_directory,
                                "current_tool": None,
                                "started_at": session.subprocess_started_at.isoformat(),
                            },
                        )

            elif event_type == "turn.completed":
                session.set_activity(ActivityState.IDLE)
                # Extract token usage from Codex turn
                codex_usage = event.get("usage") or {}
                codex_in = codex_usage.get("input_tokens") or 0
                codex_out = codex_usage.get("output_tokens") or 0
                if codex_in or codex_out:
                    session.tool_input_tokens += codex_in
                    session.tool_output_tokens += codex_out
                    if session._on_update:
                        session._on_update()
                summary_text = "".join(post_tool_text_chunks).strip() or "Codex task completed"
                self._fire_summary_task(session, summary_text)  # ty:ignore[unresolved-attribute]
                self._fire_task_update_task(session, summary_text)  # ty:ignore[unresolved-attribute]

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
        self.schedule_pending_drain(session)  # ty:ignore[unresolved-attribute]

    async def _end_codex_session(self, session: ActiveSession) -> None:
        """Clean up Codex state when the session ends."""
        if session.codex_executor is not None:
            await session.codex_executor.stop_process()
        session.codex_executor = None
        session._codex_stream_task = None

        # Clear subprocess tracking and broadcast null status
        session.clear_subprocess_tracking()

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
        self._fire_archive_task(session.id)  # ty:ignore[unresolved-attribute]

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

        # Re-broadcast subprocess status so the client shows the indicator again
        if session.subprocess_started_at is None:
            session.subprocess_started_at = datetime.now(UTC)
            session.subprocess_type = "codex"
            codex_def_for_name = self._tool_registry.get("codex")  # ty:ignore[unresolved-attribute]
            session.subprocess_display_name = (
                codex_def_for_name.display_name if codex_def_for_name and codex_def_for_name.display_name else "Codex"
            )
            session.subprocess_working_directory = session.metadata.get("codex_working_directory", "")
        session.subprocess_current_tool = None
        session.buffer.push_ephemeral(
            MessageType.SUBPROCESS_STATUS,
            {
                "session_id": session.id,
                "subprocess_type": session.subprocess_type,
                "display_name": session.subprocess_display_name,
                "working_directory": session.subprocess_working_directory,
                "current_tool": None,
                "started_at": session.subprocess_started_at.isoformat(),
            },
        )

        # Open a new agent group for this follow-up turn
        codex_def = self._tool_registry.get("codex")  # ty:ignore[unresolved-attribute]
        session.buffer.push_text(
            MessageType.AGENT_GROUP_START,
            {
                "session_id": session.id,
                "tool_name": "codex",
                "display_name": codex_def.display_name if codex_def and codex_def.display_name else "Codex",
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
        self.schedule_pending_drain(session)  # ty:ignore[unresolved-attribute]
