"""Claude Code agent methods for PromptRouter.

Extracted from prompt_router.py to reduce file size. These methods handle
starting, streaming, forwarding, restarting, and ending Claude Code
subprocess sessions.

Used as a mixin class — ``PromptRouter`` inherits from
``ClaudeCodeAgentMixin`` to gain these methods.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.core.buffer import MessageType
from src.core.permissions import (
    PermissionDecision,
    PermissionManager,
    classify_risk,
    describe_tool_action,
    get_scope_options,
)
from src.core.session import ActivityState, SessionStatus, SessionType
from src.executors.claude_code import ClaudeCodeExecutor

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


class ClaudeCodeAgentMixin:
    """Mixin providing Claude Code agent lifecycle methods for PromptRouter."""

    def _build_claude_code_extra_env(self) -> dict[str, str]:
        """Build extra environment variables for Claude Code subprocesses."""
        extra_env: dict[str, str] = {}

        # Check if the tool has its own provider configured — if so, skip
        # injecting the global ANTHROPIC_API_KEY so the settings.json env
        # section takes precedence.
        tool_provider = ""
        if self._tool_settings:  # ty:ignore[unresolved-attribute]
            tool_provider = self._tool_settings.get_settings("claude_code").get("provider", "")  # ty:ignore[unresolved-attribute]

        if tool_provider == "anthropic_login":
            # Anthropic Login uses OAuth tokens from .credentials.json —
            # ensure no ANTHROPIC_API_KEY leaks from the server process env,
            # which would override OAuth and cause "Invalid API key" errors.
            extra_env["ANTHROPIC_API_KEY"] = ""
        elif not tool_provider and self._settings and self._settings.ANTHROPIC_API_KEY:  # ty:ignore[unresolved-attribute]
            extra_env["ANTHROPIC_API_KEY"] = self._settings.ANTHROPIC_API_KEY  # ty:ignore[unresolved-attribute]

        if self._tool_settings:  # ty:ignore[unresolved-attribute]
            config_dir = self._tool_settings.get_config_dir("claude_code")  # ty:ignore[unresolved-attribute]
            config_dir.mkdir(parents=True, exist_ok=True)
            extra_env["CLAUDE_CONFIG_DIR"] = str(config_dir)

        # Ensure wt (bundled with RCFlow via wtpython) is on PATH so Claude Code
        # can call it. Fall back to adding the running Python's bin dir if wt is
        # not already resolvable via the inherited environment.
        if not shutil.which("wt"):
            venv_bin = Path(sys.executable).parent
            if (venv_bin / "wt").exists():
                current_path = os.environ.get("PATH", "")
                extra_env["PATH"] = f"{venv_bin}:{current_path}"

        # Signal to Claude Code that it is running under RCFlow orchestration
        # when the user has opted into undercover mode via tool settings.
        if self._tool_settings and self._tool_settings.get_settings("claude_code").get("undercover", False):  # ty:ignore[unresolved-attribute]
            extra_env["CLAUDE_CODE_UNDERCOVER"] = "1"

        return extra_env

    async def _start_claude_code(
        self,
        session: ActiveSession,
        tool_def: ToolDefinition,
        tool_call: ToolCallRequest,
    ) -> str:
        """Start a Claude Code session: spawn subprocess, begin background streaming."""
        working_dir = tool_call.tool_input.get("working_directory", ".")
        # Priority 1: explicit worktree selection overrides everything.
        # Priority 2: session project (from picker) used when no worktree is set.
        # Priority 3: the LLM-chosen working_directory (default above).
        selected_wt = session.metadata.get("selected_worktree_path")
        if selected_wt:
            working_dir = selected_wt
        elif session.main_project_path:
            working_dir = session.main_project_path
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
        assert isinstance(executor, ClaudeCodeExecutor)

        session.claude_code_executor = executor
        session.session_type = SessionType.LONG_RUNNING
        session.set_activity(ActivityState.RUNNING_SUBPROCESS)

        # Enable interactive permissions if configured
        effective_config = {**tool_def.executor_config.get("claude_code", {})}
        for k, v in self._get_managed_config_overrides("claude_code").items():  # ty:ignore[unresolved-attribute]
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

        # Record transient subprocess tracking fields and broadcast initial status
        session.subprocess_started_at = datetime.now(UTC)
        session.subprocess_current_tool = None
        session.subprocess_type = "claude_code"
        session.subprocess_display_name = tool_def.display_name or "Claude Code"
        session.subprocess_working_directory = str(working_path)
        session.buffer.push_ephemeral(
            MessageType.SUBPROCESS_STATUS,
            {
                "session_id": session.id,
                "subprocess_type": "claude_code",
                "display_name": session.subprocess_display_name,
                "working_directory": session.subprocess_working_directory,
                "current_tool": None,
                "started_at": session.subprocess_started_at.isoformat(),
            },
        )

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
                scan_texts: list[str] = []
                for block in message.get("content", []):
                    block_type = block.get("type")
                    if block_type == "text":
                        text_val = block["text"]
                        session.buffer.push_text(
                            MessageType.TEXT_CHUNK,
                            {
                                "session_id": session.id,
                                "content": text_val,
                                "finished": False,
                            },
                        )
                        scan_texts.append(text_val)
                    elif block_type == "thinking":
                        thinking_text = block.get("thinking", "")
                        if thinking_text:
                            session.buffer.push_text(
                                MessageType.THINKING,
                                {
                                    "session_id": session.id,
                                    "content": thinking_text,
                                },
                            )
                    elif block_type == "tool_use":
                        tool_name = block.get("name", "unknown")
                        tool_input = block.get("input", {})

                        # Permission check for interactive sessions
                        if session.permission_manager is not None:
                            decision = await self._handle_permission_check(session, tool_name, tool_input)
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

                        if tool_name == "TodoWrite":
                            todos = tool_input.get("todos", [])
                            session.update_todos(todos)
                            session.buffer.push_text(
                                MessageType.TODO_UPDATE,
                                {
                                    "session_id": session.id,
                                    "todos": todos,
                                },
                            )
                        elif tool_name == "EnterPlanMode":
                            session._plan_mode_event = asyncio.Event()
                            session._plan_mode_approved = False
                            session.set_activity(ActivityState.AWAITING_PERMISSION)
                            session.buffer.push_text(
                                MessageType.PLAN_MODE_ASK,
                                {"session_id": session.id},
                            )
                            # Block stream reading until the user approves or denies.
                            # While we wait, Claude Code's stdout pipe fills and it
                            # cannot advance further, effectively gating the session.
                            await session._plan_mode_event.wait()
                            session._plan_mode_event = None
                            session.set_activity(ActivityState.RUNNING_SUBPROCESS)
                            if not session._plan_mode_approved:
                                # User denied plan mode — terminate cleanly.
                                session.buffer.push_text(
                                    MessageType.AGENT_GROUP_END,
                                    {"session_id": session.id},
                                )
                                session.buffer.push_text(
                                    MessageType.ERROR,
                                    {
                                        "session_id": session.id,
                                        "content": "Plan mode denied. The session was stopped.",
                                        "code": "PLAN_MODE_DENIED",
                                    },
                                )
                                await self._end_claude_code_session(session)
                                return
                        elif tool_name == "ExitPlanMode":
                            session._plan_review_event = asyncio.Event()
                            session._plan_review_approved = False
                            session._plan_review_feedback = None
                            session.set_activity(ActivityState.AWAITING_PERMISSION)
                            session.buffer.push_text(
                                MessageType.PLAN_REVIEW_ASK,
                                {"session_id": session.id, "plan_input": tool_input},
                            )
                            # Block stream reading until the user approves or provides
                            # feedback. Claude Code is waiting for stdin after ExitPlanMode,
                            # so the pipe is also effectively gated.
                            await session._plan_review_event.wait()
                            session._plan_review_event = None
                            session.set_activity(ActivityState.RUNNING_SUBPROCESS)
                            response_text = session._plan_review_feedback or ""
                            session._plan_review_feedback = None
                            # Forward user's response to Claude Code stdin:
                            # approval text → CC proceeds; feedback → CC revises the plan.
                            if session.claude_code_executor is not None and session.claude_code_executor.is_running:
                                await session.claude_code_executor.send_input(response_text)
                        else:
                            session.buffer.push_text(
                                MessageType.TOOL_START,
                                {
                                    "session_id": session.id,
                                    "tool_name": tool_name,
                                    "tool_input": tool_input,
                                },
                            )
                            # Update subprocess current_tool tracking
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
                        # Collect tool input values for scanning
                        for v in tool_input.values():
                            if isinstance(v, str):
                                scan_texts.append(v)
                # Fire artifact scan for this assistant message
                if scan_texts:
                    self._fire_text_artifact_scan(session, scan_texts)  # ty:ignore[unresolved-attribute]

            elif event_type == "tool_result":
                raw_content = event.get("content", "")
                if isinstance(raw_content, list):
                    # Content blocks format — extract text parts only
                    parts = []
                    for block in raw_content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            parts.append(block.get("text", ""))
                    content = "\n".join(parts)
                else:
                    content = str(raw_content)

                content = _truncate_tool_output(content)
                if content:
                    is_error = event.get("is_error", False)
                    session.buffer.push_text(
                        MessageType.TOOL_OUTPUT,
                        {
                            "session_id": session.id,
                            "content": content,
                            "is_error": is_error,
                        },
                    )
                    # Fire artifact scan for this tool result
                    self._fire_text_artifact_scan(session, [content])  # ty:ignore[unresolved-attribute]

                # Clear current_tool after the result
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

            elif event_type == "result":
                session.set_activity(ActivityState.IDLE)
                result_text = event.get("result", "")
                result_subtype = event.get("subtype", "")
                # Extract cost and token data from Claude Code result
                cost_usd = event.get("cost_usd") or 0.0
                if cost_usd:
                    session.tool_cost_usd += float(cost_usd)
                cc_usage = event.get("usage") or {}
                cc_in = cc_usage.get("input_tokens") or 0
                cc_out = cc_usage.get("output_tokens") or 0
                if cc_in or cc_out:
                    session.tool_input_tokens += cc_in
                    session.tool_output_tokens += cc_out
                if (cost_usd or cc_in or cc_out) and session._on_update:
                    session._on_update()

                if result_subtype == "max_turns":
                    # Claude Code hit --max-turns limit; pause the session so the
                    # user sees a distinctive "turn limit reached" indicator and can
                    # resume to continue rather than being asked to end the session.
                    summary_text = result_text or "Claude Code reached the maximum number of turns for this invocation."
                    # Push AGENT_GROUP_END here so the agent block is closed before
                    # the SESSION_PAUSED message appears in the stream.
                    session.buffer.push_text(
                        MessageType.AGENT_GROUP_END,
                        {"session_id": session.id},
                    )
                    session.pause(reason="max_turns")
                    session.buffer.push_text(
                        MessageType.SESSION_PAUSED,
                        {
                            "session_id": session.id,
                            "paused_at": session.paused_at.isoformat() if session.paused_at else None,
                            "reason": "max_turns",
                            "claude_code_interrupted": False,
                        },
                    )
                    self._fire_summary_task(session, summary_text)  # ty:ignore[unresolved-attribute]  # No SESSION_END_ASK
                    self._fire_task_update_task(session, summary_text)  # ty:ignore[unresolved-attribute]
                elif result_text:
                    self._fire_summary_task(session, result_text, push_session_end_ask=True)  # ty:ignore[unresolved-attribute]
                    self._fire_task_update_task(session, result_text)  # ty:ignore[unresolved-attribute]
                else:
                    # Result event with no text and no subtype — still notify the user
                    session.buffer.push_text(
                        MessageType.SESSION_END_ASK,
                        {"session_id": session.id},
                    )

            elif event_type == "system":
                # Claude Code may emit system events with usage data
                subtype = event.get("subtype", "")
                if subtype == "usage":
                    sys_usage = event.get("usage") or {}
                    sys_in = sys_usage.get("input_tokens") or 0
                    sys_out = sys_usage.get("output_tokens") or 0
                    if sys_in or sys_out:
                        session.tool_input_tokens += sys_in
                        session.tool_output_tokens += sys_out
                        if session._on_update:
                            session._on_update()

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

        # If the relay ended the session itself (e.g. plan mode denied), it already
        # pushed AGENT_GROUP_END/SESSION_END and called _end_claude_code_session —
        # nothing left to do here.
        if session.status in (SessionStatus.COMPLETED, SessionStatus.CANCELLED, SessionStatus.FAILED):
            return

        # If the session was paused during relay (e.g. max_turns), the relay already
        # pushed AGENT_GROUP_END and SESSION_PAUSED — just clean up and return.
        if session.status == SessionStatus.PAUSED:
            if session.claude_code_executor is not None:
                await session.claude_code_executor.stop_process()
            session.claude_code_executor = None
            session._claude_code_stream_task = None
            logger.info("Session %s paused after stream (reason=%s)", session.id, session.paused_reason)
            return

        # Detect unexpected exit (no result event received)
        if not executor.got_result:
            exit_code = executor.exit_code
            session.set_activity(ActivityState.IDLE)
            session.clear_subprocess_tracking()
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            if exit_code == 0:
                # Clean exit with no result event — treat as normal completion.
                logger.info(
                    "Claude Code exited cleanly without result event (session=%s)",
                    session.id,
                )
                session.buffer.push_text(
                    MessageType.SESSION_END_ASK,
                    {"session_id": session.id},
                )
            else:
                logger.warning(
                    "Claude Code exited without result event (session=%s, exit_code=%s)",
                    session.id,
                    exit_code,
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

        session.buffer.push_text(
            MessageType.AGENT_GROUP_END,
            {"session_id": session.id},
        )

        logger.info(
            "Claude Code initial streaming finished, process kept alive (session=%s)",
            session.id,
        )

    async def _end_claude_code_session(self, session: ActiveSession) -> None:
        """Clean up Claude Code state when the session ends."""
        if session.claude_code_executor is not None:
            await session.claude_code_executor.stop_process()
        session.claude_code_executor = None
        session._claude_code_stream_task = None

        # Clear subprocess tracking and broadcast null status
        session.clear_subprocess_tracking()

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
        self._fire_archive_task(session.id)  # ty:ignore[unresolved-attribute]

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

        # Re-broadcast subprocess status so the client shows the indicator again
        if session.subprocess_started_at is None:
            session.subprocess_started_at = datetime.now(UTC)
            session.subprocess_type = "claude_code"
            cc_def_for_name = self._tool_registry.get("claude_code")  # ty:ignore[unresolved-attribute]
            session.subprocess_display_name = (
                cc_def_for_name.display_name if cc_def_for_name and cc_def_for_name.display_name else "Claude Code"
            )
            session.subprocess_working_directory = session.metadata.get("claude_code_working_directory", "")
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
        cc_def = self._tool_registry.get("claude_code")  # ty:ignore[unresolved-attribute]
        session.buffer.push_text(
            MessageType.AGENT_GROUP_START,
            {
                "session_id": session.id,
                "tool_name": "claude_code",
                "display_name": cc_def.display_name if cc_def and cc_def.display_name else "Claude Code",
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

            # Ensure any previous stream task is stopped before starting a new one
            if session._claude_code_stream_task is not None and not session._claude_code_stream_task.done():
                session._claude_code_stream_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await session._claude_code_stream_task

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

        # If the relay ended the session itself (e.g. plan mode denied), already done.
        if session.status in (SessionStatus.COMPLETED, SessionStatus.CANCELLED, SessionStatus.FAILED):
            return

        # If the session was paused during relay (e.g. max_turns), relay already
        # pushed AGENT_GROUP_END and SESSION_PAUSED — just clean up and return.
        if session.status == SessionStatus.PAUSED:
            if session.claude_code_executor is not None:
                await session.claude_code_executor.stop_process()
            session.claude_code_executor = None
            session._claude_code_stream_task = None
            logger.info("Session %s paused after restart stream (reason=%s)", session.id, session.paused_reason)
            return

        # Detect unexpected exit (no result event received)
        if not executor.got_result:
            exit_code = executor.exit_code
            session.set_activity(ActivityState.IDLE)
            session.clear_subprocess_tracking()
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            if exit_code == 0:
                logger.info(
                    "Claude Code (restart) exited cleanly without result event (session=%s)",
                    session.id,
                )
                session.buffer.push_text(
                    MessageType.SESSION_END_ASK,
                    {"session_id": session.id},
                )
            else:
                logger.warning(
                    "Claude Code (restart) exited without result event (session=%s, exit_code=%s)",
                    session.id,
                    exit_code,
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
            return

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

        # If the relay ended the session itself (e.g. plan mode denied), already done.
        if session.status in (SessionStatus.COMPLETED, SessionStatus.CANCELLED, SessionStatus.FAILED):
            return

        # If the session was paused during relay (e.g. max_turns), relay already
        # pushed AGENT_GROUP_END and SESSION_PAUSED — just clean up and return.
        if session.status == SessionStatus.PAUSED:
            if session.claude_code_executor is not None:
                await session.claude_code_executor.stop_process()
            session.claude_code_executor = None
            session._claude_code_stream_task = None
            logger.info("Session %s paused after follow-up stream (reason=%s)", session.id, session.paused_reason)
            return

        # Detect unexpected exit (no result event received)
        if not executor.got_result:
            exit_code = executor.exit_code
            session.set_activity(ActivityState.IDLE)
            session.clear_subprocess_tracking()
            session.buffer.push_text(
                MessageType.AGENT_GROUP_END,
                {"session_id": session.id},
            )
            if exit_code == 0:
                logger.info(
                    "Claude Code (follow-up) exited cleanly without result event (session=%s)",
                    session.id,
                )
                session.buffer.push_text(
                    MessageType.SESSION_END_ASK,
                    {"session_id": session.id},
                )
            else:
                logger.warning(
                    "Claude Code (follow-up) exited without result event (session=%s, exit_code=%s)",
                    session.id,
                    exit_code,
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
            return

        session.buffer.push_text(
            MessageType.AGENT_GROUP_END,
            {"session_id": session.id},
        )
