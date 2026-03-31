"""OpenCode CLI agent methods for PromptRouter.

Extracted from prompt_router.py to reduce file size. These methods handle
starting, streaming, forwarding, restarting, and ending OpenCode CLI
subprocess sessions.

Used as a mixin class — ``PromptRouter`` inherits from
``OpenCodeAgentMixin`` to gain these methods.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from src.core.buffer import MessageType
from src.core.session import ActivityState, SessionStatus, SessionType
from src.executors.opencode import OpenCodeExecutor

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


class OpenCodeAgentMixin:
    """Mixin providing OpenCode CLI agent lifecycle methods for PromptRouter."""

    def _build_opencode_extra_env(self) -> dict[str, str]:
        """Build extra environment variables for OpenCode CLI subprocesses."""
        extra_env: dict[str, str] = {}

        tool_settings: dict[str, Any] = {}
        if self._tool_settings:
            tool_settings = self._tool_settings.get_settings("opencode")

        tool_provider = tool_settings.get("provider", "")

        if tool_provider:
            tool_env = tool_settings.get("env", {})
            if isinstance(tool_env, dict):
                extra_env.update(tool_env)

        if self._tool_settings:
            config_dir = self._tool_settings.get_config_dir("opencode")
            config_dir.mkdir(parents=True, exist_ok=True)
            extra_env["OPENCODE_HOME"] = str(config_dir)

        return extra_env

    async def _start_opencode(
        self,
        session: ActiveSession,
        tool_def: ToolDefinition,
        tool_call: ToolCallRequest,
    ) -> str:
        """Start an OpenCode CLI session: spawn subprocess, begin background streaming."""
        working_dir = tool_call.tool_input.get("working_directory", ".")
        selected_wt = session.metadata.get("selected_worktree_path")
        if selected_wt:
            working_dir = selected_wt
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

        tool_call.tool_input["working_directory"] = str(working_path)

        executor = self._get_executor(tool_def.executor, tool_def)
        assert isinstance(executor, OpenCodeExecutor)

        session.opencode_executor = executor
        session.session_type = SessionType.LONG_RUNNING
        session.set_activity(ActivityState.RUNNING_SUBPROCESS)

        session.metadata["opencode_working_directory"] = str(working_path)
        session.metadata["opencode_tool_name"] = tool_def.name
        session.metadata["opencode_parameters"] = tool_call.tool_input

        task = asyncio.create_task(self._stream_opencode_events(session, executor, tool_def, tool_call))
        session._opencode_stream_task = task

        session.subprocess_started_at = datetime.now(UTC)
        session.subprocess_current_tool = None
        session.subprocess_type = "opencode"
        session.subprocess_display_name = tool_def.display_name or "OpenCode"
        session.subprocess_working_directory = str(working_path)
        session.buffer.push_ephemeral(
            MessageType.SUBPROCESS_STATUS,
            {
                "session_id": session.id,
                "subprocess_type": "opencode",
                "display_name": session.subprocess_display_name,
                "working_directory": session.subprocess_working_directory,
                "current_tool": None,
                "started_at": session.subprocess_started_at.isoformat(),
            },
        )

        return f"OpenCode session started in {working_path}"

    async def _relay_opencode_stream(
        self,
        session: ActiveSession,
        stream: AsyncGenerator[ExecutionChunk, None],
    ) -> bool:
        """Parse OpenCode CLI JSONL events and push structured buffer messages.

        Translates OpenCode event types into the same message types used by
        the RCFlow LLM pipeline.

        Returns True if the stream ended with a successful ``step_finish``
        (reason == "stop"), False if it ended with an error or unexpectedly.
        """
        post_tool_text_chunks: list[str] = []
        _completed_successfully = False

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

            # Persist session_id once we receive it (sessionID is camelCase in opencode ≥1.3)
            if event_type == "step_start":
                part = event.get("part") or {}
                sid = event.get("sessionID") or part.get("sessionID")
                if sid and session.opencode_executor:
                    session.metadata["opencode_session_id"] = sid

            elif event_type == "text":
                # Assistant text block (full, non-streaming)
                part = event.get("part") or {}
                text = part.get("text", "")
                if text:
                    post_tool_text_chunks.append(text)
                    session.buffer.push_text(
                        MessageType.TEXT_CHUNK,
                        {
                            "session_id": session.id,
                            "content": text,
                            "finished": False,
                        },
                    )
                    self._fire_text_artifact_scan(session, [text])

            elif event_type == "tool_use":
                # Single event carries both tool invocation and result
                part = event.get("part") or {}
                tool_name = part.get("tool", "unknown")
                state = part.get("state") or {}
                tool_input = state.get("input") or {}
                tool_status = state.get("status", "")

                post_tool_text_chunks.clear()
                session.buffer.push_text(
                    MessageType.TOOL_START,
                    {
                        "session_id": session.id,
                        "tool_name": tool_name,
                        "tool_input": tool_input,
                    },
                )
                session.subprocess_current_tool = tool_name
                if session.subprocess_started_at is not None:
                    session.buffer.push_ephemeral(
                        MessageType.SUBPROCESS_STATUS,
                        {
                            "session_id": session.id,
                            "subprocess_type": session.subprocess_type,
                            "display_name": session.subprocess_display_name,
                            "working_directory": session.subprocess_working_directory,
                            "current_tool": tool_name,
                            "started_at": session.subprocess_started_at.isoformat(),
                        },
                    )

                if tool_status == "completed":
                    output = state.get("output") or ""
                    if isinstance(output, dict):
                        output = json.dumps(output, indent=2)
                    output = _truncate_tool_output(str(output)) if output else ""
                    if output:
                        session.buffer.push_text(
                            MessageType.TOOL_OUTPUT,
                            {
                                "session_id": session.id,
                                "tool_name": tool_name,
                                "content": output,
                                "stream": "stdout",
                            },
                        )
                        self._fire_text_artifact_scan(session, [output])
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

            elif event_type == "step_finish":
                # Accumulate per-step token usage
                part = event.get("part") or {}
                tokens = part.get("tokens") or {}
                oc_in = tokens.get("input") or 0
                oc_out = tokens.get("output") or 0
                if oc_in or oc_out:
                    session.tool_input_tokens += oc_in
                    session.tool_output_tokens += oc_out
                    if session._on_update:
                        session._on_update()

                # Final step (reason "stop") — mark idle and fire summary
                if part.get("reason") == "stop":
                    _completed_successfully = True
                    session.set_activity(ActivityState.IDLE)
                    summary_text = "".join(post_tool_text_chunks).strip() or "OpenCode task completed"
                    self._fire_summary_task(session, summary_text, push_session_end_ask=True)
                    self._fire_task_update_task(session, summary_text)

            elif event_type in ("error", "session.error"):
                error = event.get("error") or {}
                if isinstance(error, str):
                    error_msg = error
                else:
                    # opencode wraps the message in data.message for UnknownError/APIError
                    error_msg = (
                        error.get("message")
                        or (error.get("data") or {}).get("message")
                        or "OpenCode error"
                    )
                logger.warning(
                    "OpenCode error event (session=%s): %s", session.id, error_msg
                )
                session.buffer.push_text(
                    MessageType.ERROR,
                    {
                        "session_id": session.id,
                        "content": error_msg,
                        "code": "OPENCODE_ERROR",
                    },
                )

            else:
                logger.debug("Skipping unknown OpenCode event type: %s", event_type)

        return _completed_successfully

    async def _stream_opencode_events(
        self,
        session: ActiveSession,
        executor: OpenCodeExecutor,
        tool_def: ToolDefinition,
        tool_call: ToolCallRequest,
    ) -> None:
        """Background task: read OpenCode CLI events and push to session buffer."""
        try:
            completed = await self._relay_opencode_stream(
                session, executor.execute_streaming(tool_def, tool_call.tool_input)
            )
        except Exception as e:
            logger.exception("OpenCode streaming error in session %s", session.id)
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": f"OpenCode error: {e}",
                    "code": "OPENCODE_ERROR",
                },
            )
            await self._end_opencode_session(session)
            return

        session.buffer.push_text(
            MessageType.AGENT_GROUP_END,
            {"session_id": session.id},
        )

        if not completed:
            # Stream ended without a successful step_finish (error or crash).
            # End the session so the user is not left with a broken subprocess.
            logger.info(
                "OpenCode stream ended without completion (session=%s), ending session",
                session.id,
            )
            await self._end_opencode_session(session)
            return

        await executor.stop_process()

        logger.info(
            "OpenCode initial streaming finished (session=%s)",
            session.id,
        )

    async def _end_opencode_session(self, session: ActiveSession) -> None:
        """Clean up OpenCode state when the session ends."""
        if session.opencode_executor is not None:
            await session.opencode_executor.stop_process()
        session.opencode_executor = None
        session._opencode_stream_task = None

        session.clear_subprocess_tracking()

        if session.status == SessionStatus.PAUSED:
            session.complete()
            return

        session.buffer.push_text(
            MessageType.SESSION_END,
            {
                "session_id": session.id,
                "reason": "opencode_finished",
            },
        )
        session.complete()
        self._fire_archive_task(session.id)

    async def _forward_to_opencode(self, session: ActiveSession, text: str) -> None:
        """Forward a follow-up message to the active OpenCode session.

        OpenCode CLI uses one-shot processes, so follow-ups always spawn a new
        process with ``--session-id SESSION_ID``.
        """
        executor = session.opencode_executor
        if executor is None:
            return

        if session.status == SessionStatus.PAUSED:
            return

        session.set_activity(ActivityState.RUNNING_SUBPROCESS)

        if session.subprocess_started_at is None:
            session.subprocess_started_at = datetime.now(UTC)
            session.subprocess_type = "opencode"
            opencode_def_for_name = self._tool_registry.get("opencode")
            session.subprocess_display_name = (
                opencode_def_for_name.display_name
                if opencode_def_for_name and opencode_def_for_name.display_name
                else "OpenCode"
            )
            session.subprocess_working_directory = session.metadata.get("opencode_working_directory", "")
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

        opencode_def = self._tool_registry.get("opencode")
        session.buffer.push_text(
            MessageType.AGENT_GROUP_START,
            {
                "session_id": session.id,
                "tool_name": "opencode",
                "display_name": opencode_def.display_name if opencode_def and opencode_def.display_name else "OpenCode",
            },
        )

        session._opencode_stream_task = asyncio.create_task(
            self._restart_opencode_with_prompt(session, executor, text)
        )

    async def _restart_opencode_with_prompt(
        self,
        session: ActiveSession,
        executor: OpenCodeExecutor,
        prompt: str,
    ) -> None:
        """Spawn a new OpenCode resume process and stream events for a follow-up."""
        try:
            completed = await self._relay_opencode_stream(session, executor.restart_with_prompt(prompt))
        except Exception as e:
            logger.exception("OpenCode restart error in session %s", session.id)
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            session.buffer.push_text(
                MessageType.ERROR,
                {
                    "session_id": session.id,
                    "content": f"OpenCode error: {e}",
                    "code": "OPENCODE_ERROR",
                },
            )
            await self._end_opencode_session(session)
            return

        session.buffer.push_text(
            MessageType.AGENT_GROUP_END,
            {"session_id": session.id},
        )

        if not completed:
            logger.info(
                "OpenCode restart stream ended without completion (session=%s), ending session",
                session.id,
            )
            await self._end_opencode_session(session)
            return

        await executor.stop_process()
